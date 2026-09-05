"""维护 cron 与数据库配置刷新；业务受理和收尾交给统一任务执行组件。"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agent_lab.services.scheduled_job_executor import ScheduledJobExecutor
from agent_lab.services.scheduled_task_errors import ScheduledJobAlreadyRunningError
from agent_lab.services.scheduled_task_registry import get_task_type_spec

logger = logging.getLogger(__name__)
SKIPPED_PREVIOUS_RUNNING_REASON = "previous_run_still_running"


class ScheduledJobRunner:
    def __init__(self, *, store_factory, write_runtime_factory, settings, clock=None, status_writer=None):
        self._stores = store_factory
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tzinfo = ZoneInfo(settings.timezone)
        self._scheduler = None
        self._refresh_task = None
        self._event_tasks = set()
        self._versions = {}
        self._status_writer = status_writer
        self._executor = ScheduledJobExecutor(
            store_factory=store_factory, runtime_factory=write_runtime_factory, settings=settings, clock=self._clock,
        )

    @property
    def timezone(self):
        return self._settings.timezone

    async def start(self):
        if self._scheduler is not None:
            return
        self._scheduler = AsyncIOScheduler(timezone=self._tzinfo)
        self._scheduler.add_listener(self._on_scheduler_event, EVENT_JOB_MAX_INSTANCES | EVENT_JOB_MISSED)
        self._scheduler.start(paused=True)
        await self.refresh()
        self._scheduler.resume()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def refresh(self):
        jobs = await self._stores().load_enabled_jobs()
        ids = {job.id for job in jobs}
        for job_id in list(self._versions):
            if job_id not in ids:
                self.remove_job(job_id)
        for job in jobs:
            if self._versions.get(job.id) != job.config_version:
                self.apply_job(job)
        self._publish_status(ready=len(self._versions) == len(ids))

    def _publish_status(self, *, ready):
        if self._status_writer:
            try:
                self._status_writer(ready=ready, jobs=len(self._versions), max_age_seconds=max(30, self._settings.refresh_seconds * 3))
            except Exception as exc:
                # 就绪文件过期会让检查失败；诊断写入失败不得打断业务收尾。
                logger.error("调度就绪状态保存失败 error_type=%s", type(exc).__name__)

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(self._settings.refresh_seconds)
            try:
                await self.refresh()
            except Exception as exc:
                logger.error("调度配置刷新失败 error_type=%s", type(exc).__name__)
                self._publish_status(ready=False)

    async def close(self):
        self._publish_status(ready=False)
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
        if self._scheduler is not None:
            self._scheduler.pause()
        # 不依赖 cron 开关，API 接受的后台执行也必须收尾。
        await self._executor.close()
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        await asyncio.gather(*self._event_tasks, return_exceptions=True)

    def apply_job(self, job):
        if self._scheduler is None:
            return
        self.remove_job(job.id)
        if not job.enabled:
            return
        try:
            trigger = self.parse_cron(job.cron_expr)
            spec = get_task_type_spec(job.task_type)
            if spec is None:
                raise ValueError("未知任务类型")
            spec.validate_params(job.params)
        except ValueError:
            logger.error("调度配置无效 job_id=%s", job.id)
            return
        self._scheduler.add_job(
            self._run_scheduled, trigger=trigger, args=[job.id, job.config_version],
            id=str(job.id), name=job.key, coalesce=False, max_instances=1,
            # 仅允许调度器正常投递的秒级误差，不补历史触发。
            misfire_grace_time=1, replace_existing=True,
        )
        self._versions[job.id] = job.config_version

    def remove_job(self, job_id):
        self._versions.pop(job_id, None)
        if self._scheduler is not None:
            try:
                self._scheduler.remove_job(str(job_id))
            except JobLookupError:
                pass

    def next_run_at(self, job_id):
        scheduled = self._scheduler.get_job(str(job_id)) if self._scheduler else None
        return scheduled.next_run_time.astimezone(UTC) if scheduled and scheduled.next_run_time else None

    def planned_run_at(self, job):
        if not job.enabled:
            return None
        try:
            times, _ = self.upcoming_fire_times(job.cron_expr, count=1)
            return times[0] if times else None
        except ValueError:
            return None

    def parse_cron(self, cron_expr):
        return CronTrigger.from_crontab(cron_expr, timezone=self._tzinfo)

    def upcoming_fire_times(self, cron_expr, *, count=3):
        trigger = self.parse_cron(cron_expr)
        moments = []
        next_time = trigger.get_next_fire_time(None, self._clock() + timedelta(microseconds=1))
        while next_time is not None and len(moments) < count:
            moments.append(next_time.astimezone(UTC))
            next_time = trigger.get_next_fire_time(next_time, next_time + timedelta(seconds=1))
        return moments, [moment.astimezone(self._tzinfo).isoformat() for moment in moments]

    async def trigger_now(self, job):
        return await self._executor.submit(job.id, "manual")

    async def _run_scheduled(self, job_id, expected_version=None):
        try:
            await self._executor.submit(job_id, "scheduled", expected_version)
        except ScheduledJobAlreadyRunningError:
            try:
                await self._record_skipped(job_id, SKIPPED_PREVIOUS_RUNNING_REASON)
            except Exception as exc:
                # 不把数据库异常交给 APScheduler 默认 traceback 日志，避免暴露连接信息。
                logger.error("调度跳过记录失败 job_id=%s error_type=%s", job_id, type(exc).__name__)
        except Exception as exc:
            logger.error("定时触发未受理 job_id=%s error_type=%s", job_id, type(exc).__name__)

    async def _record_skipped(self, job_id, reason):
        store = self._stores()
        if await store.get_job(job_id) is not None:
            await store.record_skipped(job_id, trigger_type="scheduled", started_at=self._clock(), stats={"reason": reason})
            await store.prune_runs(job_id, keep=self._settings.run_history_retention)

    def _on_scheduler_event(self, event):
        from uuid import UUID
        reason = "missed_fire_time" if event.code == EVENT_JOB_MISSED else "trigger_still_pending"
        task = asyncio.create_task(self._record_skipped(UUID(event.job_id), reason))
        self._event_tasks.add(task)
        def finished(completed):
            self._event_tasks.discard(completed)
            if not completed.cancelled() and completed.exception():
                logger.error("调度跳过记录失败 error_type=%s", type(completed.exception()).__name__)
        task.add_done_callback(finished)
