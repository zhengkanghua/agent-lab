"""周期、人工和业务提交共用的受理入口；返回前提交 PostgreSQL，之后才尝试投递。"""

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select

from agent_lab.models.scheduled_job import ScheduledJobRecord, TaskPolicyChangeRecord
from agent_lab.services.scheduled_task_errors import ScheduledJobInvalidParamsError, ScheduledJobNotFoundError
from agent_lab.tasks.contracts import RetryUnavailable, TaskOverlap
from agent_lab.tasks.repository import TaskRepository, receipt, transaction_lock


class TaskService:
    def __init__(self, sessions, registry, *, dispatcher=None, clock=None):
        self.sessions, self.registry = sessions, registry
        self.dispatcher = dispatcher
        self.clock = clock or (lambda: datetime.now(UTC))

    async def _publish(self, accepted):
        if self.dispatcher is not None and not accepted.details_expired:
            await self.dispatcher.publish_due(run_id=accepted.run_id)
        return accepted

    def _params(self, spec, params):
        try:
            return spec.validate_params(params)
        except (ValidationError, ValueError):
            raise ScheduledJobInvalidParamsError() from None

    async def submit(self, task_type, params, *, actor, request_key, operation=None, content=None):
        operation = operation or f"task:{task_type}:submit"
        content = content if content is not None else {"task_type": task_type, "params": params}
        async with self.sessions() as session:
            repository = TaskRepository(session)
            digest, accepted = await repository.replay(actor=actor, operation=operation, request_key=request_key, content=content)
            if accepted is None:
                spec = self.registry.require(task_type)
                params = self._params(spec, params)
                if spec.concurrency_key:
                    await transaction_lock(session, spec.concurrency_key)
                    active = await repository.active(concurrency_key=spec.concurrency_key)
                    if active:
                        raise TaskOverlap(run_id=active.id)
                run = await repository.accept(
                    spec=spec, params=params, actor=actor, operation=operation,
                    request_key=request_key, digest=digest, now=self.clock(),
                    concurrency_key=spec.concurrency_key,
                )
                accepted = receipt(run)
            await session.commit()
        return await self._publish(accepted)

    async def trigger(self, job_id, *, actor, request_key):
        operation = f"scheduled-job:{job_id}:trigger"
        async with self.sessions() as session:
            repository = TaskRepository(session)
            digest, accepted = await repository.replay(actor=actor, operation=operation, request_key=request_key, content={})
            if accepted is None:
                job = await session.scalar(select(ScheduledJobRecord).where(ScheduledJobRecord.id == job_id).with_for_update())
                if job is None:
                    raise ScheduledJobNotFoundError()
                active = await repository.active(source_job_id=job_id)
                if active:
                    raise TaskOverlap(run_id=active.id)
                spec = self.registry.require(job.task_type)
                accepted = receipt(await repository.accept(
                    spec=spec, params=self._params(spec, job.params), actor=actor, operation=operation,
                    request_key=request_key, digest=digest, now=self.clock(), job=job, trigger_type="manual",
                ))
            await session.commit()
        return await self._publish(accepted)

    async def scheduled(self, job_id, version, planned_for, cron):
        """与配置编辑共用行锁；周期事件身份不包含配置版本。"""
        now, operation = self.clock(), f"scheduled-job:{job_id}:scheduled"
        key = planned_for.astimezone(UTC).isoformat()
        async with self.sessions() as session:
            repository = TaskRepository(session)
            digest, accepted = await repository.replay(actor="system:beat", operation=operation, request_key=key, content={})
            job = await session.scalar(select(ScheduledJobRecord).where(ScheduledJobRecord.id == job_id).with_for_update())
            if job is None or not job.enabled or job.config_version != version or job.next_run_at != planned_for:
                return None
            job.next_run_at = cron.next_after(job.cron_expr, now)
            # 保持旧调度器的一秒正常误差，超出即只推进未来计划，不补历史。
            if accepted is None and timedelta(0) <= now - planned_for <= timedelta(seconds=1):
                spec = self.registry.require(job.task_type)
                active = await repository.active(source_job_id=job_id)
                accepted = receipt(await repository.accept(
                    spec=spec, params=self._params(spec, job.params), actor="system:beat", operation=operation,
                    request_key=key, digest=digest, now=now, job=job, trigger_type="scheduled",
                    scheduled_for=planned_for, skipped=active is not None,
                ))
            await session.commit()
        # Beat 的独立维护任务负责发布，慢 Redis 不阻塞本轮其他周期受理。
        return accepted

    async def retry(self, run_id, *, actor, request_key):
        operation = f"task-run:{run_id}:retry"
        async with self.sessions() as session:
            repository = TaskRepository(session)
            digest, accepted = await repository.replay(actor=actor, operation=operation, request_key=request_key, content={})
            if accepted is None:
                original = await repository.require_run(run_id)
                # 完成回调先锁执行再锁业务键；明显不允许重试时不要反向持锁等待它。
                if original.status != "failed":
                    raise RetryUnavailable(run_id=run_id)
                job = None
                if original.source_job_id:
                    job = await session.scalar(select(ScheduledJobRecord).where(ScheduledJobRecord.id == original.source_job_id).with_for_update())
                    await transaction_lock(session, f"retry-source:{original.source_job_id}")
                if original.concurrency_key:
                    await transaction_lock(session, original.concurrency_key)
                original = await repository.require_run(run_id, lock=True)
                if original.status != "failed" or not isinstance(original.config_snapshot.get("params"), dict):
                    raise RetryUnavailable(run_id=run_id)
                if original.source_job_id or original.concurrency_key:
                    active = await repository.active(source_job_id=original.source_job_id, concurrency_key=original.concurrency_key)
                    if active:
                        raise TaskOverlap(run_id=active.id)
                spec = self.registry.require(original.task_type, original.task_version)
                run = await repository.accept(
                    spec=spec, params=original.config_snapshot["params"], actor=actor, operation=operation,
                    request_key=request_key, digest=digest, now=self.clock(),
                    source_job_id=original.source_job_id, trigger_type="retry", retry_of=run_id,
                    concurrency_key=original.concurrency_key, config_snapshot=dict(original.config_snapshot),
                )
                run.job_id = job.id if job else None
                accepted = receipt(run)
            await session.commit()
        return await self._publish(accepted)

    async def cancel(self, run_id):
        async with self.sessions() as session:
            repository = TaskRepository(session)
            run = await repository.require_run(run_id, lock=True)
            spec = self.registry.get(run.task_type)
            run = await repository.cancel(run_id, self.clock(),
                discard_preparation=spec.discard_preparation if spec else None)
            await session.commit()
            return run

    async def get_run(self, run_id):
        async with self.sessions() as session:
            return await TaskRepository(session).require_run(run_id)

    async def list_runs(self, **filters):
        async with self.sessions() as session:
            return await TaskRepository(session).list_runs(**filters)

    async def get_policy(self):
        async with self.sessions() as session:
            return await TaskRepository(session).policy()

    async def update_policy(self, policy, actor):
        async with self.sessions() as session:
            await TaskRepository(session).update_policy(policy, actor, self.clock())
            await session.commit()
            return policy

    async def policy_history(self, *, limit=20):
        async with self.sessions() as session:
            return list((await session.scalars(select(TaskPolicyChangeRecord).order_by(
                TaskPolicyChangeRecord.changed_at.desc(), TaskPolicyChangeRecord.id.desc(),
            ).limit(limit))).all())
