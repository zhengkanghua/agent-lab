"""周期配置的短事务存取；任务执行独立保存在公共任务仓储中。

``delete_job`` 是删配置的唯一路径，它必须在同一事务里把历史 run 的 ``job_id`` 置空。
库里没有 ``ON DELETE SET NULL`` 了，这一步没有数据库兜底。
"""

from uuid import uuid4
from sqlalchemy import select, update

from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
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
        """删除周期配置，并在同一事务里断开历史执行对它的指向。

        库里没有 ``ON DELETE SET NULL``，不补这一步会静默留下指向已删配置的 ``job_id``。
        置空只影响 ``job_id`` 这一列：``source_job_id`` 和受理时冻结的 ``config_snapshot``
        原样保留，历史仍能按原配置身份查询（ADR 0019）。

        Args:
            record: 已加行锁的 ``ScheduledJobRecord``。

        Notes:
            一次 PostgreSQL 写入事务，覆盖两条语句（置空历史 + 删配置行）。
        """

        await self._session.execute(
            update(JobRunRecord)
            .where(JobRunRecord.job_id == record.id)
            .values(job_id=None)
        )
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
