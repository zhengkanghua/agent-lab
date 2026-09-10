"""执行者恢复、按需装配与本地就绪检查的离线边界测试。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from agent_lab import scheduler_main
from agent_lab.domain.write_scope import WriteRecoveryRequiredError
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.scheduler_maintenance import inspect_or_recover
from agent_lab.services.write_coordination import WriteCoordinator
from tests.test_scheduler_runner import FakeStore, FakeWriteRuntime, make_job, make_runner


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
    runtime.executor = SimpleNamespace(writing=no_resources, sync_news=AsyncMock(return_value="sync-result"), index_pending=AsyncMock(return_value="index-result"))

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
    now = datetime.now(UTC)
    run = SimpleNamespace(id=uuid4(), job_id=uuid4(), status="failed", stats={"needs_attention": True, "documents_deleted": 50}, heartbeat_at=now, error_type="WriteRecoveryRequiredError")
    operation = SimpleNamespace(heartbeat_at=now if recent else now - timedelta(minutes=1))
    session = SimpleNamespace(
        get=AsyncMock(return_value=run), scalar=AsyncMock(), refresh=AsyncMock(), execute=AsyncMock(),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [operation])), commit=AsyncMock(),
    )

    @asynccontextmanager
    async def sessions():
        yield session

    async def verify():
        if recent:
            with pytest.raises(ValueError):
                await inspect_or_recover(sessions, run_id=run.id, confirm_stopped=True)
            session.commit.assert_not_awaited()
            assert run.stats["needs_attention"] is True
        else:
            result = await inspect_or_recover(sessions, run_id=run.id, confirm_stopped=True)
            assert result["released_operations"] == 1
            assert run.status == "failed" and run.stats["documents_deleted"] == 50
            assert run.stats["needs_attention"] is False
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


def test_terminal_save_failure_never_repeats_business_or_reports_confirmed_success(caplog):
    async def verify():
        store, runtime = FakeStore(), FakeWriteRuntime()
        job = make_job()
        store.jobs[job.id] = job
        store.finish_run = AsyncMock(side_effect=RuntimeError("private-database-detail"))
        runner = make_runner(store, runtime)
        run_id = await runner.trigger_now(job)
        await runner.close()
        assert len(runtime.sync_calls) == 1 and runtime.closed
        assert store.find(run_id).status == "running"
        assert store.finish_run.await_count == 2
    asyncio.run(verify())
    assert "任务终态仍未确认" in caplog.text
    assert "private-database-detail" not in caplog.text


def test_local_readiness_needs_fresh_success_and_living_process(tmp_path, monkeypatch):
    path = tmp_path / "scheduler.json"
    monkeypatch.setenv("SCHEDULER_STATUS_PATH", str(path))
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


def test_readiness_write_failure_cannot_skip_task_cleanup():
    async def verify():
        store, runtime = FakeStore(), FakeWriteRuntime()
        job = make_job()
        store.jobs[job.id] = job
        runner = make_runner(store, runtime)
        runner._status_writer = Mock(side_effect=OSError("本地目录不可写"))
        run_id = await runner.trigger_now(job)
        await runner.close()
        assert runtime.closed and store.find(run_id).status == "succeeded"
    asyncio.run(verify())
