"""单个 Celery Beat 的 PostgreSQL 调度适配；没有第二份静态周期配置。"""

import asyncio
from datetime import timedelta
import logging

from celery.beat import Scheduler
from sqlalchemy import select

from agent_lab.config.scheduler import get_scheduler_settings
from agent_lab.config.task_queue import get_task_queue_settings
from agent_lab.models.scheduled_job import ScheduledJobRecord
from agent_lab.tasks.cron import CronSchedule
from agent_lab.tasks.process import close_process_runtime, get_process_runtime
from agent_lab.tasks.status import write_status

logger = logging.getLogger(__name__)


class BeatController:
    def __init__(self, runtime, *, cron, maintenance_seconds=5):
        self.runtime, self.cron = runtime, cron
        self.maintenance_seconds = maintenance_seconds
        self._last_maintenance = None
        self._maintenance_task = None

    async def start(self):
        now = self.runtime.store.clock()
        async with self.runtime.store.sessions() as session:
            jobs = list((await session.scalars(select(ScheduledJobRecord).where(ScheduledJobRecord.enabled).with_for_update())).all())
            for job in jobs:
                job.next_run_at = self.cron.next_after(job.cron_expr, now)
            await session.commit()
        # 启动重置只影响尚未受理的周期；已有执行仍由补投循环推进。
        self._last_maintenance = None

    async def tick(self):
        now = self.runtime.store.clock()
        async with self.runtime.store.sessions() as session:
            jobs = list((await session.scalars(select(ScheduledJobRecord).where(
                ScheduledJobRecord.enabled, ScheduledJobRecord.next_run_at <= now,
            ))).all())
        for job in jobs:
            await self.runtime.service.scheduled(job.id, job.config_version, job.next_run_at, self.cron)
        if (self._maintenance_task is None or self._maintenance_task.done()) and (
            self._last_maintenance is None or now - self._last_maintenance >= timedelta(seconds=self.maintenance_seconds)
        ):
            # 发布 Redis 可能超时，不能因此拖住下一分钟的周期受理。
            self._maintenance_task = asyncio.create_task(self._maintain())
            self._last_maintenance = now
        write_status(ready=True, jobs=len(jobs))

    async def _maintain(self):
        try:
            await self.runtime.worker.recover_stalled()
            await self.runtime.store.prune_history()
            await self.runtime.dispatcher.publish_due()
        except Exception as error:
            logger.error("Beat 维护失败 error_type=%s", type(error).__name__)

    async def close(self):
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            await asyncio.gather(self._maintenance_task, return_exceptions=True)


class PostgresScheduler(Scheduler):
    """tick 的返回值控制 Beat 休眠；lazy 实例不建立事件循环或连接。"""

    def setup_schedule(self):
        self._controller = None

    def tick(self, **kwargs):
        settings = get_task_queue_settings()
        runtime = get_process_runtime()
        try:
            if getattr(self, "_controller", None) is None:
                controller = BeatController(runtime, cron=CronSchedule(get_scheduler_settings().timezone),
                                            maintenance_seconds=settings.maintenance_seconds)
                runtime.run(controller.start())
                self._controller = controller
            runtime.run(self._controller.tick())
        except Exception as error:
            logger.error("Beat 推进失败 error_type=%s", type(error).__name__)
            write_status(ready=False, jobs=0)
        return settings.beat_poll_seconds

    def close(self):
        try:
            write_status(ready=False, jobs=0)
        finally:
            try:
                controller = getattr(self, "_controller", None)
                if controller is not None:
                    controller.runtime.run(controller.close())
            finally:
                close_process_runtime()
