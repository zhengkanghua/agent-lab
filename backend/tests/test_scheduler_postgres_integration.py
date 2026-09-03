"""显式启用后验证定时任务模块对真实 PostgreSQL 与上游服务的端到端行为。

两个用例：
1. 迁移种子：``alembic upgrade head`` 之后库里应有两条启用的种子任务；
2. 端到端执行：在可回滚的外层事务（savepoint）里插入两条测试任务，用真实写 Runtime
   执行 ``freshrss_sync`` 与 ``index_pending`` 各一轮，断言执行历史落库且统计合理。
   测试结束后外层事务回滚，本测试插入的任务与历史不残留。

访问范围：PostgreSQL（连接由 .env 提供）、FreshRSS、Ollama、Qdrant；只发送小批量
有界请求，不打印密钥、正文或完整向量。
"""

import asyncio
import os
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_lab.config.freshrss import get_freshrss_settings
from agent_lab.config.ollama_embedding import get_ollama_embedding_settings
from agent_lab.config.qdrant import get_qdrant_settings
from agent_lab.config.scheduler import SchedulerSettings
from agent_lab.db.session import engine
from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.repositories.scheduled_job_repository import ScheduledJobRepository, ScheduledJobStore
from agent_lab.services.scheduler_runner import ScheduledJobRunner


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason=(
        "set RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST=1 to verify the scheduler "
        "against the configured PostgreSQL, FreshRSS, Ollama and Qdrant services"
    ),
)


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


async def wait_terminal(store: ScheduledJobStore, run_id: Any, *, timeout: float = 300.0) -> Any:
    """轮询执行记录到终态；真实同步/索引以分钟计，超时放宽到 5 分钟。"""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        record = await store.get_run(run_id)
        if record is not None and record.status != "running":
            return record
        await asyncio.sleep(0.5)
    raise AssertionError("执行记录在超时内未进入终态")


class ReadingStore(ScheduledJobStore):
    """在 Store 上补一个测试用的按 id 读取（生产代码不需要这个读法）。"""

    async def get_run(self, run_id: Any) -> JobRunRecord | None:
        async with self._session() as session:
            return await session.get(JobRunRecord, run_id)


def test_seed_jobs_exist_and_are_enabled() -> None:
    """迁移执行后必须存在两条启用的种子任务，cron 与参数和 spec 一致。"""

    async def verify() -> None:
        async with engine.connect() as connection:
            result = await connection.execute(
                select(
                    ScheduledJobRecord.key,
                    ScheduledJobRecord.task_type,
                    ScheduledJobRecord.cron_expr,
                    ScheduledJobRecord.enabled,
                ).order_by(ScheduledJobRecord.key)
            )
            rows = {row.key: row for row in result.all()}
        assert {"freshrss-sync", "index-pending"} <= set(rows)
        sync_row = rows["freshrss-sync"]
        assert sync_row.task_type == "freshrss_sync"
        assert sync_row.cron_expr == "*/10 * * * *"
        assert sync_row.enabled is True
        index_row = rows["index-pending"]
        assert index_row.task_type == "index_pending"
        assert index_row.cron_expr == "*/5 * * * *"
        assert index_row.enabled is True

    run(verify())


def test_runner_executes_both_task_types_end_to_end() -> None:
    """真实执行两类任务各一轮：历史落库、统计合理，外层事务回滚不留残留。"""

    async def verify() -> None:
        connection = await engine.connect()
        outer_transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            # 1、在外层事务的 savepoint 里插入两条测试任务（绝不写真实业务任务）。
            async with factory() as session:
                repository = ScheduledJobRepository(session)
                sync_job = await repository.create_job(
                    key=f"it-sync-{uuid4().hex[:8]}",
                    task_type="freshrss_sync",
                    cron_expr="*/10 * * * *",
                    params={"limit_per_source": 1},
                    enabled=True,
                )
                index_job = await repository.create_job(
                    key=f"it-index-{uuid4().hex[:8]}",
                    task_type="index_pending",
                    cron_expr="*/5 * * * *",
                    params={"batch_size": 1, "stale_after_minutes": 1},
                    enabled=True,
                )

            def write_runtime_factory() -> PipelineWriteRuntime:
                return PipelineWriteRuntime.build(
                    session_factory=factory,
                    freshrss_settings=get_freshrss_settings(),
                    qdrant_settings=get_qdrant_settings(),
                    ollama_settings=get_ollama_embedding_settings(),
                )

            store = ReadingStore(factory)
            runner = ScheduledJobRunner(
                store_factory=lambda: store,
                write_runtime_factory=write_runtime_factory,
                settings=SchedulerSettings(timezone="Asia/Shanghai"),
            )

            # 2、真实跑一轮 FreshRSS 同步（走 .env 配置的真实 FreshRSS）。
            async with factory() as session:
                sync_record = await ScheduledJobRepository(session).get_job(sync_job.id)
                assert sync_record is not None
            sync_run_id = await runner.trigger_now(sync_record)
            sync_result = await wait_terminal(store, sync_run_id)
            assert sync_result.status == "succeeded", sync_result.error_type
            assert sync_result.stats["source_count"] >= 0

            # 3、真实跑一轮待索引批次（走真实 Ollama 与 Qdrant；无候选时统计为 0 也算成功）。
            async with factory() as session:
                index_record = await ScheduledJobRepository(session).get_job(index_job.id)
                assert index_record is not None
            index_run_id = await runner.trigger_now(index_record)
            index_result = await wait_terminal(store, index_run_id)
            assert index_result.status == "succeeded", index_result.error_type
            assert index_result.stats["candidate_count"] <= 1

            # 4、savepoint 内可查到执行历史；外层回滚后一切不残留。
            async with factory() as session:
                repository = ScheduledJobRepository(session)
                assert await repository.count_runs(sync_job.id) >= 1
                assert await repository.count_runs(index_job.id) >= 1
        finally:
            await outer_transaction.rollback()
            await connection.close()

    run(verify())
