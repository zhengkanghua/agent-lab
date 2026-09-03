"""定时任务配置与执行历史的 PostgreSQL 数据访问。

本模块提供两层：
- ``ScheduledJobRepository``：绑定一个 AsyncSession 的仓储，事务边界由调用方（HTTP
  请求的 Service）控制，方法只做读写不管理连接；
- ``ScheduledJobStore``：给调度器后台执行用的「短会话」适配器，每个方法开一条独立
  Session、用完即关——后台任务不属于任何 HTTP 请求，不能共享请求 Session，也不该
  长期占用连接（与 ADR 0010 的短会话思路一致）。

本模块不解析 cron、不启动调度器、不访问 FreshRSS/Ollama/Qdrant。
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord


class ScheduledJobRepository:
    """绑定单个 AsyncSession 的定时任务仓储。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定本次工作单元使用的 Session。

        Args:
            session: 由调用方控制生命周期的异步 Session。
        """

        self._session = session

    async def list_jobs(self) -> Sequence[ScheduledJobRecord]:
        """返回全部定时任务，按业务键排序保证输出稳定。"""

        result = await self._session.execute(
            select(ScheduledJobRecord).order_by(ScheduledJobRecord.key)
        )
        return result.scalars().all()

    async def list_enabled_jobs(self) -> Sequence[ScheduledJobRecord]:
        """返回全部启用中的定时任务，供调度器启动时加载。"""

        result = await self._session.execute(
            select(ScheduledJobRecord)
            .where(ScheduledJobRecord.enabled)
            .order_by(ScheduledJobRecord.key)
        )
        return result.scalars().all()

    async def get_job(self, job_id: UUID) -> ScheduledJobRecord | None:
        """按主键取任务；不存在返回 None。"""

        return await self._session.get(ScheduledJobRecord, job_id)

    async def get_job_by_key(self, key: str) -> ScheduledJobRecord | None:
        """按业务唯一键取任务，用于创建时的冲突检查。"""

        result = await self._session.execute(
            select(ScheduledJobRecord).where(ScheduledJobRecord.key == key)
        )
        return result.scalars().first()

    async def create_job(
        self,
        *,
        key: str,
        task_type: str,
        cron_expr: str,
        params: dict,
        enabled: bool,
    ) -> ScheduledJobRecord:
        """插入一条定时任务并提交。"""

        record = ScheduledJobRecord(
            id=uuid4(),
            key=key,
            task_type=task_type,
            cron_expr=cron_expr,
            params=params,
            enabled=enabled,
        )
        self._session.add(record)
        await self._session.commit()
        return record

    async def commit(self) -> None:
        """提交当前 Session 上累计的字段修改（更新场景由 Service 就地改列）。"""

        await self._session.commit()

    async def refresh(self, record: ScheduledJobRecord) -> None:
        """刷新 ORM 对象，重新从数据库加载所有字段（含服务器端自动更新的时间戳）。"""

        await self._session.refresh(record)

    async def delete_job(self, record: ScheduledJobRecord) -> None:
        """删除任务；执行历史由数据库级联删除。"""

        await self._session.delete(record)
        await self._session.commit()

    async def create_run(
        self,
        *,
        job_id: UUID,
        trigger_type: str,
        status: str,
        started_at: datetime,
        stats: dict,
        error_type: str | None = None,
    ) -> JobRunRecord:
        """插入一条任务执行记录并提交，返回带主键的记录。"""

        record = JobRunRecord(
            id=uuid4(),
            job_id=job_id,
            trigger_type=trigger_type,
            status=status,
            started_at=started_at,
            stats=stats,
            error_type=error_type,
        )
        self._session.add(record)
        await self._session.commit()
        return record

    async def finish_run(
        self,
        run_id: UUID,
        *,
        status: str,
        finished_at: datetime | None,
        stats: dict,
        error_type: str | None,
    ) -> None:
        """把一条执行记录更新为终态（succeeded/failed/skipped）并提交。"""

        record = await self._session.get(JobRunRecord, run_id)
        if record is None:
            # 落库记录被并发删除时保持静默：历史行丢失不影响任务执行本身。
            return
        record.status = status
        record.finished_at = finished_at
        record.stats = stats
        record.error_type = error_type
        await self._session.commit()

    async def list_runs(
        self,
        job_id: UUID,
        *,
        limit: int,
    ) -> Sequence[JobRunRecord]:
        """返回某任务最近的执行记录，新→旧；并列时按主键倒序保证稳定。"""

        result = await self._session.execute(
            select(JobRunRecord)
            .where(JobRunRecord.job_id == job_id)
            .order_by(JobRunRecord.started_at.desc(), JobRunRecord.id.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def latest_run(self, job_id: UUID) -> JobRunRecord | None:
        """返回某任务最近一次执行记录；没有历史时返回 None。"""

        runs = await self.list_runs(job_id, limit=1)
        return runs[0] if runs else None

    async def prune_runs(self, job_id: UUID, *, keep: int) -> int:
        """只保留某任务最近 ``keep`` 条执行记录，返回被裁掉的条数。

        「最近」的判定与 ``list_runs`` 一致（started_at 倒序、主键打破并列），保证
        管理端看到的列表和留下的记录是同一批。
        """

        subquery = (
            select(JobRunRecord.id)
            .where(JobRunRecord.job_id == job_id)
            .order_by(JobRunRecord.started_at.desc(), JobRunRecord.id.desc())
            .limit(keep)
            .scalar_subquery()
        )
        result = await self._session.execute(
            delete(JobRunRecord).where(
                JobRunRecord.job_id == job_id,
                JobRunRecord.id.not_in(subquery),
            )
        )
        await self._session.commit()
        return result.rowcount or 0

    async def count_runs(self, job_id: UUID) -> int:
        """返回某任务的执行记录总数（测试与运维检查用）。"""

        result = await self._session.execute(
            select(func.count())
            .select_from(JobRunRecord)
            .where(JobRunRecord.job_id == job_id)
        )
        return int(result.scalar_one())


class ScheduledJobStore:
    """给调度器后台执行用的短会话适配器：每个方法独立开 Session、用完即关。

    为什么不让调度器直接拿一个长 Session：调度任务的执行以分钟计，长会话会在整个
    执行期间占住一条连接池连接（连接池总共 5+10），几个任务并行就能把检索页挤到
    报数据库不可用。每个操作开短会话的代价只是几次连接获取，可以忽略。
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """绑定进程级 Session 工厂，不执行任何 I/O。"""

        self._session_factory = session_factory

    @asynccontextmanager
    async def _session(self):
        """打开一个短生命周期 Session，退出时自动关闭。"""

        async with self._session_factory() as session:
            yield session

    async def load_enabled_jobs(self) -> Sequence[ScheduledJobRecord]:
        """读取全部启用中的任务，供调度器启动时注册。"""

        async with self._session() as session:
            return await ScheduledJobRepository(session).list_enabled_jobs()

    async def get_job(self, job_id: UUID) -> ScheduledJobRecord | None:
        """按 id 读取任务（cron 到点时重读，配置可能已被管理端修改）。"""

        async with self._session() as session:
            return await ScheduledJobRepository(session).get_job(job_id)

    async def record_skipped(
        self,
        job_id: UUID,
        *,
        trigger_type: str,
        started_at: datetime,
        stats: dict,
    ) -> UUID:
        """记一条 ``skipped`` 执行记录（上一轮未结束时到点的触发），返回记录 id。"""

        async with self._session() as session:
            record = await ScheduledJobRepository(session).create_run(
                job_id=job_id,
                trigger_type=trigger_type,
                status="skipped",
                started_at=started_at,
                stats=stats,
            )
            return record.id

    async def start_run(
        self,
        job_id: UUID,
        *,
        trigger_type: str,
        started_at: datetime,
    ) -> UUID:
        """记一条 ``running`` 执行记录并返回记录 id。"""

        async with self._session() as session:
            record = await ScheduledJobRepository(session).create_run(
                job_id=job_id,
                trigger_type=trigger_type,
                status="running",
                started_at=started_at,
                stats={},
            )
            return record.id

    async def finish_run(
        self,
        run_id: UUID,
        *,
        status: str,
        finished_at: datetime | None,
        stats: dict,
        error_type: str | None,
    ) -> None:
        """把执行记录更新为终态。"""

        async with self._session() as session:
            await ScheduledJobRepository(session).finish_run(
                run_id,
                status=status,
                finished_at=finished_at,
                stats=stats,
                error_type=error_type,
            )

    async def prune_runs(self, job_id: UUID, *, keep: int) -> None:
        """裁掉超出保留条数的旧执行记录。"""

        async with self._session() as session:
            await ScheduledJobRepository(session).prune_runs(job_id, keep=keep)


__all__ = ["ScheduledJobRepository", "ScheduledJobStore"]
