"""执行者恢复、按需装配与本地就绪检查的离线边界测试。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from agent_lab.tasks import status as scheduler_main
from agent_lab.domain.write_scope import WriteRecoveryRequiredError
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.scheduler_maintenance import inspect_or_recover
from agent_lab.services.write_coordination import WriteCoordinator
from tests.task_helpers import task_system
from agent_lab.models.scheduled_job import JobRunRecord
from agent_lab.models.write_operation import WriteOperationRecord


@asynccontextmanager
async def no_resources(*_args):
    yield SimpleNamespace()


@pytest.mark.parametrize("step", ["sync", "index", "retention"])
def test_only_requested_dependencies_are_created_and_all_are_closed(monkeypatch, step):
    import agent_lab.pipeline.write_runtime as module
    importer = Mock()
    index = SimpleNamespace(run=AsyncMock(return_value="index-result"))
    indexing = Mock(return_value=index)
    deletion = SimpleNamespace(close=AsyncMock())
    settings = Mock(return_value=SimpleNamespace(collection_alias="synthetic_current"))
    monkeypatch.setattr(module, "build_qdrant_client", Mock(return_value=deletion))
    service = SimpleNamespace(prune_old_documents=AsyncMock(return_value="retention-result"))
    monkeypatch.setattr(module, "DocumentRetentionService", Mock(return_value=service))
    runtime = PipelineWriteRuntime.lazy(session_factory=no_resources, freshrss_factory=importer, processing_factory=indexing, qdrant_settings_factory=settings)
    runtime.executor = SimpleNamespace(writing=no_resources, sync_news=AsyncMock(return_value="sync-result"))

    async def verify():
        if step == "sync":
            assert await runtime.sync_only(limit_per_source=1) == "sync-result"
        elif step == "index":
            assert await runtime.index_only(batch_size=1, stale_after=timedelta(minutes=1)) == "index-result"
        else:
            assert await runtime.prune_old_documents(
                retention_days=180, dry_run=True, knowledge_base_ids=[DEFAULT_NEWS_KNOWLEDGE_BASE_ID]
            ) == "retention-result"
        await runtime.close()
    asyncio.run(verify())
    assert importer.call_count == (step == "sync")
    assert indexing.call_count == index.run.await_count == (step == "index")
    assert settings.call_count == deletion.close.await_count == (step == "retention")


def test_retention_service_creation_failure_still_closes_created_client(monkeypatch):
    import agent_lab.pipeline.write_runtime as module
    client = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(module, "build_qdrant_client", lambda _: client)
    monkeypatch.setattr(module, "DocumentRetentionService", Mock(side_effect=ValueError("本地构造失败")))
    runtime = PipelineWriteRuntime.lazy(session_factory=no_resources, freshrss_factory=Mock(), processing_factory=Mock(), qdrant_settings_factory=lambda: SimpleNamespace(collection_alias="synthetic"))
    runtime.executor = SimpleNamespace(writing=no_resources)

    async def verify():
        try:
            with pytest.raises(ValueError):
                await runtime.prune_old_documents(
                retention_days=180, dry_run=True, knowledge_base_ids=[DEFAULT_NEWS_KNOWLEDGE_BASE_ID]
            )
        finally:
            await runtime.close()
    asyncio.run(verify())
    client.close.assert_awaited_once()


@pytest.mark.parametrize("recent", [False, True])
def test_manual_recovery_clears_attention_only_after_owner_stops(recent):
    async def verify():
        async with task_system() as system:
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            heartbeat = datetime.now(UTC) - timedelta(seconds=0 if recent else 60)
            async with system.sessions() as session:
                record = await session.get(JobRunRecord, accepted.run_id)
                record.status, record.heartbeat_at = "needs_attention", heartbeat
                record.stats = {"documents_deleted": 50}
                session.add(WriteOperationRecord(id=uuid4(), run_id=record.id, resources=["index"],
                    owner="stopped-test", status="uncertain", started_at=heartbeat, heartbeat_at=heartbeat))
                await session.commit()
            if recent:
                with pytest.raises(ValueError):
                    await inspect_or_recover(system.sessions, run_id=accepted.run_id, confirm_stopped=True)
                assert (await system.service.get_run(accepted.run_id)).status == "needs_attention"
            else:
                result = await inspect_or_recover(system.sessions, run_id=accepted.run_id, confirm_stopped=True)
                assert result["released_operations"] == 1
                record = await system.service.get_run(accepted.run_id)
                assert record.status == "failed" and record.stats["documents_deleted"] == 50
                assert record.expires_at is not None and record.claim_token is None
                retried = await system.service.retry(record.id, actor="user:1", request_key="retry")
                assert retried.status == "queued"
    asyncio.run(verify())


def test_failed_resource_release_requires_recovery():
    session = SimpleNamespace(add=Mock(), commit=AsyncMock())

    @asynccontextmanager
    async def sessions():
        yield session

    coordinator = WriteCoordinator(sessions)
    coordinator._acquire = AsyncMock(return_value=True)
    original = coordinator._release

    async def release(*args):
        heartbeat = args[-1]
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        raise RuntimeError("数据库提交失败")

    coordinator._release = release

    async def verify():
        with pytest.raises(WriteRecoveryRequiredError) as raised:
            async with coordinator.hold(("index",)):
                raise WriteRecoveryRequiredError(stats={"indexed_count": 2})
        assert raised.value.stats == {"indexed_count": 2}
    try:
        asyncio.run(verify())
    finally:
        coordinator._release = original


def test_local_readiness_needs_fresh_success_and_living_process(tmp_path, monkeypatch):
    path = tmp_path / "scheduler.json"
    monkeypatch.setenv("TASK_BEAT_STATUS_PATH", str(path))
    assert not scheduler_main.check_status()
    scheduler_main.write_status(ready=True, jobs=0, max_age_seconds=180)
    assert scheduler_main.check_status()
    state = json.loads(path.read_text())
    state["updated_at"] -= 60
    path.write_text(json.dumps(state))
    assert scheduler_main.check_status()
    state["updated_at"] -= 180
    path.write_text(json.dumps(state))
    assert not scheduler_main.check_status()
    scheduler_main.write_status(ready=False, jobs=2)
    assert not scheduler_main.check_status()
    scheduler_main.write_status(ready=True, jobs=2)
    monkeypatch.setattr(scheduler_main, "process_exists", lambda _: False)
    assert not scheduler_main.check_status()


def test_readiness_write_failure_cannot_skip_process_cleanup(monkeypatch):
    from agent_lab.tasks import beat
    scheduler = beat.PostgresScheduler.__new__(beat.PostgresScheduler)
    scheduler._controller = None
    closing = Mock()
    monkeypatch.setattr(beat, "write_status", Mock(side_effect=OSError("unwritable")))
    monkeypatch.setattr(beat, "close_process_runtime", closing)
    with pytest.raises(OSError):
        scheduler.close()
    closing.assert_called_once()


@pytest.mark.parametrize("shutdown_signal", ["worker_process_shutdown", "worker_shutdown"])
def test_process_runtime_reuses_one_loop_until_worker_shutdown(monkeypatch, shutdown_signal):
    from celery import signals
    from agent_lab.tasks import celery_app, process  # noqa: F401

    # 导入真实信号装配，分别验证 prefork 子进程与 solo 主进程的关闭入口。
    monkeypatch.setattr(process, "_runtime", None)
    loops = []
    async def remember():
        loops.append(asyncio.get_running_loop())
    async def opened(self):
        await remember()
        self.engine = SimpleNamespace(dispose=remember)
    monkeypatch.setattr(process.ProcessRuntime, "_open", opened)
    runtime = process.get_process_runtime()
    try:
        runtime.run(remember())
        runtime.run(remember())
        getattr(signals, shutdown_signal).send(sender=None)
        getattr(signals, shutdown_signal).send(sender=None)
        assert len(loops) == 4 and all(loop is loops[0] for loop in loops)
        assert loops[0].is_closed()
    finally:
        process.close_process_runtime()
