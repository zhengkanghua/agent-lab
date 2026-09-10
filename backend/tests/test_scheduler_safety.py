"""调度收尾、配置版本与第三种任务接入的离线行为测试。"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent_lab.config.scheduler import SchedulerSettings
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.schemas.scheduled_jobs import JobRunResponse
from agent_lab.services.scheduled_job_service import ScheduledJobService
from agent_lab.services.scheduled_task_errors import (
    ScheduledJobAlreadyRunningError, ScheduledJobClosingError, ScheduledJobEditBlockedError,
)
from tests.test_scheduler_runner import FakeStore, FakeWriteRuntime, make_job, make_runner


def test_retention_uses_registered_execution_with_boolean_dry_run():
    async def scenario():
        store, runtime = FakeStore(), FakeWriteRuntime()
        runtime.prune_old_documents = AsyncMock(return_value=SimpleNamespace(to_job_run_stats=lambda: {"dry_run": True, "documents_deleted": 12}))
        job = make_job(task_type="prune_old_documents", params={"retention_days": 180, "dry_run": True})
        store.jobs[job.id] = job
        runner = make_runner(store, runtime)
        run_id = await runner.trigger_now(job)
        await runner.close()
        # 缺省范围的旧任务参数规范化后固定解析到新闻库，不扩大为全库清理。
        runtime.prune_old_documents.assert_awaited_once_with(
            retention_days=180, dry_run=True, knowledge_base_ids=[DEFAULT_NEWS_KNOWLEDGE_BASE_ID]
        )
        assert store.find(run_id).stats["dry_run"] is True
        assert runtime.closed and not runtime.index_calls and not runtime.sync_calls
    asyncio.run(scenario())


def test_old_cron_version_and_disabled_job_do_not_start_business():
    async def scenario():
        store, runtime = FakeStore(), FakeWriteRuntime()
        job = make_job()
        job.config_version = 3
        store.jobs[job.id] = job
        runner = make_runner(store, runtime)
        await runner._run_scheduled(job.id, 2)
        job.enabled = False
        await runner._run_scheduled(job.id, 3)
        assert store.runs == []
        assert runtime.sync_calls == []
        # 停用仍然允许明确的手动执行。
        await runner.trigger_now(job)
        await runner.close()
        assert len(runtime.sync_calls) == 1
    asyncio.run(scenario())


def test_refresh_reloads_changes_from_another_store_writer():
    async def scenario():
        store, runtime = FakeStore(), FakeWriteRuntime()
        runner = make_runner(store, runtime)
        await runner.start()
        try:
            job = make_job()
            store.jobs[job.id] = job
            await runner.refresh()
            assert runner._scheduler.get_job(str(job.id)).args == (job.id, 1)
            job.config_version = 2
            job.cron_expr = "0 9 * * *"
            await runner.refresh()
            scheduled = runner._scheduler.get_job(str(job.id))
            assert scheduled.args == (job.id, 2)
            assert scheduled.misfire_grace_time == 1
            assert scheduled.next_run_time > datetime.now(UTC)
            store.jobs.clear()
            await runner.refresh()
            assert runner.next_run_at(job.id) is None
        finally:
            await runner.close()
    asyncio.run(scenario())


def test_shutdown_waits_for_inflight_claim_and_closes_accepted_runtime():
    async def scenario():
        store, runtime = FakeStore(), FakeWriteRuntime()
        job = make_job()
        store.jobs[job.id] = job
        entered, gate = asyncio.Event(), asyncio.Event()
        original = store.claim_run

        async def delayed(*args, **kwargs):
            entered.set()
            await gate.wait()
            return await original(*args, **kwargs)

        store.claim_run = delayed
        runner = make_runner(store, runtime)
        submission = asyncio.create_task(runner.trigger_now(job))
        await entered.wait()
        closing = asyncio.create_task(runner.close())
        await asyncio.sleep(0)
        assert not closing.done()
        gate.set()
        run_id = await submission
        await closing
        assert store.find(run_id).status == "succeeded"
        assert runtime.closed
        with pytest.raises(ScheduledJobClosingError):
            await runner.trigger_now(job)
    asyncio.run(scenario())


def test_shutdown_cancel_is_recorded_and_runtime_closes_before_terminal():
    async def scenario():
        store, runtime = FakeStore(), FakeWriteRuntime(gate=asyncio.Event())
        job = make_job()
        store.jobs[job.id] = job
        original = store.finish_run

        async def finished(*args, **kwargs):
            assert runtime.closed
            await original(*args, **kwargs)

        store.finish_run = finished
        runner = make_runner(store, runtime, settings=SchedulerSettings(shutdown_grace_seconds=0))
        run_id = await runner.trigger_now(job)
        await asyncio.sleep(0)
        await runner.close()
        assert store.find(run_id).error_type == "CancelledError"
        assert store.find(run_id).stats["needs_attention"] is True
    asyncio.run(scenario())


def test_terminal_save_retry_does_not_repeat_business_and_close_failure_is_distinct():
    async def scenario():
        store, runtime = FakeStore(), FakeWriteRuntime()
        job = make_job()
        store.jobs[job.id] = job
        original = store.finish_run
        attempts = 0

        async def finished(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("终态提交失败")
            await original(*args, **kwargs)

        runtime.close = AsyncMock(side_effect=RuntimeError("关闭失败"))
        store.finish_run = finished
        runner = make_runner(store, runtime)
        run_id = await runner.trigger_now(job)
        await runner.close()
        record = store.find(run_id)
        assert attempts == 2 and len(runtime.sync_calls) == 1
        assert record.status == "succeeded"
        assert record.stats["resource_close_error"] == "RuntimeError"
    asyncio.run(scenario())


@pytest.mark.parametrize("enabled, active, requested_enable", [(True, False, False), (False, True, None), (False, False, True)])
def test_edit_requires_previously_disabled_idle_job(enabled, active, requested_enable):
    async def scenario():
        job = make_job(enabled=enabled)
        service = ScheduledJobService(None, None)
        repository = SimpleNamespace(lock_job=AsyncMock(return_value=job), active_run=AsyncMock(return_value=object() if active else None), commit=AsyncMock())
        service._repository = repository
        with pytest.raises(ScheduledJobEditBlockedError):
            await service.update_job(job.id, params={"limit_per_source": 4}, enabled=requested_enable)
        repository.commit.assert_not_awaited()
    asyncio.run(scenario())


def test_active_execution_blocks_delete_but_not_disable():
    async def scenario():
        job = make_job()
        service = ScheduledJobService(None, make_runner(FakeStore(), FakeWriteRuntime()))
        repository = SimpleNamespace(lock_job=AsyncMock(return_value=job), active_run=AsyncMock(return_value=object()), commit=AsyncMock(), refresh=AsyncMock(), latest_run=AsyncMock(return_value=None), delete_job=AsyncMock())
        service._repository = repository
        with pytest.raises(ScheduledJobAlreadyRunningError):
            await service.delete_job(job.id)
        await service.update_job(job.id, enabled=False)
        assert job.enabled is False and job.config_version == 2
        repository.delete_job.assert_not_awaited()
    asyncio.run(scenario())


def test_stale_heartbeat_is_attention_not_permission_to_rerun():
    response = JobRunResponse(id=uuid4(), job_id=uuid4(), trigger_type="manual", status="running", started_at=datetime.now(UTC), finished_at=None, stats={}, error_type=None, heartbeat_at=datetime.now(UTC) - timedelta(minutes=1))
    assert response.needs_attention
    assert response.status == "running"


def test_repeated_cancellation_does_not_interrupt_runtime_close_or_terminal_save():
    async def scenario():
        store, runtime = FakeStore(), FakeWriteRuntime()
        job = make_job()
        store.jobs[job.id] = job
        closing, release = asyncio.Event(), asyncio.Event()

        async def close_runtime():
            closing.set()
            await release.wait()
            runtime.closed = True

        runtime.close = close_runtime
        runner = make_runner(store, runtime)
        run_id = await runner.trigger_now(job)
        await closing.wait()
        execution = next(iter(runner._executor._tasks))
        for _ in range(2):
            execution.cancel()
            await asyncio.sleep(0)
        assert not execution.done()
        assert store.find(run_id).status == "running"
        release.set()
        await runner.close()
        assert runtime.closed
        assert store.find(run_id).status == "succeeded"
    asyncio.run(scenario())


def test_runtime_creation_failure_is_recorded_without_retrying_factory():
    async def scenario():
        store = FakeStore()
        job = make_job()
        store.jobs[job.id] = job
        runner = make_runner(store, FakeWriteRuntime())
        calls = []

        def fail():
            calls.append(1)
            raise RuntimeError("敏感配置不得进入记录")

        runner._executor._runtimes = fail
        run_id = await runner.trigger_now(job)
        await runner.close()
        record = store.find(run_id)
        assert calls == [1]
        assert record.error_type == "RuntimeError"
        assert record.stats == {"failure_phase": "runtime"}
    asyncio.run(scenario())


def test_remote_interruption_keeps_completed_retention_counts_in_execution_record():
    from agent_lab.domain.write_scope import WriteScope, remote_write, write_scope
    from agent_lab.services.document_retention_service import DocumentRetentionService
    from tests.test_document_retention_service import FakeRepository, FakeQdrant, NOW

    async def scenario():
        repo, qdrant = FakeRepository(120), FakeQdrant()
        original = qdrant.delete_by_document_ids

        @remote_write
        async def delete(ids):
            if qdrant.delete_calls:
                raise TimeoutError("远端可能已经接受写入")
            await original(ids)

        qdrant.delete_by_document_ids = delete
        store, runtime = FakeStore(), FakeWriteRuntime()

        async def prune(**params):
            token = write_scope.set(WriteScope(("sync", "index")))
            try:
                return await DocumentRetentionService(repo, qdrant, lambda: None, clock=lambda: NOW).prune_old_documents(**params)
            finally:
                write_scope.reset(token)

        runtime.prune_old_documents = prune
        job = make_job(task_type="prune_old_documents", params={"dry_run": False, "retention_days": 180})
        store.jobs[job.id] = job
        runner = make_runner(store, runtime)
        run_id = await runner.trigger_now(job)
        await runner.close()
        record = store.find(run_id)
        assert record.status == "failed" and record.stats["needs_attention"]
        assert record.stats["documents_deleted"] == 50
        assert record.stats["qdrant_points_deleted"] == 100
        assert record.stats["failed_documents"] == 50
        assert len(repo.documents) == 70 and len(repo.intents) == 50
        assert len(qdrant.count_calls) == 2
    asyncio.run(scenario())


def test_skipped_record_failure_is_sanitized_before_returning_to_apscheduler(caplog):
    async def scenario():
        store, runtime = FakeStore(), FakeWriteRuntime(gate=asyncio.Event())
        job = make_job()
        store.jobs[job.id] = job
        store.record_skipped = AsyncMock(side_effect=RuntimeError("private-database-address"))
        runner = make_runner(store, runtime, settings=SchedulerSettings(shutdown_grace_seconds=0))
        await runner.trigger_now(job)
        await runner._run_scheduled(job.id, 1)
        await runner.close()
    asyncio.run(scenario())
    assert "调度跳过记录失败" in caplog.text
    assert "private-database-address" not in caplog.text
