"""周期配置管理；配置行锁与 Beat 受理协调，新执行使用新配置，旧执行保留快照。"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.repositories.scheduled_job_repository import ScheduledJobRepository
from agent_lab.services.scheduled_task_errors import (
    ScheduledJobInvalidCronError, ScheduledJobInvalidParamsError,
    ScheduledJobKeyConflictError, ScheduledJobNotFoundError, ScheduledJobUnknownTypeError,
)


@dataclass(frozen=True, slots=True)
class ScheduledJobView:
    record: ScheduledJobRecord
    next_run_at: datetime | None
    last_run: JobRunRecord | None
    active_run: JobRunRecord | None = None


class ScheduledJobService:
    def __init__(self, session, cron, registry):
        self._repository = ScheduledJobRepository(session)
        self._cron, self._registry = cron, registry

    @property
    def timezone(self):
        return self._cron.timezone

    async def list_jobs(self):
        return [await self._build_view(record) for record in await self._repository.list_jobs()]

    async def get_job(self, job_id):
        return await self._build_view(await self._require_job(job_id))

    async def create_job(self, *, key, task_type, cron_expr, params, enabled):
        spec = self._registry.require(task_type)
        if not spec.schedulable:
            raise ScheduledJobUnknownTypeError()
        self._require_cron(cron_expr)
        normalized = self._normalize_params(spec, params)
        if await self._repository.get_job_by_key(key) is not None:
            raise ScheduledJobKeyConflictError()
        times, _ = self._cron.upcoming_fire_times(cron_expr, count=1)
        try:
            record = await self._repository.create_job(
                key=key, task_type=task_type, cron_expr=cron_expr, params=normalized,
                enabled=enabled, next_run_at=times[0] if enabled and times else None,
            )
        except IntegrityError:
            raise ScheduledJobKeyConflictError() from None
        await self._repository.refresh(record)
        return await self._build_view(record)

    async def update_job(self, job_id, *, cron_expr=None, params=None, enabled=None):
        record = await self._repository.lock_job(job_id)
        if record is None:
            raise ScheduledJobNotFoundError()
        if cron_expr is not None:
            self._require_cron(cron_expr)
            record.cron_expr = cron_expr
        if params is not None:
            record.params = self._normalize_params(self._registry.require(record.task_type), params)
        if enabled is not None:
            record.enabled = enabled
        record.config_version += 1
        record.next_run_at = self._cron.planned_run_at(record)
        await self._repository.commit()
        await self._repository.refresh(record)
        return await self._build_view(record)

    async def delete_job(self, job_id):
        record = await self._repository.lock_job(job_id)
        if record is None:
            raise ScheduledJobNotFoundError()
        await self._repository.delete_job(record)

    async def list_runs(self, job_id, *, limit):
        return list(await self._repository.list_runs(job_id, limit=limit))

    async def get_run(self, job_id, run_id):
        run = await self._repository.get_run(job_id, run_id)
        if run is None:
            raise ScheduledJobNotFoundError()
        return run

    def validate_cron(self, cron_expr):
        self._require_cron(cron_expr)
        return self._cron.upcoming_fire_times(cron_expr)

    async def _build_view(self, record):
        return ScheduledJobView(record, record.next_run_at,
            await self._repository.latest_run(record.id), await self._repository.active_run(record.id))

    async def _require_job(self, job_id):
        record = await self._repository.get_job(job_id)
        if record is None:
            raise ScheduledJobNotFoundError()
        return record

    def _require_cron(self, expression):
        try:
            self._cron.parse_cron(expression)
        except ValueError:
            raise ScheduledJobInvalidCronError() from None

    def _normalize_params(self, spec, params):
        try:
            return spec.validate_params(params)
        except ValidationError:
            raise ScheduledJobInvalidParamsError() from None
