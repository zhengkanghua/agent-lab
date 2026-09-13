"""显式授权的开发 PostgreSQL 验证，不调用上游。

可用 --scheduler-configured-services 读取现有开发配置，或同时提供
RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST=1 与 SCHEDULER_TEST_DATABASE_URL。
每个测试创建随机 schema，只使用合成数据，子进程各自提交事务；
结束后关闭子进程并删除该 schema，不升级业务数据库。
"""

import asyncio
from contextlib import asynccontextmanager, contextmanager, nullcontext
from dataclasses import replace
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
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.models.source import SourceRecord
from agent_lab.models.write_operation import DocumentDeletionRecord, WriteOperationRecord
from agent_lab.repositories.scheduled_job_repository import ScheduledJobRepository
from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.knowledge.adapters.importing import PostgresImportDocumentRepository
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentVersion
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository
from agent_lab.scheduler_maintenance import inspect_or_recover
from agent_lab.services.scheduled_job_service import ScheduledJobService
from agent_lab.tasks.service import TaskService
from agent_lab.tasks.repository import TaskStore
from agent_lab.tasks.registry import TaskRegistry
from agent_lab.tasks.worker import TaskWorker
from agent_lab.tasks.cron import CronSchedule
from agent_lab.tasks.contracts import TaskError, TaskOverlap, ExecutionPolicy
from agent_lab.models.scheduled_job import TaskPolicyRecord
from agent_lab.services.scheduled_task_registry import TASK_TYPE_SPECS
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
        connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=10000 -ctimezone=UTC", "connect_timeout": 5},
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
            # 循环引用的当前版本、处理记录及审核账号均建在随机 schema 内。
            import agent_lab.models  # noqa: F401
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(TaskPolicyRecord.__table__.insert().values(id=1,
                policy=ExecutionPolicy().model_dump(), updated_at=datetime.now(UTC), updated_by="test"))
            await connection.execute(KnowledgeBaseRecord.__table__.insert().values(
                id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID, key="news", name="新闻", is_active=True,
            ))

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


async def probe(_runtime, params):
    return {"value": params["limit_per_source"]}


def registry(execute=probe):
    return TaskRegistry(replace(spec, execute=execute, execution_scope=lambda *_: nullcontext())
                        for spec in TASK_TYPE_SPECS.values())


def service(sessions, *, clock=None):
    return TaskService(sessions, registry(), clock=clock)


def claim_worker(dsn, schema, job_id, request_key, gate, output):
    async def execute():
        engine, sessions = database(dsn, schema)
        try:
            output.put("ready")
            assert await asyncio.to_thread(gate.wait, 20)
            accepted = await service(sessions).trigger(UUID(job_id), actor="test:user",
                request_key=request_key or f"worker:{os.getpid()}")
            output.put(("accepted", str(accepted.run_id)))
        except TaskOverlap as error:
            output.put(("conflict", str(error.run_id)))
        except Exception as error:
            output.put(("error", type(error).__name__))
        finally:
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



def scheduler_http_app(sessions):
    """真实路由、受理和查询，只替换身份及无关的启动依赖。"""
    from agent_lab.db.session import get_db_session
    from tests.app_helpers import FakeSearchRuntime, create_offline_app
    from tests.auth_helpers import allow_superuser
    app = create_offline_app(runtime_factory=FakeSearchRuntime, task_service_factory=lambda: service(sessions))
    async def session_dependency():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_db_session] = session_dependency
    return allow_superuser(app)


def execute_worker(dsn, schema, run_id, gate, release, output):
    async def execute():
        engine, sessions = database(dsn, schema)
        async def business(_runtime, params):
            output.put(("business", params))
            assert await asyncio.to_thread(release.wait, 30)
            return {"value": params["limit_per_source"]}
        try:
            output.put(("ready", None))
            assert await asyncio.to_thread(gate.wait, 20)
            await TaskWorker(TaskStore(sessions), registry(business), owner=f"test:{os.getpid()}").execute(UUID(run_id), 1)
            output.put(("done", None))
        finally:
            await engine.dispose()
    run(execute())


async def create_job(db, *, enabled=True):
    async with db.sessions() as session:
        return await ScheduledJobRepository(session).create_job(
            key=f"synthetic-{uuid4().hex}", task_type="freshrss_sync",
            cron_expr="0 0 1 1 *", params={"limit_per_source": 1}, enabled=enabled,
        )


@pytest.mark.parametrize("same_request", [False, True])
def test_parallel_intake_uses_database_identity_and_one_active_slot(isolated_database, same_request):
    from contextlib import ExitStack
    db = isolated_database
    job = run(create_job(db))
    context = multiprocessing.get_context("spawn")
    gate, output = context.Event(), context.Queue()
    try:
        with ExitStack() as stack:
            for _ in range(3):
                stack.enter_context(child(claim_worker, db.dsn, db.schema, str(job.id),
                    "same" if same_request else None, gate, output))
            assert [output.get(timeout=30) for _ in range(3)] == ["ready"] * 3
            gate.set()
            results = [output.get(timeout=30) for _ in range(3)]
            assert sorted(item[0] for item in results) == (["accepted"] * 3 if same_request else ["accepted", "conflict", "conflict"])
            assert len({item[1] for item in results}) == 1
        async def verify():
            async with db.sessions() as session:
                records = (await session.scalars(select(JobRunRecord))).all()
                assert len(records) == 1 and records[0].attempts == 0
        run(verify())
    finally:
        output.close()
        output.join_thread()


def test_http_receipt_survives_edit_delete_and_duplicate_worker_messages(isolated_database):
    from contextlib import ExitStack
    db = isolated_database
    context = multiprocessing.get_context("spawn")
    gate, release, output = context.Event(), context.Event(), context.Queue()
    async def verify():
        app = scheduler_http_app(db.sessions)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post("/scheduled-jobs", json={
                "key": f"http-{uuid4().hex}", "task_type": "freshrss_sync", "cron_expr": "0 0 1 1 *",
                "params": {"limit_per_source": 1}, "enabled": True,
            })
            assert created.status_code == 201
            job_id = UUID(created.json()["id"])
            accepted = await client.post(f"/scheduled-jobs/{job_id}/trigger", headers={"Idempotency-Key": "first"})
            assert accepted.status_code == 202 and accepted.json()["status"] == "queued"
            identity = accepted.json()["run_id"]
            conflict = await client.post(f"/scheduled-jobs/{job_id}/trigger", headers={"Idempotency-Key": "second"})
            assert conflict.status_code == 409 and conflict.json()["run_id"] == identity
            planned = datetime.now(UTC)
            async with db.sessions() as session:
                await session.execute(update(ScheduledJobRecord).where(ScheduledJobRecord.id == job_id).values(next_run_at=planned))
                await session.commit()
            skipped = await service(db.sessions, clock=lambda: planned).scheduled(job_id, 1, planned, CronSchedule())
            assert skipped.status == "skipped"
            edited = await client.patch(f"/scheduled-jobs/{job_id}", json={"params": {"limit_per_source": 9}, "enabled": False})
            assert edited.status_code == 200 and edited.json()["active_run"]["id"] == identity
            assert (await client.delete(f"/scheduled-jobs/{job_id}")).status_code == 204
            with ExitStack() as stack:
                for _ in range(2):
                    stack.enter_context(child(execute_worker, db.dsn, db.schema, identity, gate, release, output))
                assert [await asyncio.to_thread(output.get, True, 30) for _ in range(2)] == [("ready", None)] * 2
                gate.set()
                messages = [await asyncio.to_thread(output.get, True, 30) for _ in range(2)]
                assert sorted(item[0] for item in messages) == ["business", "done"]
                assert next(item[1] for item in messages if item[0] == "business") == {"limit_per_source": 1}
                release.set()
                assert (await asyncio.to_thread(output.get, True, 30))[0] == "done"
            detail = (await client.get(f"/task-runs/{identity}")).json()
            assert detail["status"] == "succeeded" and detail["attempts"] == 1
            assert detail["job_id"] is None and detail["source_job_id"] == str(job_id)
            assert detail["stats"] == {"value": 1}
            repeated = await client.post(f"/scheduled-jobs/{job_id}/trigger", headers={"Idempotency-Key": "first"})
            assert repeated.status_code == 202 and repeated.json()["run_id"] == identity
    try:
        run(verify())
    finally:
        release.set()
        output.close()
        output.join_thread()


def test_claim_serializes_with_configuration_edit(isolated_database):
    db = isolated_database
    context = multiprocessing.get_context("spawn")
    gate, output = context.Event(), context.Queue()
    job = run(create_job(db, enabled=False))
    async def verify():
        async with db.sessions() as session:
            locked = await ScheduledJobRepository(session).lock_job(job.id)
            with child(claim_worker, db.dsn, db.schema, str(job.id), "edit-race", gate, output):
                assert await asyncio.to_thread(output.get, True, 30) == "ready"
                gate.set()
                locked.params, locked.config_version = {"limit_per_source": 7}, 2
                await session.commit()
                result = await asyncio.to_thread(output.get, True, 30)
                assert result[0] == "accepted"
        run_record = await service(db.sessions).get_run(UUID(result[1]))
        assert run_record.config_snapshot["params"] == {"limit_per_source": 7}
        assert run_record.config_snapshot["config_version"] == 2
    try:
        run(verify())
    finally:
        output.close()
        output.join_thread()


async def change_configuration(session, job_id, action):
    """通过配置用例修改，保持生产校验、版本推进和提交行为。"""
    jobs = ScheduledJobService(session, CronSchedule(), registry())
    if action == "delete":
        await jobs.delete_job(job_id)
    elif action == "disable":
        await jobs.update_job(job_id, enabled=False)
    else:
        await jobs.update_job(job_id, params={"limit_per_source": 7}, cron_expr="30 9 * * *")


@asynccontextmanager
async def existing_session(session):
    """测试外层持有连接，用它确定两个真实事务的先后；不替换 SQL 或提交。"""
    yield session


def scheduled_configuration_worker(dsn, schema, job_id, planned, action, output):
    async def execute():
        engine, sessions = database(dsn, schema)
        try:
            async with sessions() as session:
                output.put(await session.scalar(text("SELECT pg_backend_pid()")))
                if action == "scheduled":
                    tasks = service(lambda: existing_session(session), clock=lambda: planned)
                    accepted = await tasks.scheduled(UUID(job_id), 1, planned, CronSchedule())
                    output.put(("accepted", str(accepted.run_id)) if accepted else ("ignored", None))
                else:
                    await change_configuration(session, UUID(job_id), action)
                    output.put(("changed", None))
        except Exception as error:
            output.put(("error", type(error).__name__))
        finally:
            await engine.dispose()
    run(execute())


@pytest.mark.parametrize("action", ["edit", "disable", "delete"])
@pytest.mark.parametrize("scheduled_first", [False, True], ids=["configuration-first", "scheduled-first"])
def test_scheduled_intake_serializes_with_configuration_changes(isolated_database, action, scheduled_first):
    """确认另一事务已在数据库等待后才提交，覆盖改／停／删与周期受理的两种先后。"""
    db = isolated_database
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    planned = datetime.now(UTC)

    async def verify():
        job = await create_job(db)
        async with db.sessions() as session:
            await session.execute(update(ScheduledJobRecord).where(ScheduledJobRecord.id == job.id).values(next_run_at=planned))
            await session.commit()
            await ScheduledJobRepository(session).lock_job(job.id)
            blocker = await session.scalar(text("SELECT pg_backend_pid()"))
            second_action = action if scheduled_first else "scheduled"
            with child(scheduled_configuration_worker, db.dsn, db.schema, str(job.id), planned, second_action, output):
                waiting = await asyncio.to_thread(output.get, True, 30)
                assert isinstance(waiting, int)
                # 观察 PostgreSQL 的真实锁等待，不靠睡眠猜测另一个请求已经抵达。
                async with asyncio.timeout(15), db.sessions() as observer:
                    while not await observer.scalar(text("SELECT :blocker = ANY(pg_blocking_pids(:waiting))"),
                            {"blocker": blocker, "waiting": waiting}):
                        await asyncio.sleep(0.02)
                if scheduled_first:
                    tasks = service(lambda: existing_session(session), clock=lambda: planned)
                    accepted = await tasks.scheduled(job.id, 1, planned, CronSchedule())
                    assert accepted is not None and accepted.status == "queued"
                else:
                    await change_configuration(session, job.id, action)
                assert await asyncio.to_thread(output.get, True, 30) == (
                    ("changed", None) if scheduled_first else ("ignored", None)
                )

        tasks = service(db.sessions, clock=lambda: planned)
        # 两种顺序都不能因旧回调再次出现而增加执行。
        assert await tasks.scheduled(job.id, 1, planned, CronSchedule()) is None
        records = await tasks.list_runs(source_job_id=job.id)
        assert len(records) == (1 if scheduled_first else 0)
        if scheduled_first:
            retained = await tasks.get_run(accepted.run_id)
            assert retained.config_snapshot["params"] == {"limit_per_source": 1}
            assert retained.config_snapshot["config_version"] == 1
            assert retained.source_job_id == job.id and retained.scheduled_for == planned
            assert retained.job_id == (None if action == "delete" else job.id)
            await TaskWorker(TaskStore(db.sessions), registry(), owner="test:after-config").execute(retained.id, 1)
            completed = await tasks.get_run(retained.id)
            assert completed.status == "succeeded" and completed.stats == {"value": 1}
        async with db.sessions() as session:
            changed = await session.get(ScheduledJobRecord, job.id)
            if action == "delete":
                assert changed is None
            else:
                assert changed.config_version == 2
                if action == "disable":
                    assert not changed.enabled and changed.next_run_at is None
                else:
                    assert changed.params == {"limit_per_source": 7} and changed.cron_expr == "30 9 * * *"
                    assert changed.next_run_at > planned
    try:
        run(verify())
    finally:
        output.close()
        output.join_thread()


def start_cancel_worker(dsn, schema, run_id, token, action, gate, output):
    async def execute():
        engine, sessions = database(dsn, schema)
        try:
            output.put("ready")
            assert await asyncio.to_thread(gate.wait, 20)
            if action == "start":
                started = await TaskStore(sessions).start(UUID(run_id), UUID(token))
                output.put("started" if started else "not-started")
            else:
                try:
                    await service(sessions).cancel(UUID(run_id))
                    output.put("cancelled")
                except TaskError:
                    output.put("cancel-conflict")
        finally:
            await engine.dispose()
    run(execute())


def test_start_and_cancel_compete_for_same_database_state(isolated_database):
    from contextlib import ExitStack
    db = isolated_database
    context = multiprocessing.get_context("spawn")
    gate, output = context.Event(), context.Queue()
    async def accepted_claim():
        job = await create_job(db)
        accepted = await service(db.sessions).trigger(job.id, actor="test:user", request_key="cancel")
        return await TaskStore(db.sessions).claim(accepted.run_id, 1, "preparing")
    claim = run(accepted_claim())
    try:
        with ExitStack() as stack:
            for action in ("start", "cancel"):
                stack.enter_context(child(start_cancel_worker, db.dsn, db.schema, str(claim.id),
                    str(claim.claim_token), action, gate, output))
            assert [output.get(timeout=30) for _ in range(2)] == ["ready", "ready"]
            gate.set()
            assert set(output.get(timeout=30) for _ in range(2)) in (
                {"started", "cancel-conflict"}, {"not-started", "cancelled"},
            )
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


async def seed_documents(sessions, dates_and_statuses, *, knowledge_base_id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID):
    """只创建合成来源与 Document；调用方的 schema 完全隔离。"""
    async with sessions() as session:
        source = SourceRecord(id=uuid4(), provider="synthetic", external_id=uuid4().hex, name="Synthetic", knowledge_base_id=knowledge_base_id)
        session.add(source)
        await session.flush()
        documents = [DocumentRecord(
            id=uuid4(), knowledge_base_id=knowledge_base_id, source_id=source.id, external_id=str(index), title="Synthetic",
            url="https://example.invalid/test", content_text="synthetic body", content_hash="a" * 64,
            published_at=published, created_at=created, processing_status=status,
            index_revision=1, indexed_revision=1 if status == ProcessingStatus.INDEXED else None,
        ) for index, (published, created, status) in enumerate(dates_and_statuses)]
        session.add_all(documents)
        await session.flush()
        # 本夹具只服务保留期：构造已采用关系，不运行无关的解析与模型调用。
        for document in documents:
            if document.processing_status != ProcessingStatus.INDEXED:
                continue
            processing_id, version_id, instance_id = uuid4(), uuid4(), uuid4()
            session.add(DocumentProcessingRecord(id=processing_id, document_id=document.id,
                source_kind="freshrss", state="adopted", index_instance_id=instance_id))
            await session.flush()
            session.add(DocumentVersion(id=version_id, document_id=document.id, processing_id=processing_id,
                revision=1, title=document.title, mime_type="text/plain", content_text=document.content_text,
                content_hash=document.content_hash, parsed_document={}, chunk_result={}, processing_spec={}))
            await session.flush()
            document.current_version_id, document.current_index_instance_id = version_id, instance_id
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
            scope = (DEFAULT_NEWS_KNOWLEDGE_BASE_ID,)
            first = await repository.candidates(cutoff, None, 1, knowledge_base_ids=scope)
            second = await repository.candidates(cutoff, first[-1], 1, knowledge_base_ids=scope)
            assert {candidate.document_id for candidate in first + second} == {item.id for item in documents[:2]}
            assert await repository.candidates(cutoff, second[-1], 1, knowledge_base_ids=scope) == []
            intents = await repository.prepare(first + second, cutoff)
            target = documents[0]
            target_id = target.id
            source = await session.get(SourceRecord, target.source_id)
            incoming = SourceDocument(external_id=target.external_id, title="Changed", url=target.url, raw_bytes=b"new body", source=SourceInfo(provider=source.provider, external_id=source.external_id, name=source.name))
            with pytest.raises(DocumentDeletionPendingError):
                await PostgresImportDocumentRepository(session).prepare(incoming, source_id=target.source_id, knowledge_base_id=target.knowledge_base_id)
            await session.rollback()
            await repository.mark_qdrant_deleted(intents)
            # 模拟一个绕过正式入口的状态/版本变化，验证恢复仍不会盲删。
            await session.execute(update(DocumentRecord).where(DocumentRecord.id == target_id).values(index_revision=2, processing_status=ProcessingStatus.FAILED))
            await session.commit()
            assert await DocumentRepository(session).get_with_source(target_id) is None
            with pytest.raises(ProcessingApplicationError, match="document_deletion_conflict"):
                await repository.verify(intents)
            with pytest.raises(ProcessingApplicationError, match="document_deletion_conflict"):
                await repository.finish(intents)
            await session.rollback()
            assert await session.get(DocumentRecord, target_id) is not None
            assert await session.scalar(select(func.count()).select_from(DocumentDeletionRecord)) == 2
    run(verify())
