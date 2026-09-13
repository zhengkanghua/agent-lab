"""从真实旧结构升级公共任务，默认跳过；只写并清理随机 PostgreSQL schema。"""

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import MetaData, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from agent_lab.models.scheduled_job import JobRunRecord, TaskPolicyRecord
from agent_lab.models.write_operation import DocumentDeletionRecord, WriteOperationRecord
from agent_lab.tasks.contracts import ExecutionPolicy, RetryUnavailable, TaskDetailsExpired
from agent_lab.tasks.repository import TaskStore
from tests.test_scheduler_postgres_integration import database, run, service

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason="需要显式授权并设置开发 PostgreSQL DSN。",
)

PREVIOUS = "a91b3c7d5e20"
CURRENT = "b6e2f9047a31"


@pytest.fixture
def old_database():
    """执行已有迁移生成旧表，避免 create_all 掩盖真实约束名称与升级问题。"""
    dsn = os.environ.get("SCHEDULER_TEST_DATABASE_URL", "")
    if not dsn.startswith("postgresql+psycopg://"):
        pytest.fail("必须提供 SCHEDULER_TEST_DATABASE_URL（允许随机 schema 的测试 PostgreSQL）。", pytrace=False)
    schema = f"scheduler_test_{uuid4().hex}"
    engine, sessions = database(dsn, schema)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    metadata = MetaData()

    def upgrade(connection, revision=CURRENT):
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    async def create():
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(upgrade, PREVIOUS)
            await connection.run_sync(metadata.reflect)

    async def cleanup():
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            await engine.dispose()

    try:
        run(create())
        yield SimpleNamespace(engine=engine, sessions=sessions, tables=metadata.tables, upgrade=upgrade)
    finally:
        run(cleanup())


async def legacy_run(connection, tables, *, status, snapshot=None, stats=None, job_id=None):
    """每种历史场景默认使用独立配置，只有拒绝升级用例刻意共享配置。"""
    if job_id is None:
        job_id = uuid4()
        await connection.execute(tables["scheduled_jobs"].insert().values(
            id=job_id, key=f"legacy-{job_id.hex}", task_type="freshrss_sync",
            cron_expr="0 9 * * *", params={"limit_per_source": 99}, enabled=True,
        ))
    identity = uuid4()
    started = datetime(2026, 8, 1, tzinfo=UTC)
    await connection.execute(tables["scheduled_job_runs"].insert().values(
        id=identity, job_id=job_id, trigger_type="manual", status=status,
        started_at=started, finished_at=None if status in ("running", "skipped") else started + timedelta(seconds=2),
        stats=stats or {}, config_snapshot=snapshot or {}, owner="legacy-process", heartbeat_at=started,
    ))
    return identity, job_id


def test_upgrade_preserves_history_recovery_and_new_request_constraints(old_database):
    async def verify():
        env = old_database
        async with env.engine.begin() as connection:
            absent, _ = await legacy_run(connection, env.tables, status="failed")
            saved_snapshot = {"task_type": "freshrss_sync", "params": {"limit_per_source": 2}, "key": "at-acceptance"}
            saved, saved_job = await legacy_run(connection, env.tables, status="failed", snapshot=saved_snapshot)
            running, _ = await legacy_run(connection, env.tables, status="running", snapshot=saved_snapshot)
            occupied, _ = await legacy_run(connection, env.tables, status="failed", snapshot=saved_snapshot)
            flagged, _ = await legacy_run(connection, env.tables, status="failed", stats={"needs_attention": True})
            skipped, _ = await legacy_run(connection, env.tables, status="skipped")
            operation_id, deletion_id = uuid4(), uuid4()
            await connection.execute(env.tables["write_operations"].insert().values(
                id=operation_id, run_id=occupied, resources=["sync", "index"], owner="legacy-process",
                status="uncertain", started_at=datetime.now(UTC), heartbeat_at=datetime.now(UTC),
            ))
            await connection.execute(env.tables["document_deletions"].insert().values(
                document_id=deletion_id, revision=7, cutoff_date=datetime.now(UTC), retention_date=datetime.now(UTC),
                qdrant_deleted=True, objects=[{"key": "synthetic/original", "deleted": False}],
            ))
            jobs_query = select(env.tables["scheduled_jobs"]).order_by(env.tables["scheduled_jobs"].c.id)
            jobs_before = (await connection.execute(jobs_query)).mappings().all()
            evidence_before = (await connection.execute(select(env.tables["document_deletions"]))).mappings().one()

        async with env.engine.begin() as connection:
            await connection.run_sync(env.upgrade)
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT
            assert (await connection.execute(jobs_query)).mappings().all() == jobs_before
            assert (await connection.execute(select(env.tables["document_deletions"]))).mappings().one() == evidence_before

        tasks = service(env.sessions)
        missing = await tasks.get_run(absent)
        assert missing.config_snapshot == {} and missing.recovery["legacy_parameters_unavailable"] is True
        with pytest.raises(RetryUnavailable):
            await tasks.retry(absent, actor="test:migration", request_key="no-invented-parameters")
        retained = await tasks.get_run(saved)
        assert retained.config_snapshot == saved_snapshot and retained.source_job_id == saved_job
        assert retained.expires_at == retained.finished_at + timedelta(days=30)
        for identity in (running, occupied, flagged):
            uncertain = await tasks.get_run(identity)
            assert uncertain.status == "needs_attention" and uncertain.finished_at is None and uncertain.expires_at is None
            assert uncertain.wait_reason and uncertain.owner == "legacy-process"
        skipped_run = await tasks.get_run(skipped)
        assert skipped_run.attempts == 0 and skipped_run.finished_at == skipped_run.started_at

        async with env.sessions() as session:
            operation = await session.get(WriteOperationRecord, operation_id)
            assert operation.resources == ["sync", "index"] and operation.claim_token is None
            assert (await session.get(TaskPolicyRecord, 1)).policy == ExecutionPolicy().model_dump()
            await session.execute(env.tables["scheduled_jobs"].delete().where(env.tables["scheduled_jobs"].c.id == saved_job))
            await session.commit()
        retained = await tasks.get_run(saved)
        assert retained.job_id is None and retained.source_job_id == saved_job and retained.config_snapshot == saved_snapshot
        await TaskStore(env.sessions, clock=lambda: datetime(2027, 1, 1, tzinfo=UTC)).prune_history()
        async with env.sessions() as session:
            assert await session.get(DocumentDeletionRecord, deletion_id) is not None
            assert await session.get(WriteOperationRecord, operation_id) is not None
            assert (await tasks.get_run(occupied)).status == "needs_attention"
        # 在同一份成功升级的旧数据上验证新写入，避免为相同升级再建一遍环境。
        received = await tasks.submit("freshrss_sync", {"limit_per_source": 2}, actor="test:migration", request_key="original")
        assert received.status == "queued"
        await tasks.cancel(received.run_id)
        original = await tasks.get_run(received.run_id)
        await TaskStore(env.sessions, clock=lambda: original.expires_at + timedelta(seconds=1)).prune_history()
        replay = await tasks.submit("freshrss_sync", {"limit_per_source": 2}, actor="test:migration", request_key="original")
        assert replay.run_id == received.run_id and replay.details_expired
        with pytest.raises(TaskDetailsExpired):
            await tasks.get_run(received.run_id)

        # 直接绕过 Service 写表，确认并发安全由迁移后的数据库约束保护。
        now, source_id = datetime.now(UTC), uuid4()
        values = dict(task_type="freshrss_sync", task_version=1, trigger_type="scheduled", actor="test:migration",
            status="queued", accepted_at=now, available_at=now, dispatch_after=now, policy_snapshot=ExecutionPolicy().model_dump())
        async with env.engine.begin() as connection:
            await connection.execute(JobRunRecord.__table__.insert().values(id=uuid4(), source_job_id=source_id, **values))
        with pytest.raises(IntegrityError):
            async with env.engine.begin() as connection:
                await connection.execute(JobRunRecord.__table__.insert().values(id=uuid4(), source_job_id=source_id, **values))
        values.update(status="skipped", scheduled_for=now, finished_at=now)
        async with env.engine.begin() as connection:
            await connection.execute(JobRunRecord.__table__.insert().values(id=uuid4(), source_job_id=source_id, **values))
        with pytest.raises(IntegrityError):
            async with env.engine.begin() as connection:
                await connection.execute(JobRunRecord.__table__.insert().values(id=uuid4(), source_job_id=source_id, **values))
        values.update(status="waiting_resource", scheduled_for=None, concurrency_key="test:batch")
        async with env.engine.begin() as connection:
            await connection.execute(JobRunRecord.__table__.insert().values(id=uuid4(), **values))
        with pytest.raises(IntegrityError):
            async with env.engine.begin() as connection:
                await connection.execute(JobRunRecord.__table__.insert().values(id=uuid4(), **values))
    run(verify())


def test_upgrade_rejects_multiple_unconfirmed_old_runs_without_losing_records(old_database):
    async def verify():
        env = old_database
        async with env.engine.begin() as connection:
            first, job_id = await legacy_run(connection, env.tables, status="running")
            second, _ = await legacy_run(connection, env.tables, status="failed", stats={"needs_attention": True}, job_id=job_id)
        with pytest.raises(DBAPIError, match="多条未确认旧执行"):
            async with env.engine.begin() as connection:
                await connection.run_sync(env.upgrade)
        async with env.engine.begin() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS
            records = (await connection.execute(select(env.tables["scheduled_job_runs"]))).mappings().all()
            assert {item["id"] for item in records} == {first, second}
            assert {item["status"] for item in records} == {"running", "failed"}
            assert (await connection.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'scheduled_job_runs' AND column_name = 'source_job_id'"))).first() is None
    run(verify())
