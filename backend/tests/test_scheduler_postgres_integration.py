"""显式授权的开发 PostgreSQL 验证，不调用上游。

可用 --scheduler-configured-services 读取现有开发配置，或同时提供
RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST=1 与 SCHEDULER_TEST_DATABASE_URL。
每个测试创建随机 schema，只使用合成数据，子进程各自提交事务；
结束后关闭子进程并删除该 schema，不升级业务数据库。
"""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import multiprocessing
import os
import re
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agent_lab.config.scheduler import SchedulerSettings
from agent_lab.db.base import Base
from agent_lab.domain.write_scope import WriteRecoveryRequiredError
from agent_lab.domain.enums import ProcessingStatus
from agent_lab.domain.source_document import SourceDocument, SourceInfo
from agent_lab.domain.write_scope import DocumentDeletionPendingError
from agent_lab.models.document import DocumentRecord
from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.models.source import SourceRecord
from agent_lab.models.write_operation import DocumentDeletionRecord, WriteOperationRecord
from agent_lab.repositories.scheduled_job_repository import ScheduledJobRepository, ScheduledJobStore
from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository
from agent_lab.scheduler_maintenance import inspect_or_recover
from agent_lab.services.scheduled_job_service import ScheduledJobService
from agent_lab.services.scheduled_task_errors import ScheduledJobAlreadyRunningError, ScheduledJobEditBlockedError
from agent_lab.services.scheduler_runner import ScheduledJobRunner
from agent_lab.services.write_coordination import WriteCoordinator

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason="需要显式授权并设置开发 PostgreSQL DSN。",
)


def run(coroutine):
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)


def database(dsn, schema):
    assert re.fullmatch(r"scheduler_test_[0-9a-f]{32}", schema)
    engine = create_async_engine(
        dsn, poolclass=NullPool, hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000", "connect_timeout": 5},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def isolated_database():
    dsn = os.environ.get("SCHEDULER_TEST_DATABASE_URL")
    if not dsn or not dsn.startswith("postgresql+psycopg://"):
        pytest.fail("必须显式指定 SCHEDULER_TEST_DATABASE_URL（开发 PostgreSQL）。", pytrace=False)
    schema = f"scheduler_test_{uuid4().hex}"
    engine, sessions = database(dsn, schema)

    async def create():
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            tables = [model.__table__ for model in (SourceRecord, DocumentRecord, ScheduledJobRecord, JobRunRecord, WriteOperationRecord, DocumentDeletionRecord)]
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))

    async def cleanup():
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            await engine.dispose()

    try:
        run(create())
        yield SimpleNamespace(dsn=dsn, schema=schema, sessions=sessions)
    finally:
        run(cleanup())


@contextmanager
def child(target, *args):
    """只看护本测试创建的进程；超时失败时也不留下连接或后台写入。"""
    process = multiprocessing.get_context("spawn").Process(target=target, args=args)
    process.start()
    try:
        yield process
    finally:
        process.join(2)
        if process.is_alive():
            process.terminate()
            process.join(5)
        assert not process.is_alive()


def claim_worker(dsn, schema, job_id, trigger, gate, output):
    async def execute():
        engine, sessions = database(dsn, schema)
        try:
            output.put("ready")
            assert await asyncio.to_thread(gate.wait, 10)
            result = await ScheduledJobStore(sessions).claim_run(
                UUID(job_id), trigger_type=trigger, started_at=datetime.now(UTC), owner=f"test:{os.getpid()}", expected_version=1,
            )
            output.put("accepted" if result else "ignored")
        except ScheduledJobAlreadyRunningError:
            output.put("conflict")
        except Exception as exc:
            output.put(type(exc).__name__)
        finally:
            await engine.dispose()
    run(execute())


def refresh_worker(dsn, schema, commands, output):
    async def execute():
        engine, sessions = database(dsn, schema)
        runner = ScheduledJobRunner(
            store_factory=lambda: ScheduledJobStore(sessions), write_runtime_factory=lambda: None,
            settings=SchedulerSettings(_env_file=None, refresh_seconds=1),
        )
        try:
            await runner.start()
            output.put("ready")
            while await asyncio.to_thread(commands.get, True, 15) != "stop":
                # 不手动 refresh，让真实刷新循环从另一个进程读取已提交配置。
                output.put({str(job.id): job.args[1] for job in runner._scheduler.get_jobs()})
        except Exception as exc:
            output.put(type(exc).__name__)
        finally:
            await runner.close()
            await engine.dispose()
    run(execute())


def resource_worker(dsn, schema, resources, output, release, crash=False):
    async def execute():
        engine, sessions = database(dsn, schema)
        try:
            async with WriteCoordinator(sessions, poll_seconds=0.02).hold(resources):
                output.put("acquired")
                assert await asyncio.to_thread(release.wait, 10)
                if crash:
                    os._exit(0)
            output.put("released")
        except Exception as exc:
            output.put(type(exc).__name__)
        finally:
            await engine.dispose()
    run(execute())


def scheduler_http_app(sessions, runner):
    """保留真实 HTTP、Service、执行器及存储，只替换无关启动依赖和账号身份。"""
    from agent_lab.db.session import get_db_session
    from tests.app_helpers import create_offline_app
    from tests.auth_helpers import allow_superuser

    app = create_offline_app(scheduler_runner_builder=lambda _: runner)

    async def session_dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db_session] = session_dependency
    return allow_superuser(app)


def http_trigger_worker(dsn, schema, job_id, gate, release, output):
    async def execute():
        from agent_lab.services.news_pipeline_execution_service import NewsSyncExecutionResult

        engine, sessions = database(dsn, schema)

        class ControlledRuntime:
            async def sync_only(self, **params):
                output.put(("business", os.getpid(), params))
                assert await asyncio.to_thread(release.wait, 60)
                return NewsSyncExecutionResult(synchronized_count=1)

            async def close(self):
                pass

        runner = ScheduledJobRunner(
            store_factory=lambda: ScheduledJobStore(sessions), write_runtime_factory=ControlledRuntime,
            settings=SchedulerSettings(_env_file=None, shutdown_grace_seconds=60),
        )
        try:
            app = scheduler_http_app(sessions, runner)
            output.put(("ready", os.getpid(), None))
            assert await asyncio.to_thread(gate.wait, 30)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                response = await client.post(f"/scheduled-jobs/{job_id}/trigger")
                output.put(("response", response.status_code, response.json()))
            assert await asyncio.to_thread(release.wait, 60)
        except Exception as exc:
            output.put(("error", type(exc).__name__, None))
            raise RuntimeError(type(exc).__name__) from None
        finally:
            await runner.close()
            await engine.dispose()
            output.put(("closed", os.getpid(), None))
    run(execute())


async def create_job(db, *, enabled=True):
    async with db.sessions() as session:
        return await ScheduledJobRepository(session).create_job(
            key=f"synthetic-{uuid4().hex}", task_type="freshrss_sync",
            cron_expr="0 0 1 1 *", params={"limit_per_source": 1}, enabled=enabled,
        )


def test_two_api_workers_and_cron_have_one_claim(isolated_database):
    from contextlib import ExitStack
    db = isolated_database
    job = run(create_job(db))
    context = multiprocessing.get_context("spawn")
    gate, output = context.Event(), context.Queue()
    try:
        with ExitStack() as stack:
            for trigger in ("manual", "manual", "scheduled"):
                stack.enter_context(child(claim_worker, db.dsn, db.schema, str(job.id), trigger, gate, output))
            assert [output.get(timeout=15) for _ in range(3)] == ["ready"] * 3
            gate.set()
            assert sorted(output.get(timeout=15) for _ in range(3)) == ["accepted", "conflict", "conflict"]
    finally:
        output.close()
        output.join_thread()

    async def verify():
        async with db.sessions() as session:
            records = (await session.scalars(select(JobRunRecord))).all()
            assert len(records) == 1 and records[0].config_snapshot["config_version"] == 1
    run(verify())


def test_http_workers_conflict_and_cron_skip_preserve_receipt(isolated_database):
    from contextlib import ExitStack
    db = isolated_database
    context = multiprocessing.get_context("spawn")
    gate, release, output = context.Event(), context.Event(), context.Queue()

    async def verify():
        runner = ScheduledJobRunner(
            store_factory=lambda: ScheduledJobStore(db.sessions), write_runtime_factory=lambda: None,
            settings=SchedulerSettings(_env_file=None),
        )
        app = scheduler_http_app(db.sessions, runner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post("/scheduled-jobs", json={
                "key": f"http-{uuid4().hex}", "task_type": "freshrss_sync",
                "cron_expr": "0 0 1 1 *", "params": {"limit_per_source": 1}, "enabled": True,
            })
            assert response.status_code == 201
            job_id = response.json()["id"]
            with ExitStack() as stack:
                for _ in range(2):
                    stack.enter_context(child(http_trigger_worker, db.dsn, db.schema, job_id, gate, release, output))
                try:
                    for _ in range(2):
                        message = await asyncio.to_thread(output.get, True, 30)
                        assert message[0] == "ready"
                    gate.set()
                    messages = [await asyncio.to_thread(output.get, True, 30) for _ in range(3)]
                    responses = [message for message in messages if message[0] == "response"]
                    assert sorted(message[1] for message in responses) == [202, 409]
                    assert sum(message[0] == "business" for message in messages) == 1
                    receipt = next(message[2] for message in responses if message[1] == 202)
                    await runner._run_scheduled(UUID(job_id), 1)
                    response = await client.patch(f"/scheduled-jobs/{job_id}", json={"enabled": False})
                    assert response.status_code == 200 and response.json()["active_run"]["id"] == receipt["run_id"]
                    assert (await client.patch(f"/scheduled-jobs/{job_id}", json={"params": {"limit_per_source": 2}})).status_code == 409
                    assert (await client.delete(f"/scheduled-jobs/{job_id}")).status_code == 409
                finally:
                    release.set()
                for _ in range(2):
                    message = await asyncio.to_thread(output.get, True, 30)
                    assert message[0] == "closed"
            detail = await client.get(f"/scheduled-jobs/{job_id}/runs/{receipt['run_id']}")
            assert detail.status_code == 200 and detail.json()["status"] == "succeeded"
            assert detail.json()["stats"]["synchronized_document_count"] == 1
            runs = (await client.get(f"/scheduled-jobs/{job_id}/runs")).json()
            assert sorted(record["status"] for record in runs) == ["skipped", "succeeded"]
            assert all(record["finished_at"] for record in runs)
            updated = await client.patch(f"/scheduled-jobs/{job_id}", json={"params": {"limit_per_source": 2}})
            assert updated.status_code == 200 and updated.json()["enabled"] is False
            assert (await client.delete(f"/scheduled-jobs/{job_id}")).status_code == 204
        await runner.close()
    try:
        run(verify())
    finally:
        output.close()
        output.join_thread()


def test_configuration_refresh_crosses_process_boundary(isolated_database):
    import time
    db = isolated_database
    context = multiprocessing.get_context("spawn")
    commands, output = context.Queue(), context.Queue()
    try:
        with child(refresh_worker, db.dsn, db.schema, commands, output):
            assert output.get(timeout=15) == "ready"
            job = run(create_job(db))

            def observed(expected):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    commands.put("snapshot")
                    if output.get(timeout=5) == expected:
                        return
                    time.sleep(0.05)
                pytest.fail("独立 scheduler 未在刷新周期内应用配置")

            observed({str(job.id): 1})

            async def disable():
                async with db.sessions() as session:
                    await session.execute(update(ScheduledJobRecord).where(ScheduledJobRecord.id == job.id).values(enabled=False, config_version=2))
                    await session.commit()
            run(disable())
            observed({})
            commands.put("stop")
    finally:
        for queue in (commands, output):
            queue.close()
            queue.join_thread()


def test_claim_serializes_with_configuration_edit_and_protects_history(isolated_database):
    db = isolated_database
    context = multiprocessing.get_context("spawn")
    gate, output = context.Event(), context.Queue()
    job = run(create_job(db, enabled=False))

    async def verify():
        async with db.sessions() as session:
            repository = ScheduledJobRepository(session)
            locked = await repository.lock_job(job.id)
            with child(claim_worker, db.dsn, db.schema, str(job.id), "manual", gate, output):
                assert await asyncio.to_thread(output.get, True, 15) == "ready"
                gate.set()
                locked.params = {"limit_per_source": 7}
                locked.config_version = 2
                await session.commit()
                assert await asyncio.to_thread(output.get, True, 15) == "accepted"
        runner = ScheduledJobRunner(store_factory=lambda: ScheduledJobStore(db.sessions), write_runtime_factory=lambda: None, settings=SchedulerSettings(_env_file=None))
        async with db.sessions() as session:
            service = ScheduledJobService(session, runner)
            with pytest.raises(ScheduledJobEditBlockedError):
                await service.update_job(job.id, params={"limit_per_source": 8})
            await session.rollback()
            with pytest.raises(ScheduledJobAlreadyRunningError):
                await service.delete_job(job.id)
            await session.rollback()
            record = await session.scalar(select(JobRunRecord))
            assert record.config_snapshot["params"] == {"limit_per_source": 7}
            now = datetime.now(UTC)
            session.add_all([JobRunRecord(
                id=uuid4(), job_id=job.id, trigger_type="scheduled", status="skipped",
                started_at=now, finished_at=now, stats={},
            ) for _ in range(55)])
            await session.commit()
            await ScheduledJobRepository(session).prune_runs(job.id, keep=3)
            assert await session.get(JobRunRecord, record.id) is not None
            assert await session.scalar(select(func.count()).select_from(JobRunRecord)) == 4
    try:
        run(verify())
    finally:
        output.close()
        output.join_thread()


def test_crashed_owner_requires_explicit_recovery(isolated_database):
    db = isolated_database
    context = multiprocessing.get_context("spawn")
    output, release = context.Queue(), context.Event()
    try:
        with child(resource_worker, db.dsn, db.schema, ("index",), output, release, True) as process:
            assert output.get(timeout=15) == "acquired"
            release.set()
            process.join(5)
            assert process.exitcode == 0
    finally:
        output.close()
        output.join_thread()

    async def verify():
        async with db.sessions() as session:
            operation = await session.scalar(select(WriteOperationRecord))
            await session.execute(update(WriteOperationRecord).values(heartbeat_at=datetime.now(UTC) - timedelta(minutes=1)))
            await session.commit()
        with pytest.raises(WriteRecoveryRequiredError):
            async with WriteCoordinator(db.sessions).hold(("sync", "index")):
                pytest.fail("失联占用不能被自动抢走")
        result = await inspect_or_recover(db.sessions, operation_id=operation.id, confirm_stopped=True)
        assert result["released_operations"] == 1
        async with WriteCoordinator(db.sessions).hold(("sync", "index")):
            pass
        async with db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(WriteOperationRecord)) == 0
    run(verify())


def test_cleanup_waits_for_index_and_blocks_new_sync(isolated_database):
    db = isolated_database
    context = multiprocessing.get_context("spawn")
    output, release_cleanup = context.Queue(), context.Event()

    async def verify():
        coordinator = WriteCoordinator(db.sessions, poll_seconds=0.02)
        index_entered, release_index, sync_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def index():
            async with coordinator.hold(("index",)):
                index_entered.set()
                await release_index.wait()

        async def sync():
            async with coordinator.hold(("sync",)):
                sync_entered.set()

        async def wait_for_waiters(count):
            async with asyncio.timeout(30):
                while True:
                    async with db.sessions() as session:
                        waiting = await session.scalar(select(func.count()).select_from(WriteOperationRecord).where(WriteOperationRecord.status == "waiting"))
                    if waiting == count:
                        return
                    await asyncio.sleep(0.02)

        index_task = asyncio.create_task(index())
        sync_task = None
        signal = asyncio.create_task(index_entered.wait())
        try:
            # 开发库允许网络延迟；先传播参与者异常，避免把真实失败吞成等待超时。
            done, _ = await asyncio.wait((signal, index_task), timeout=30, return_when=asyncio.FIRST_COMPLETED)
            if index_task in done:
                await index_task
            assert signal in done, "索引写资源未在测试等待期限内取得"
            with child(resource_worker, db.dsn, db.schema, ("sync", "index"), output, release_cleanup):
                await wait_for_waiters(1)
                sync_task = asyncio.create_task(sync())
                await wait_for_waiters(2)
                assert not sync_entered.is_set()
                release_index.set()
                await index_task
                assert await asyncio.to_thread(output.get, True, 15) == "acquired"
                assert not sync_entered.is_set()
                release_cleanup.set()
                assert await asyncio.to_thread(output.get, True, 15) == "released"
                await asyncio.wait_for(sync_task, 30)
                assert sync_entered.is_set()
        finally:
            release_cleanup.set()
            release_index.set()
            for task in (signal, index_task, sync_task):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (signal, index_task, sync_task) if task), return_exceptions=True)
    try:
        run(verify())
    finally:
        output.close()
        output.join_thread()


async def seed_documents(sessions, dates_and_statuses):
    """只创建合成来源与 Document；调用方的 schema 完全隔离。"""
    async with sessions() as session:
        source = SourceRecord(id=uuid4(), provider="synthetic", external_id=uuid4().hex, name="Synthetic")
        session.add(source)
        await session.flush()
        documents = [DocumentRecord(
            id=uuid4(), source_id=source.id, external_id=str(index), title="Synthetic",
            url="https://example.invalid/test", content_text="synthetic body", content_hash="a" * 64,
            published_at=published, created_at=created, processing_status=status,
            index_revision=1, indexed_revision=1 if status == ProcessingStatus.INDEXED else None,
        ) for index, (published, created, status) in enumerate(dates_and_statuses)]
        session.add_all(documents)
        await session.commit()
        return documents


def test_retention_boundaries_pending_guards_and_conditional_finish(isolated_database):
    db = isolated_database

    async def verify():
        cutoff = datetime(2026, 3, 9, tzinfo=UTC)
        old, recent = cutoff - timedelta(microseconds=1), cutoff + timedelta(days=1)
        documents = await seed_documents(db.sessions, [
            (old, recent, ProcessingStatus.INDEXED),
            (None, old, ProcessingStatus.INDEXED),
            (cutoff, old, ProcessingStatus.INDEXED),
            (recent, old, ProcessingStatus.INDEXED),
            *((old, old, status) for status in (ProcessingStatus.PENDING, ProcessingStatus.PROCESSING, ProcessingStatus.FAILED)),
        ])
        async with db.sessions() as session:
            repository = DocumentRetentionRepository(session)
            first = await repository.candidates(cutoff, None, 1)
            second = await repository.candidates(cutoff, first[-1], 1)
            assert {candidate.document_id for candidate in first + second} == {item.id for item in documents[:2]}
            assert await repository.candidates(cutoff, second[-1], 1) == []
            intents = await repository.prepare(first + second, cutoff)
            target = documents[0]
            source = await session.get(SourceRecord, target.source_id)
            incoming = SourceDocument(external_id=target.external_id, title="Changed", url=target.url, content_text="new body", source=SourceInfo(provider=source.provider, external_id=source.external_id, name=source.name))
            with pytest.raises(DocumentDeletionPendingError):
                await DocumentRepository(session).upsert(incoming, source_id=target.source_id)
            await session.rollback()
            # 模拟一个绕过正式入口的状态/版本变化，验证恢复仍不会盲删。
            await session.execute(update(DocumentRecord).where(DocumentRecord.id == target.id).values(index_revision=2, processing_status=ProcessingStatus.FAILED))
            await session.commit()
            assert target.id not in await DocumentRepository(session).list_index_candidate_ids(limit=20)
            assert not await DocumentRepository(session).claim_for_indexing(document_id=target.id, expected_revision=2)
            with pytest.raises(RuntimeError):
                await repository.verify(intents)
            with pytest.raises(RuntimeError):
                await repository.finish(intents)
            await session.rollback()
            assert await session.get(DocumentRecord, target.id) is not None
            assert await session.scalar(select(func.count()).select_from(DocumentDeletionRecord)) == 2
    run(verify())
