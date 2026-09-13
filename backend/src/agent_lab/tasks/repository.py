"""公共受理与状态存储；每个方法只做数据库工作，不在事务里发布消息或执行业务。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import exists, select, text, update
from sqlalchemy.orm import aliased

from agent_lab.models.scheduled_job import (
    JobRunRecord, ScheduledJobRecord, TaskPolicyRecord, TaskPolicyChangeRecord, TaskRequestRecord,
)
from agent_lab.tasks.contracts import (
    ACTIVE_STATUSES, DELIVERABLE_STATUSES, TERMINAL_STATUSES, ExecutionPolicy,
    RequestConflict, TaskDetailsExpired, TaskError, TaskNotFound, TaskOverlap,
)


@dataclass(frozen=True)
class Acceptance:
    run_id: UUID
    status: str
    job_id: UUID | None = None
    details_expired: bool = False


def receipt(run):
    return Acceptance(run.id, run.status, run.source_job_id)


async def transaction_lock(session, identity: str):
    """稳定事务锁只缩小竞争窗口；唯一约束仍是最终受理保护。"""
    key = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


class TaskRepository:
    """调用者管理事务，可将业务待办与本次受理一并提交。"""

    def __init__(self, session):
        self.session = session

    async def replay(self, *, actor, operation, request_key, content):
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        await transaction_lock(self.session, json.dumps([actor, operation, request_key]))
        previous = await self.session.get(TaskRequestRecord, (actor, operation, request_key))
        if previous is None:
            return digest, None
        if previous.request_digest != digest:
            raise RequestConflict(run_id=previous.run_id)
        run = await self.session.get(JobRunRecord, previous.run_id)
        return digest, receipt(run) if run else Acceptance(previous.run_id, "expired", details_expired=True)

    async def policy(self, *, lock=False):
        statement = select(TaskPolicyRecord).where(TaskPolicyRecord.id == 1)
        if lock:
            statement = statement.with_for_update(read=True)
        record = await self.session.scalar(statement)
        if record is None:
            raise RuntimeError("任务默认策略尚未迁移。")
        return ExecutionPolicy.model_validate(record.policy)

    async def active(self, *, source_job_id=None, concurrency_key=None):
        criterion = JobRunRecord.source_job_id == source_job_id if source_job_id else JobRunRecord.concurrency_key == concurrency_key
        return await self.session.scalar(select(JobRunRecord).where(
            criterion, JobRunRecord.status.in_(ACTIVE_STATUSES),
        ).order_by(JobRunRecord.accepted_at).limit(1))

    async def accept(self, *, spec, params, actor, operation, request_key, digest, now,
                     job=None, source_job_id=None, trigger_type="direct", scheduled_for=None,
                     retry_of=None, concurrency_key=None, config_snapshot=None, skipped=False):
        """保存执行及原请求回执，不提交；周期推进和业务待办可加入同一事务。"""
        policy = await self.policy(lock=True)
        snapshot = config_snapshot or {"task_type": spec.task_type, "task_version": spec.version, "params": params}
        if job is not None:
            source_job_id = job.id
            snapshot = {**snapshot, "key": job.key, "cron_expr": job.cron_expr, "config_version": job.config_version}
        run = JobRunRecord(
            id=uuid4(), job_id=job.id if job else None, source_job_id=source_job_id,
            task_type=spec.task_type, task_version=spec.version, actor=actor,
            trigger_type=trigger_type, concurrency_key=concurrency_key, retry_of=retry_of,
            status="skipped" if skipped else "queued", accepted_at=now, scheduled_for=scheduled_for,
            available_at=now, dispatch_after=now, attempts=0, delivery_generation=1, delivery_count=0,
            config_snapshot=snapshot, policy_snapshot=policy.model_dump(), recovery={},
            stats={"reason": "previous_run_still_running"} if skipped else {},
            finished_at=now if skipped else None,
            expires_at=now + timedelta(days=policy.history_retention_days) if skipped else None,
        )
        self.session.add(run)
        self.session.add(TaskRequestRecord(
            actor=actor, operation=operation, request_key=request_key, request_digest=digest,
            run_id=run.id, accepted_at=now,
        ))
        await self.session.flush()
        return run

    async def require_run(self, run_id, *, lock=False):
        statement = select(JobRunRecord).where(JobRunRecord.id == run_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        run = await self.session.scalar(statement)
        if run is not None:
            return run
        if await self.session.scalar(select(TaskRequestRecord.run_id).where(TaskRequestRecord.run_id == run_id).limit(1)):
            raise TaskDetailsExpired(run_id=run_id)
        raise TaskNotFound(run_id=run_id)

    async def list_runs(self, *, limit=50, offset=0, source_job_id=None, status=None, task_type=None):
        statement = select(JobRunRecord)
        if source_job_id:
            statement = statement.where(JobRunRecord.source_job_id == source_job_id)
        if status:
            statement = statement.where(JobRunRecord.status == status)
        if task_type:
            statement = statement.where(JobRunRecord.task_type == task_type)
        return list((await self.session.scalars(statement.order_by(
            JobRunRecord.accepted_at.desc(), JobRunRecord.id.desc(),
        ).limit(limit).offset(offset))).all())

    async def cancel(self, run_id, now, *, discard_preparation=None):
        run = await self.require_run(run_id, lock=True)
        if run.status == "cancelled":
            return run
        if run.status not in DELIVERABLE_STATUSES:
            raise TaskError(run_id=run_id)
        if discard_preparation is not None:
            await discard_preparation(self.session, run)
        run.status, run.finished_at = "cancelled", now
        run.expires_at = now + timedelta(days=ExecutionPolicy.model_validate(run.policy_snapshot).history_retention_days)
        run.delivery_generation += 1
        run.claim_token = None
        run.wait_reason = None
        return run

    async def update_policy(self, policy, actor, now):
        record = await self.session.scalar(select(TaskPolicyRecord).where(TaskPolicyRecord.id == 1).with_for_update())
        if record is None:
            raise RuntimeError("任务默认策略尚未迁移。")
        current = policy.model_dump()
        if record.policy != current:
            self.session.add(TaskPolicyChangeRecord(actor=actor, changed_at=now, previous=record.policy, current=current))
            record.policy, record.updated_at, record.updated_by = current, now, actor
        return record


class TaskStore:
    """Beat 和 Worker 使用短会话；领取与开始分开，使资源等待仍可取消。"""

    def __init__(self, sessions, *, clock=None):
        self.sessions = sessions
        self.clock = clock or (lambda: datetime.now(UTC))

    async def claim(self, run_id, generation, owner):
        now, token = self.clock(), uuid4()
        async with self.sessions() as session:
            result = await session.scalar(update(JobRunRecord).where(
                JobRunRecord.id == run_id, JobRunRecord.delivery_generation == generation,
                JobRunRecord.status.in_(DELIVERABLE_STATUSES), JobRunRecord.claim_token.is_(None),
                JobRunRecord.available_at <= now,
            ).values(claim_token=token, owner=owner, heartbeat_at=now).returning(JobRunRecord))
            await session.commit()
            return result

    async def start(self, run_id, token):
        now = self.clock()
        async with self.sessions() as session:
            result = await session.execute(update(JobRunRecord).where(
                JobRunRecord.id == run_id, JobRunRecord.claim_token == token,
                JobRunRecord.status.in_(DELIVERABLE_STATUSES),
            ).values(status="running", started_at=now, heartbeat_at=now, wait_reason=None,
                     attempts=JobRunRecord.attempts + 1))
            await session.commit()
            return result.rowcount == 1

    async def heartbeat(self, run_id, token):
        async with self.sessions() as session:
            result = await session.execute(update(JobRunRecord).where(
                JobRunRecord.id == run_id, JobRunRecord.claim_token == token,
                JobRunRecord.status.in_(ACTIVE_STATUSES),
            ).values(heartbeat_at=self.clock()))
            await session.commit()
            return result.rowcount == 1

    async def finish(self, run_id, token, *, status, stats, error_type=None, wait_reason=None,
                     delay_seconds=0, recovery=None, on_success=None):
        """只收尾当前领取代次；成功回调与本批终态在同一事务保存。"""
        now = self.clock()
        async with self.sessions() as session:
            run = await session.scalar(select(JobRunRecord).where(JobRunRecord.id == run_id).with_for_update())
            if run is None or run.claim_token != token:
                return False
            if run.status in TERMINAL_STATUSES:
                return run.status == status
            run.status, run.stats, run.error_type = status, stats, error_type
            run.wait_reason = wait_reason
            if recovery is not None:
                run.recovery = recovery
            if status in TERMINAL_STATUSES:
                run.finished_at = now
                run.expires_at = now + timedelta(days=ExecutionPolicy.model_validate(run.policy_snapshot).history_retention_days)
            elif status in DELIVERABLE_STATUSES:
                run.available_at = now + timedelta(seconds=delay_seconds)
                run.dispatch_after = run.available_at
                run.claim_token = None
                run.delivery_generation += 1
            await session.flush()
            if status == "succeeded" and on_success is not None:
                await on_success(session, run)
            await session.commit()
            return True

    async def reserve_deliveries(self, *, limit=100, retry_seconds=5, run_id=None):
        now = self.clock()
        async with self.sessions() as session:
            statement = select(JobRunRecord).where(
                JobRunRecord.status.in_(DELIVERABLE_STATUSES), JobRunRecord.claim_token.is_(None),
                JobRunRecord.available_at <= now, JobRunRecord.dispatch_after <= now,
            )
            if run_id:
                statement = statement.where(JobRunRecord.id == run_id)
            rows = list((await session.scalars(statement.order_by(JobRunRecord.available_at).limit(limit).with_for_update(skip_locked=True))).all())
            messages = []
            for run in rows:
                run.dispatch_after = now + timedelta(seconds=retry_seconds)
                run.delivery_count += 1
                messages.append((run.id, run.delivery_generation))
            await session.commit()
            return messages

    async def record_delivery(self, run_id, generation, error_type=None):
        async with self.sessions() as session:
            values = {"dispatch_error_type": error_type}
            if error_type is None:
                values["last_dispatched_at"] = self.clock()
            await session.execute(update(JobRunRecord).where(
                JobRunRecord.id == run_id, JobRunRecord.delivery_generation == generation,
            ).values(**values))
            await session.commit()

    async def stale_claims(self, *, stale_seconds=30):
        async with self.sessions() as session:
            return list((await session.scalars(select(JobRunRecord).where(
                JobRunRecord.status.in_(ACTIVE_STATUSES), JobRunRecord.status != "needs_attention",
                JobRunRecord.claim_token.is_not(None), JobRunRecord.heartbeat_at < self.clock() - timedelta(seconds=stale_seconds),
            ).limit(100))).all())

    async def recover_claim(self, run, decision, *, discard_preparation=None, on_success=None):
        """恢复前再次核对心跳，不能把刚恢复联系的执行者判作失联。"""
        async with self.sessions() as session:
            current = await session.scalar(select(JobRunRecord).where(
                JobRunRecord.id == run.id, JobRunRecord.claim_token == run.claim_token,
                JobRunRecord.heartbeat_at == run.heartbeat_at,
                JobRunRecord.status.in_(ACTIVE_STATUSES), JobRunRecord.status != "needs_attention",
            ).with_for_update())
            if current is None:
                return
            policy = ExecutionPolicy.model_validate(current.policy_snapshot)
            if current.status in DELIVERABLE_STATUSES:
                # 行锁保护下清理准备资源；旧 token 不能在清理之后创建占用或通过 start。
                if discard_preparation is not None:
                    await discard_preparation(session, current)
                current.dispatch_after = self.clock()
            elif decision.action == "retry":
                current.recovery = {"confirmed_safe": True, "reason": decision.reason}
                if current.attempts <= policy.max_retries:
                    current.status = "retry_wait"
                    current.available_at = self.clock() + timedelta(seconds=policy.delay(current.attempts))
                    current.dispatch_after = current.available_at
                else:
                    current.status, current.finished_at = "failed", self.clock()
                    current.error_type = "RecoveryRetriesExhausted"
                    current.wait_reason = "已确认可以安全恢复，但本次自动重试额度已耗尽。"
                    current.stats = {**decision.stats, "error_reason": "retries_exhausted"}
                    current.expires_at = current.finished_at + timedelta(days=policy.history_retention_days)
            elif decision.action == "succeeded":
                current.status, current.stats, current.finished_at = "succeeded", decision.stats, self.clock()
                current.expires_at = current.finished_at + timedelta(days=policy.history_retention_days)
            else:
                current.status = "needs_attention"
                current.wait_reason = decision.reason
                current.recovery = {"reason": "owner_unconfirmed", "steps": decision.reason}
            # 恢复结论生效后，迟到的心跳或结果不能覆盖新的状态。
            current.claim_token = None
            current.delivery_generation += 1
            await session.flush()
            if current.status == "succeeded" and on_success is not None:
                await on_success(session, current)
            await session.commit()

    async def prune_history(self, *, limit=100):
        """原请求回执和业务待办不在删除范围，仍被重试关联的失败记录受保护。"""
        child = aliased(JobRunRecord)
        async with self.sessions() as session:
            rows = list((await session.scalars(select(JobRunRecord).where(
                JobRunRecord.status.in_(TERMINAL_STATUSES), JobRunRecord.expires_at <= self.clock(),
                ~exists().where(child.retry_of == JobRunRecord.id),
            ).order_by(JobRunRecord.expires_at).limit(limit).with_for_update(skip_locked=True))).all())
            for run in rows:
                await session.delete(run)
            await session.commit()
            return len(rows)
