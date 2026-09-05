"""cron 与 API 共用的受理和收尾流程；任务业务由静态定义绑定。"""

import asyncio
import logging
from datetime import UTC, datetime

from agent_lab.domain.write_scope import WriteRecoveryRequiredError
from agent_lab.services.execution_cleanup import finish_cleanup
from agent_lab.services.scheduled_task_errors import ScheduledJobClosingError, ScheduledJobUnknownTypeError
from agent_lab.services.scheduled_task_registry import get_task_type_spec
from agent_lab.services.write_coordination import OWNER, current_run_id

logger = logging.getLogger(__name__)


class ScheduledJobExecutor:
    def __init__(self, *, store_factory, runtime_factory, settings, clock=None):
        self._stores = store_factory
        self._runtimes = runtime_factory
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tasks: set[asyncio.Task] = set()
        self._closing = False
        self._accept_lock = asyncio.Lock()

    async def submit(self, job_id, trigger_type, expected_version=None):
        # 关闭必须等正在提交的认领完成，才能取得完整的后台执行集合。
        async with self._accept_lock:
            if self._closing:
                raise ScheduledJobClosingError()
            claimed = await self._stores().claim_run(
                job_id, trigger_type=trigger_type, started_at=self._clock(), owner=OWNER,
                expected_version=expected_version,
            )
            if claimed is None:
                return None
            job, run_id = claimed
            task = asyncio.create_task(self.execute(job, run_id, trigger_type))
            self._tasks.add(task)
            task.add_done_callback(self._done)
            return run_id

    def _done(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("任务执行收尾失败 error_type=%s", type(task.exception()).__name__)

    async def close(self):
        async with self._accept_lock:
            self._closing = True
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks, timeout=self._settings.shutdown_grace_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def execute(self, job, run_id, trigger_type):
        store = self._stores()
        runtime = None
        stats = {}
        error_type = None
        status = "succeeded"
        phase = "validation"
        token = current_run_id.set(run_id)
        heartbeat = asyncio.create_task(self._heartbeat(store, run_id, asyncio.current_task()))
        try:
            spec = get_task_type_spec(job.task_type)
            if spec is None:
                raise ScheduledJobUnknownTypeError()
            params = spec.validate_params(job.params)
            phase = "runtime"
            runtime = self._runtimes()
            phase = "business"
            stats = await spec.execute(runtime, params)
        except asyncio.CancelledError:
            status, error_type = "failed", "CancelledError"
            stats.update(error_reason="execution_interrupted", failure_phase=phase, needs_attention=True)
        except Exception as exc:
            status, error_type = "failed", type(exc).__name__
            if isinstance(exc, WriteRecoveryRequiredError):
                stats.update(exc.stats)
                stats["needs_attention"] = True
            stats.update(failure_phase=phase)
            reason = getattr(exc, "reason", None)
            if isinstance(reason, str) and reason.replace("_", "").isalnum() and len(reason) <= 80:
                stats["error_reason"] = reason
        finally:
            try:
                await finish_cleanup(self._finish(
                    store, runtime, heartbeat, job, run_id, trigger_type, status, stats, error_type,
                ))
            finally:
                current_run_id.reset(token)

    async def _finish(self, store, runtime, heartbeat, job, run_id, trigger_type, status, stats, error_type):
        # 业务已经停止，先停心跳，避免诊断失败再取消资源关闭。
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            if runtime is not None:
                await runtime.close()
        except Exception as exc:
            stats["resource_close_error"] = type(exc).__name__
            logger.error("任务资源关闭失败 run_id=%s error_type=%s", run_id, type(exc).__name__)
        try:
            await store.finish_run(
                run_id, status=status, stats=stats, error_type=error_type, finished_at=self._clock(),
            )
            logger.info("任务执行结束 job_id=%s run_id=%s trigger=%s status=%s error_type=%s", job.id, run_id, trigger_type, status, error_type)
        except Exception as exc:
            logger.error("任务终态保存失败 job_id=%s run_id=%s business_status=%s error_type=%s", job.id, run_id, status, type(exc).__name__)
            # 只重试幂等的终态保存一次，绝不重新调用任务业务。
            try:
                await store.finish_run(run_id, status=status, stats=stats, error_type=error_type, finished_at=self._clock())
            except Exception as retry_error:
                logger.error("任务终态仍未确认 run_id=%s business_status=%s stats=%s error_type=%s", run_id, status, stats, type(retry_error).__name__)
        try:
            await store.prune_runs(job.id, keep=self._settings.run_history_retention)
        except Exception as exc:
            logger.error("任务历史裁剪失败 run_id=%s error_type=%s", run_id, type(exc).__name__)

    async def _heartbeat(self, store, run_id, task):
        try:
            while True:
                await asyncio.sleep(5)
                await store.heartbeat(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("任务执行心跳失败 run_id=%s error_type=%s", run_id, type(exc).__name__)
            task.cancel()
