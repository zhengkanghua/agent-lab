"""周期配置的短事务存取；任务执行独立保存在公共任务仓储中。"""

from uuid import uuid4
from sqlalchemy import select

from agent_lab.models.scheduled_job import ScheduledJobRecord
from agent_lab.tasks.repository import TaskRepository


class ScheduledJobRepository:
    def __init__(self, session):
        self._session = session

    async def list_jobs(self):
        return (await self._session.scalars(select(ScheduledJobRecord).order_by(ScheduledJobRecord.key))).all()

    async def get_job(self, job_id):
        return await self._session.get(ScheduledJobRecord, job_id)

    async def lock_job(self, job_id):
        return await self._session.scalar(select(ScheduledJobRecord).where(
            ScheduledJobRecord.id == job_id,
        ).with_for_update().execution_options(populate_existing=True))

    async def get_job_by_key(self, key):
        return await self._session.scalar(select(ScheduledJobRecord).where(ScheduledJobRecord.key == key))

    async def create_job(self, **values):
        record = ScheduledJobRecord(id=uuid4(), **values)
        self._session.add(record)
        await self._session.commit()
        return record

    async def commit(self):
        await self._session.commit()

    async def refresh(self, record):
        await self._session.refresh(record)

    async def delete_job(self, record):
        await self._session.delete(record)
        await self._session.commit()

    async def active_run(self, job_id):
        return await TaskRepository(self._session).active(source_job_id=job_id)

    async def list_runs(self, job_id, *, limit):
        return await TaskRepository(self._session).list_runs(source_job_id=job_id, limit=limit)

    async def latest_run(self, job_id):
        rows = await self.list_runs(job_id, limit=1)
        return rows[0] if rows else None

    async def get_run(self, job_id, run_id):
        run = await TaskRepository(self._session).require_run(run_id)
        return run if run.source_job_id == job_id else None
