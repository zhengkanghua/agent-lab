"""定时任务管理的事务边界与业务校验（一个 HTTP 请求一个实例）。

本模块把管理 API 的意图翻译成对仓储和调度器的协作操作：字段级校验（任务类型、cron、
参数）在这里完成并抛领域异常；写库成功后调 ``ScheduledJobRunner.apply_job`` /
``remove_job`` 让运行中的调度器立即跟上最新配置。调度器未启动（``SCHEDULER_ENABLED``
关闭）时装卸是无害空操作，管理功能照常可用。

事务边界与 ``UserAdminService`` 相同：Service 持有请求级 Session，每个公开方法自己
提交；领域异常抛出时不提交，Session 由请求收尾关闭。
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.repositories.scheduled_job_repository import ScheduledJobRepository
from agent_lab.services.scheduled_task_errors import (
    ScheduledJobInvalidCronError,
    ScheduledJobInvalidParamsError,
    ScheduledJobKeyConflictError,
    ScheduledJobNotFoundError,
    ScheduledJobUnknownTypeError,
)
from agent_lab.services.scheduler_runner import ScheduledJobRunner
from agent_lab.services.scheduled_task_registry import TaskTypeSpec, get_task_type_spec


@dataclass(frozen=True, slots=True)
class ScheduledJobView:
    """一条定时任务及其运行侧视图（下次执行时间、最近一次执行）。

    为什么要个视图而不是直接返回 ORM 记录：``next_run_at`` 活在调度器内存里、
    ``last_run`` 在另一张表，三样东西凑齐才算「列表页一行的完整信息」。
    """

    record: ScheduledJobRecord
    next_run_at: datetime | None
    last_run: JobRunRecord | None


class ScheduledJobService:
    """管理定时任务配置：校验 → 写库 → 同步调度器，三步走。"""

    def __init__(self, session: AsyncSession, runner: ScheduledJobRunner) -> None:
        """绑定请求级 Session 与进程级调度器。

        Args:
            session: 请求级异步 Session，同时是本 Service 的事务边界。
            runner: 进程级调度器包装器；写库成功后用它同步调度状态。
        """

        self._repository = ScheduledJobRepository(session)
        self._runner = runner

    async def list_jobs(self) -> list[ScheduledJobView]:
        """返回全部任务及各自的下次执行时间与最近一次执行。"""

        views: list[ScheduledJobView] = []
        for record in await self._repository.list_jobs():
            views.append(await self._build_view(record))
        return views

    async def get_job(self, job_id: UUID) -> ScheduledJobView:
        """取单个任务的完整视图；不存在抛 ``ScheduledJobNotFoundError``。"""

        record = await self._require_job(job_id)
        return await self._build_view(record)

    async def create_job(
        self,
        *,
        key: str,
        task_type: str,
        cron_expr: str,
        params: dict | None,
        enabled: bool,
    ) -> ScheduledJobView:
        """校验并创建任务，成功后注册进调度器。

        Raises:
            ScheduledJobUnknownTypeError: 任务类型不在注册表。
            ScheduledJobInvalidCronError: cron 无法解析。
            ScheduledJobInvalidParamsError: 参数不符合类型 schema。
            ScheduledJobKeyConflictError: 业务唯一键已存在。
        """

        spec = self._require_task_type(task_type)
        self._require_cron(cron_expr)
        normalized_params = self._normalize_params(spec, params)
        if await self._repository.get_job_by_key(key) is not None:
            raise ScheduledJobKeyConflictError()
        record = await self._repository.create_job(
            key=key,
            task_type=task_type,
            cron_expr=cron_expr,
            params=normalized_params,
            enabled=enabled,
        )
        self._runner.apply_job(record)
        return await self._build_view(record)

    async def update_job(
        self,
        job_id: UUID,
        *,
        cron_expr: str | None = None,
        params: dict | None = None,
        enabled: bool | None = None,
    ) -> ScheduledJobView:
        """修改 cron / 参数 / 启停（key 与任务类型不可改），成功后同步调度器。

        只提交了哪个字段就改哪个字段；``params`` 传了（哪怕空 dict）就整体替换并按
        任务类型重新校验。
        """

        record = await self._require_job(job_id)
        if cron_expr is not None:
            self._require_cron(cron_expr)
            record.cron_expr = cron_expr
        if params is not None:
            spec = self._require_task_type(record.task_type)
            record.params = self._normalize_params(spec, params)
        if enabled is not None:
            record.enabled = enabled
        await self._repository.commit()
        self._runner.apply_job(record)
        return await self._build_view(record)

    async def delete_job(self, job_id: UUID) -> None:
        """删除任务；执行历史随数据库级联删除，调度器条目同步摘除。"""

        record = await self._require_job(job_id)
        self._runner.remove_job(job_id)
        await self._repository.delete_job(record)

    async def trigger(self, job_id: UUID) -> UUID:
        """手动触发一次执行，返回新执行记录 id。

        Raises:
            ScheduledJobAlreadyRunningError: 上一轮执行尚未结束。
        """

        record = await self._require_job(job_id)
        return await self._runner.trigger_now(record)

    async def list_runs(
        self,
        job_id: UUID,
        *,
        limit: int,
    ) -> list[JobRunRecord]:
        """返回任务的执行历史（新→旧）；任务不存在抛 404 领域异常。"""

        record = await self._require_job(job_id)
        return list(await self._repository.list_runs(record.id, limit=limit))

    def validate_cron(self, cron_expr: str) -> tuple[list[datetime], list[str]]:
        """校验 cron 并给出未来 3 次执行时间，供管理端提交前预览。

        Returns:
            (UTC 时刻列表, 解释时区下的 ISO 本地展示列表)。

        Raises:
            ScheduledJobInvalidCronError: cron 无法解析。
        """

        try:
            self._runner.parse_cron(cron_expr)
        except ValueError as exc:
            raise ScheduledJobInvalidCronError() from exc
        return self._runner.upcoming_fire_times(cron_expr)

    async def _build_view(self, record: ScheduledJobRecord) -> ScheduledJobView:
        """凑齐一行的完整视图：记录 + 调度器里的下次执行 + 最近一次执行。"""

        return ScheduledJobView(
            record=record,
            next_run_at=self._runner.next_run_at(record.id),
            last_run=await self._repository.latest_run(record.id),
        )

    async def _require_job(self, job_id: UUID) -> ScheduledJobRecord:
        record = await self._repository.get_job(job_id)
        if record is None:
            raise ScheduledJobNotFoundError()
        return record

    def _require_task_type(self, task_type: str) -> TaskTypeSpec:
        spec = get_task_type_spec(task_type)
        if spec is None:
            raise ScheduledJobUnknownTypeError()
        return spec

    def _require_cron(self, cron_expr: str) -> None:
        try:
            self._runner.parse_cron(cron_expr)
        except ValueError as exc:
            raise ScheduledJobInvalidCronError() from exc

    def _normalize_params(self, spec: TaskTypeSpec, params: dict | None) -> dict:
        try:
            return spec.validate_params(params)
        except ValidationError as exc:
            raise ScheduledJobInvalidParamsError() from exc


__all__ = ["ScheduledJobService", "ScheduledJobView"]
