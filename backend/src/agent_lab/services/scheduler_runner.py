"""进程内定时任务调度器：APScheduler 的包装器与统一执行入口。

本模块是定时任务模块的「引擎室」，职责有四：
1. **cron 装卸**：把数据库里的任务配置注册进 APScheduler（``AsyncIOScheduler`` +
   内存 job store），管理端增删改查后通过 ``apply_job`` / ``remove_job`` 实时同步；
2. **统一执行**：cron 到点（``_run_scheduled``）和管理端手动触发（``trigger_now``）
   走同一个 ``_execute`` 包装器——同样的参数重验、按次新建写 Runtime、只跑对应步骤、
   用完即关、写执行历史、裁剪保留条数；
3. **重叠治理**：每个任务一把进程内 ``asyncio.Lock``，上一轮没跑完时到点的触发记一条
   ``skipped`` 执行记录后直接放弃，不排队不堆积（运行策略见 ADR 0014）；
4. **cron 预览**：给管理 API 提供「未来 N 次执行时间」计算，用于提交前校验。

数据库（``ScheduledJobStore``）是任务配置的唯一事实来源；调度器只是「让到点这件事
发生」的执行机构，进程重启后由 ``start`` 从数据库重新加载，调度器本身不持久化任何东西。
本模块不实现 HTTP、不直接持有 Session，所有数据库访问经短会话 Store。
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agent_lab.config.scheduler import SchedulerSettings
from agent_lab.models.scheduled_job import ScheduledJobRecord
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.repositories.scheduled_job_repository import ScheduledJobStore
from agent_lab.services.scheduled_task_errors import (
    ScheduledJobAlreadyRunningError,
    ScheduledJobUnknownTypeError,
)
from agent_lab.services.scheduled_task_registry import get_task_type_spec


logger = logging.getLogger(__name__)

# 上一轮未结束时到点触发的统一跳过原因；写进 skipped 记录的 stats，管理端据此展示。
SKIPPED_PREVIOUS_RUNNING_REASON = "previous_run_still_running"

_SKIPPED_STATS: dict = {"reason": SKIPPED_PREVIOUS_RUNNING_REASON}

type SchedulerStoreFactory = Callable[[], ScheduledJobStore]
type SchedulerWriteRuntimeFactory = Callable[[], PipelineWriteRuntime]
type UtcClock = Callable[[], datetime]


def _aggregate_failures(error_types) -> dict[str, int]:
    """把一串失败异常类名聚合成「类型 → 数量」的稳定排序 dict。"""

    return dict(sorted(Counter(error_types).items()))


class ScheduledJobRunner:
    """包装 APScheduler，提供任务装卸、手动触发与统一执行包装。

    生命周期：``create_app`` 时构造（零 I/O，只存工厂）；``SCHEDULER_ENABLED`` 为真时
    由 lifespan 调 ``start``（启动 APScheduler 并从数据库加载启用任务），关闭时 ``close``。
    调度器未启动（开关关闭）时本对象仍然可用：任务装卸变成无害空操作、``next_run_at``
    恒为 None，管理 API 照常工作，手动触发也照常能执行。
    """

    def __init__(
        self,
        *,
        store_factory: SchedulerStoreFactory,
        write_runtime_factory: SchedulerWriteRuntimeFactory,
        settings: SchedulerSettings,
        clock: UtcClock | None = None,
    ) -> None:
        """绑定存储、写 Runtime 工厂与配置；不启动调度器、不执行 I/O。

        Args:
            store_factory: 每次调用返回一个短会话 Store；后台任务用它读写执行历史。
            write_runtime_factory: 每次执行新建写 Runtime 的工厂，与手动流水线同一来源。
            settings: 调度器配置（启停、时区、宽限、保留条数）。
            clock: 返回 UTC 时间的函数；测试可注入固定时钟。
        """

        self._store_factory = store_factory
        self._write_runtime_factory = write_runtime_factory
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._tzinfo = ZoneInfo(settings.timezone)
        self._scheduler: AsyncIOScheduler | None = None
        # 每个任务一把锁：上一轮没跑完就跳过本轮。任务数量有限，锁对象常驻可接受。
        self._locks: dict = {}
        # 持有手动触发派生的后台 Task 引用，防止被垃圾回收中途取消（asyncio 官方要求）。
        self._background_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        """启动 APScheduler 并从数据库加载启用中的任务（仅在总开关开启时被调用）。

        Raises:
            Exception: 数据库读取失败或 APScheduler 启动失败；由 lifespan 决定失败语义。
        """

        if self._scheduler is not None:
            return
        scheduler = AsyncIOScheduler(timezone=self._tzinfo)
        scheduler.start()
        self._scheduler = scheduler
        store = self._store_factory()
        for job in await store.load_enabled_jobs():
            self.apply_job(job)
        logger.info(
            "定时任务调度器已启动 enabled_jobs 已加载 timezone=%s",
            self._settings.timezone,
        )

    async def close(self) -> None:
        """停掉 APScheduler；正在执行的包装器任务随事件循环关闭（有界可恢复）。"""

        if self._scheduler is None:
            return
        self._scheduler.shutdown(wait=False)
        self._scheduler = None
        logger.info("定时任务调度器已停止")

    def apply_job(self, job: ScheduledJobRecord) -> None:
        """按最新配置注册或替换一个调度条目；任务停用则移除。

        为什么在禁用时也调它而不是只在启用时：管理端把任务从启用改成停用后，调度器里
        还挂着旧条目，必须显式摘掉。cron 解析失败只记日志不抛出——配置在写入侧已校验，
        走到这里的脏数据（直接改库）不应拖垮启动流程。
        """

        if self._scheduler is None:
            return
        self._remove_scheduled(job.id)
        if not job.enabled:
            return
        try:
            trigger = CronTrigger.from_crontab(job.cron_expr, timezone=self._tzinfo)
        except ValueError:
            logger.error(
                "定时任务 cron 无效，跳过注册 job=%s", job.key
            )
            return
        self._scheduler.add_job(
            self._run_scheduled,
            trigger=trigger,
            args=[job.id],
            id=str(job.id),
            name=job.key,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=self._settings.misfire_grace_seconds,
            replace_existing=True,
        )

    def remove_job(self, job_id: UUID) -> None:
        """把一个任务从调度器摘除（任务被删除时调用）。"""

        self._remove_scheduled(job_id)

    def _remove_scheduled(self, job_id: UUID) -> None:
        if self._scheduler is None:
            return
        try:
            self._scheduler.remove_job(str(job_id))
        except JobLookupError:
            # 本来就没挂上（停用、注册失败、重复删除）都是正常状态，静默即可。
            pass

    def next_run_at(self, job_id: UUID) -> datetime | None:
        """返回任务下一次计划执行的 UTC 时刻；调度器未启动或任务未注册时为 None。"""

        if self._scheduler is None:
            return None
        scheduled = self._scheduler.get_job(str(job_id))
        if scheduled is None or scheduled.next_run_time is None:
            return None
        return scheduled.next_run_time.astimezone(UTC)

    def parse_cron(self, cron_expr: str) -> CronTrigger:
        """解析 5 段式 cron 为触发器；无效时抛 ``ValueError``。

        本方法同时是写入校验与预览计算的共用入口，保证「存得进去的」和「算得出下一次
        的」永远是同一种字符串。
        """

        return CronTrigger.from_crontab(cron_expr, timezone=self._tzinfo)

    def upcoming_fire_times(
        self,
        cron_expr: str,
        *,
        count: int = 3,
    ) -> tuple[list[datetime], list[str]]:
        """计算未来 N 次执行时间，返回 (UTC 时刻列表, 解释时区下的 ISO 展示列表)。"""

        trigger = self.parse_cron(cron_expr)
        utc_times: list[datetime] = []
        cursor = self._clock()
        next_time = trigger.get_next_fire_time(None, cursor)
        while next_time is not None and len(utc_times) < count:
            utc_times.append(next_time.astimezone(UTC))
            # 把游标推过刚拿到的时刻，下一次才不会返回同一个点。
            next_time = trigger.get_next_fire_time(
                next_time, next_time + timedelta(seconds=1)
            )
        local_times = [moment.astimezone(self._tzinfo).isoformat() for moment in utc_times]
        return utc_times, local_times

    async def trigger_now(self, job: ScheduledJobRecord) -> UUID:
        """手动触发一次执行，返回新执行记录的 id；上一轮未结束时抛冲突异常。

        Args:
            job: 已从数据库读出的任务记录（手动触发允许作用于停用任务，便于验证配置）。

        Returns:
            新建执行记录的 UUID。

        Raises:
            ScheduledJobAlreadyRunningError: 同一任务的执行锁正被持有。
        """

        if self._is_running(job.id):
            raise ScheduledJobAlreadyRunningError(job.id)
        store = self._store_factory()
        run_id = await store.start_run(job.id, trigger_type="manual", started_at=self._clock())
        self._spawn(job, run_id, "manual")
        return run_id

    def _is_running(self, job_id: UUID) -> bool:
        lock = self._locks.get(job_id)
        return lock is not None and lock.locked()

    def _spawn(self, job: ScheduledJobRecord, run_id: UUID, trigger_type: str) -> None:
        """把执行包装器派生为后台任务；引用挂到集合上防止被垃圾回收。"""

        task = asyncio.create_task(self._execute(job, run_id, trigger_type))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_scheduled(self, job_id: UUID) -> None:
        """cron 到点入口：重读配置 → 判停用/删除 → 判重叠 → 起一轮执行。

        为什么到点后重新读库而不是闭包里带配置：注册之后配置可能已被管理端修改或删除，
        数据库才是事实来源（ADR 0014）；读出来是 None（已删除）或已停用就安静放弃。
        """

        store = self._store_factory()
        job = await store.get_job(job_id)
        if job is None or not job.enabled:
            return
        if self._is_running(job_id):
            await store.record_skipped(
                job_id,
                trigger_type="scheduled",
                started_at=self._clock(),
                stats=dict(_SKIPPED_STATS),
            )
            return
        run_id = await store.start_run(
            job_id, trigger_type="scheduled", started_at=self._clock()
        )
        await self._execute(job, run_id, "scheduled")

    async def _execute(
        self,
        job: ScheduledJobRecord,
        run_id: UUID,
        trigger_type: str,
    ) -> None:
        """统一执行包装器：锁内新建写 Runtime、只跑对应步骤、写终态、裁剪历史。

        两个触发入口（cron 与手动）在这里汇合，保证「执行历史怎么记、Runtime 何时关、
        失败如何归类」只有一份逻辑。历史写入失败只记日志不中断——记录是观测手段，
        不该反过来决定任务成败。
        """

        store = self._store_factory()
        lock = self._locks.setdefault(job.id, asyncio.Lock())
        if lock.locked():
            # 极小概率的并发窗口（检查锁与拿到运行 id 之间又来了一发触发）：
            # 这条运行记录直接按 skipped 收尾，保证每条 running 记录都有终态。
            await self._safe_finish(
                store,
                run_id,
                status="skipped",
                stats=dict(_SKIPPED_STATS),
                error_type=None,
                finished_at=None,
            )
            return
        async with lock:
            runtime = None
            stats: dict = {}
            error_type: str | None = None
            try:
                # 1、防御性重验参数：库里配置可能被绕过 API 直接修改。
                spec = get_task_type_spec(job.task_type)
                if spec is None:
                    raise ScheduledJobUnknownTypeError()
                params = spec.validate_params(job.params)
                # 2、按次新建写 Runtime（与手动流水线同一工厂），用完即关。
                runtime = self._write_runtime_factory()
                # 3、只跑本任务类型对应的那一步。
                stats = await self._run_task(job.task_type, runtime, params)
                logger.info(
                    "定时任务执行完成 job=%s trigger=%s", job.key, trigger_type
                )
            except Exception as exc:
                # 只记类型名：异常文本可能带凭据、正文或第三方响应（全项目统一口径）。
                error_type = type(exc).__name__
                logger.error(
                    "定时任务执行失败 job=%s trigger=%s error_type=%s",
                    job.key,
                    trigger_type,
                    error_type,
                )
            finally:
                if runtime is not None:
                    try:
                        await runtime.close()
                    except Exception as close_error:
                        logger.error(
                            "定时任务写 Runtime 关闭失败 job=%s error_type=%s",
                            job.key,
                            type(close_error).__name__,
                        )
            # 4、写终态：成败只差在 error_type；skipped 不写结束时刻（与模型注释一致）。
            await self._safe_finish(
                store,
                run_id,
                status="failed" if error_type else "succeeded",
                stats=stats,
                error_type=error_type,
                finished_at=self._clock(),
            )
            # 5、裁剪历史：每任务只留最近 N 条，防止执行历史无界增长。
            try:
                await store.prune_runs(
                    job.id, keep=self._settings.run_history_retention
                )
            except Exception as prune_error:
                logger.error(
                    "定时任务历史裁剪失败 job=%s error_type=%s",
                    job.key,
                    type(prune_error).__name__,
                )

    async def _run_task(self, task_type: str, runtime: PipelineWriteRuntime, params: dict) -> dict:
        """按任务类型分发到对应执行器，返回脱敏统计 dict。"""

        if task_type == "freshrss_sync":
            result = await runtime.sync_only(
                limit_per_source=int(params["limit_per_source"])
            )
            return {
                "source_count": result.source_count,
                "successful_source_count": result.successful_source_count,
                "synchronized_document_count": result.synchronized_count,
                "checkpoint_advanced_count": result.checkpoint_advanced_count,
                "failed_source_count": result.failed_source_count,
                "failures": _aggregate_failures(
                    failure.error_type for failure in result.failures
                ),
            }
        if task_type == "index_pending":
            result = await runtime.index_only(
                batch_size=int(params["batch_size"]),
                stale_after=timedelta(minutes=int(params["stale_after_minutes"])),
            )
            return {
                "requeued_stale_count": result.requeued_stale_count,
                "candidate_count": result.candidate_count,
                "indexed_count": result.indexed_count,
                "skipped_count": result.skipped_count,
                "failed_count": result.failed_count,
                "failures": _aggregate_failures(
                    failure.error_type for failure in result.failures
                ),
            }
        if task_type == "prune_old_documents":
            # prune_old_documents 任务需要独立的 Session 和组件
            from agent_lab.services.document_retention_service import DocumentRetentionService
            from agent_lab.repositories.document_repository import DocumentRepository
            from agent_lab.db.session import async_session_factory

            # 创建独立 Session
            async with async_session_factory() as session:
                document_repo = DocumentRepository(session)
                qdrant_store = runtime.indexing_runtime.service.qdrant_store

                retention_service = DocumentRetentionService(
                    session=session,
                    document_repo=document_repo,
                    qdrant_store=qdrant_store,
                )

                result = await retention_service.prune_old_documents(
                    retention_days=int(params["retention_days"]),
                    dry_run=bool(params["dry_run"]),
                )

            return result.to_job_run_stats()
        raise ScheduledJobUnknownTypeError()

    async def _safe_finish(
        self,
        store: ScheduledJobStore,
        run_id: UUID,
        *,
        status: str,
        stats: dict,
        error_type: str | None,
        finished_at: datetime | None,
    ) -> None:
        """写执行终态，数据库失败只记日志——历史是观测手段，不影响执行本身。"""

        try:
            await store.finish_run(
                run_id,
                status=status,
                finished_at=finished_at,
                stats=stats,
                error_type=error_type,
            )
        except Exception as exc:
            logger.error(
                "任务执行终态写入失败 run=%s status=%s error_type=%s",
                run_id,
                status,
                type(exc).__name__,
            )


__all__ = [
    "SKIPPED_PREVIOUS_RUNNING_REASON",
    "ScheduledJobRunner",
]
