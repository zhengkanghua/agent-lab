"""用量链路离线测试共用的内存 SQLite 用量库与记录工厂。

与 ``task_helpers.py`` 同一角色：让真实的 ORM 映射、唯一约束与事务跑在内存 SQLite 上，不连真
PostgreSQL。真库守的是内存库证明不了的东西（两条索引、时刻列带时区、跨库隔离），在
``test_usage_postgres_integration.py`` 里，默认跳过。

本模块不访问网络、不连 PostgreSQL、不读 ``.env``。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy import Integer, MetaData, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_lab.usage.assembly import UsageRuntime
from agent_lab.usage.collector import QueuedUsageCollector
from agent_lab.usage.contracts import UsageRecord, UsageSource, UsageStatus
from agent_lab.usage.models import UsageRecordRow
from agent_lab.usage.repository import UsageRepository


@asynccontextmanager
async def usage_engine():
    """内存 SQLite 上的真实用量表，连引擎一起交出来。

    Yields:
        ``SimpleNamespace(engine=..., sessions=...)``。需要整体交给 ``UsageRuntime``（它要负责
        ``close``）的用例用它；只要会话工厂的用例用下面的 ``usage_database``。
    """

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    table = UsageRecordRow.__table__.to_metadata(metadata)
    table.c.id.type = Integer()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield SimpleNamespace(engine=engine, sessions=sessions)
    finally:
        await engine.dispose()


@asynccontextmanager
async def usage_database():
    """内存 SQLite 上的真实用量表。

    只对 SQLite 做一处适配：``BigInteger`` 主键在 SQLite 里不会自增（只有 ``INTEGER PRIMARY
    KEY`` 才是 rowid 别名），所以离线层把这一列降成 ``Integer``。生产库是 PostgreSQL，模型与
    迁移里的列类型不变。

    Yields:
        绑定这个内存库的 ``async_sessionmaker``。
    """

    async with usage_engine() as database:
        yield database.sessions


@asynccontextmanager
async def usage_service():
    """内存 SQLite 上的完整用量库资源：引擎 + 短会话工厂 + 真实直写采集器。

    走完整链路的端到端用例用它（装配 → 采集器 → 入队 → 落库 → 查询），而不是只拿会话工厂。

    Yields:
        可直接当作 lifespan 的 ``usage_runtime`` 注入的 ``UsageRuntime``。
    """

    async with usage_engine() as database:
        yield UsageRuntime(
            engine=database.engine,
            session_factory=database.sessions,
            collector=QueuedUsageCollector(
                session_factory=database.sessions,
                loop=asyncio.get_running_loop(),
            ),
        )


def make_record(**overrides: Any) -> UsageRecord:
    """造一条字段齐全的用量记录；``overrides`` 覆盖单个字段。"""

    defaults: dict[str, Any] = {
        "user_id": uuid4(),
        "thread_id": uuid4(),
        "run_id": uuid4(),
        "model_name": "test-model",
        "input_tokens": 10,
        "output_tokens": 4,
        "cached_tokens": 6,
        "total_tokens": 14,
        "duration_ms": 123,
        "status": UsageStatus.COMPLETED,
        "source": UsageSource.UPSTREAM,
        "occurred_at": datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
    }
    return UsageRecord(**{**defaults, **overrides})


async def add_records(
    sessions: async_sessionmaker[AsyncSession],
    records: list[UsageRecord],
) -> None:
    """通过仓储一次写入多条并提交。"""

    async with sessions() as session:
        repository = UsageRepository(session)
        for record in records:
            await repository.add(record)
        await session.commit()


async def stored_rows(sessions: async_sessionmaker[AsyncSession]) -> list[UsageRecordRow]:
    """按主键顺序读出全部行。"""

    async with sessions() as session:
        result = await session.execute(select(UsageRecordRow).order_by(UsageRecordRow.id))
        return list(result.scalars().all())


__all__ = [
    "add_records",
    "make_record",
    "stored_rows",
    "usage_database",
    "usage_engine",
    "usage_service",
]
