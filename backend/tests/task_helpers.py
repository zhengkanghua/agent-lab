"""公共任务的离线存储接缝：真实 ORM/事务跑在内存 SQLite，绝不访问开发数据库。

这里只适配 JSON/UTC 和部分索引语法，不模拟 PostgreSQL 行锁或咨询锁；并发事务、
多进程领取与迁移仍由门控 PostgreSQL 测试验证，不能据此声称真库并发已通过。
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from agent_lab.models.scheduled_job import (
    JobRunRecord, ScheduledJobRecord, TaskPolicyChangeRecord, TaskPolicyRecord, TaskRequestRecord,
)
from agent_lab.models.write_operation import WriteOperationRecord
from agent_lab.tasks.contracts import ExecutionPolicy
from agent_lab.tasks.registry import TaskRegistry, TaskTypeSpec
from agent_lab.tasks.repository import TaskStore
from agent_lab.tasks.service import TaskService
from agent_lab.tasks.worker import TaskWorker


@compiles(JSONB, "sqlite")
def sqlite_json_type(_type, _compiler, **_kwargs):
    return "JSON"


class UTCDateTime(DATETIME):
    def result_processor(self, dialect, coltype):
        parse = super().result_processor(dialect, coltype)
        return lambda value: parse(value).replace(tzinfo=UTC) if value is not None else None


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 12, 1, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class EchoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = 1


async def echo(_runtime, params):
    return {"value": params["value"]}


def echo_spec(**overrides):
    return TaskTypeSpec(**{
        "task_type": "test_echo", "description": "无外部副作用的测试任务",
        "params_model": EchoParams, "execute": echo, **overrides,
    })


@asynccontextmanager
async def task_system(*specs, dispatcher=None):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    engine.sync_engine.dialect.colspecs = {**engine.sync_engine.dialect.colspecs, DateTime: UTCDateTime}

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine.sync_engine, "before_cursor_execute", retval=True)
    def advisory_lock(_connection, _cursor, statement, parameters, _context, _many):
        if statement.startswith("SELECT pg_advisory_xact_lock"):
            return "SELECT 1", ()
        return statement, parameters

    metadata = MetaData()
    for model in (ScheduledJobRecord, JobRunRecord, TaskPolicyRecord, TaskPolicyChangeRecord, TaskRequestRecord, WriteOperationRecord):
        table = model.__table__.to_metadata(metadata)
        for index in table.indexes:
            where = index.dialect_options["postgresql"].get("where")
            if where is not None:
                index.dialect_options["sqlite"]["where"] = where
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    clock = Clock()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        async with sessions() as session:
            session.add(TaskPolicyRecord(id=1, policy=ExecutionPolicy().model_dump(),
                updated_at=clock(), updated_by="test"))
            await session.commit()
        registry = TaskRegistry(specs or [echo_spec()])
        store = TaskStore(sessions, clock=clock)
        service = TaskService(sessions, registry, dispatcher=dispatcher, clock=clock)
        yield SimpleNamespace(sessions=sessions, store=store, service=service, clock=clock,
            registry=registry, worker=TaskWorker(store, registry, owner="offline:test"))
    finally:
        await engine.dispose()


async def add_job(system, **overrides):
    async with system.sessions() as session:
        job = ScheduledJobRecord(**{
            "key": "test-job", "task_type": "test_echo", "params": {"value": 7},
            "cron_expr": "0 9 * * *", "enabled": True, "config_version": 1,
            "next_run_at": system.clock(), **overrides,
        })
        session.add(job)
        await session.commit()
        return job


async def execute_accepted(system, accepted):
    run = await system.service.get_run(accepted.run_id)
    await system.worker.execute(run.id, run.delivery_generation)
    return await system.service.get_run(run.id)
