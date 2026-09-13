"""执行收尾、业务恢复依据与批次交接的离线回归。"""

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_lab.domain.write_scope import WriteRecoveryRequiredError
from agent_lab.knowledge.task_intake import continue_document_processing, ensure_document_processing
from agent_lab.services.scheduled_task_registry import TASK_TYPE_SPECS
from agent_lab.tasks.contracts import ExecutionPolicy, FailureDecision
from tests.task_helpers import echo_spec, execute_accepted, task_system


def test_terminal_save_retry_and_close_failure_do_not_repeat_business():
    async def scenario():
        business = AsyncMock(return_value={"done": 1})
        runtime = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed")))
        async with task_system(echo_spec(execute=business, runtime_factory=lambda: runtime)) as system:
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            original, calls = system.store.finish, []
            async def finish(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError("save failed")
                return await original(*args, **kwargs)
            system.store.finish = finish
            result = await execute_accepted(system, accepted)
            assert result.status == "succeeded" and result.stats["resource_close_error"] == "RuntimeError"
            assert len(calls) == 2
            business.assert_awaited_once()
    asyncio.run(scenario())


def test_repeated_cancellation_does_not_interrupt_cleanup():
    async def scenario():
        closing, release = asyncio.Event(), asyncio.Event()
        async def close():
            closing.set()
            await release.wait()
        runtime = SimpleNamespace(close=close)
        async with task_system(echo_spec(runtime_factory=lambda: runtime)) as system:
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            execution = asyncio.create_task(system.worker.execute(accepted.run_id, 1))
            await closing.wait()
            for _ in range(2):
                execution.cancel()
                await asyncio.sleep(0)
            assert not execution.done()
            release.set()
            await execution
            assert (await system.service.get_run(accepted.run_id)).status == "succeeded"
    asyncio.run(scenario())


def test_runtime_creation_failure_is_sanitized_and_not_repeated():
    async def scenario():
        calls = []
        def fail():
            calls.append(1)
            raise RuntimeError("secret connection")
        async with task_system(echo_spec(runtime_factory=fail)) as system:
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            result = await execute_accepted(system, accepted)
            assert result.status == "failed" and result.error_type == "RuntimeError"
            assert result.attempts == 0 and len(calls) == 1
            assert "secret" not in str(result.stats)
    asyncio.run(scenario())


def test_remote_interruption_keeps_completed_retention_counts():
    from agent_lab.domain.write_scope import WriteScope, remote_write, write_scope
    from agent_lab.services.document_retention_service import DocumentRetentionService
    from tests.test_document_retention_service import FakeRepository, FakeQdrant, NOW
    async def scenario():
        repository, qdrant = FakeRepository(120), FakeQdrant()
        original = qdrant.delete_by_document_ids
        @remote_write
        async def delete(ids):
            if qdrant.delete_calls:
                raise TimeoutError("remote write may have succeeded")
            await original(ids)
        qdrant.delete_by_document_ids = delete
        async def prune(**params):
            token = write_scope.set(WriteScope(("sync", "index")))
            try:
                return await DocumentRetentionService(repository, qdrant, lambda: None, clock=lambda: NOW).prune_old_documents(**params)
            finally:
                write_scope.reset(token)
        runtime = SimpleNamespace(prune_old_documents=prune, close=AsyncMock())
        spec = replace(TASK_TYPE_SPECS["prune_old_documents"], runtime_factory=lambda: runtime,
                       execution_scope=lambda *_: nullcontext())
        async with task_system(spec) as system:
            accepted = await system.service.submit(spec.task_type, {"dry_run": False}, actor="user:1", request_key="a")
            record = await execute_accepted(system, accepted)
            assert record.status == "needs_attention"
            assert record.stats["documents_deleted"] == 50
            assert record.stats["qdrant_points_deleted"] == 100
            assert record.stats["failed_documents"] == 50
            assert len(repository.documents) == 70 and len(repository.intents) == 50
    asyncio.run(scenario())


def test_original_failure_stays_while_retry_history_is_retained():
    async def scenario():
        async with task_system(echo_spec(execute=AsyncMock(side_effect=RuntimeError()))) as system:
            await system.service.update_policy(ExecutionPolicy(history_retention_days=1), "user:1")
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            failed = await execute_accepted(system, accepted)
            await system.service.update_policy(ExecutionPolicy(history_retention_days=10), "user:1")
            retry = await system.service.retry(failed.id, actor="user:1", request_key="retry")
            await execute_accepted(system, retry)
            system.clock.advance(2 * 86400)
            assert await system.store.prune_history() == 0
            assert (await system.service.get_run(failed.id)).status == "failed"
            system.clock.advance(10 * 86400)
            assert await system.store.prune_history() == 1
            assert await system.store.prune_history() == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "needs_attention", "cancelled"])
def test_document_batch_only_continues_after_committed_success(monkeypatch, outcome):
    async def pending(_session):
        return True
    monkeypatch.setattr("agent_lab.knowledge.task_intake.has_pending_work", pending)
    execute = AsyncMock(return_value={})
    if outcome != "succeeded":
        execute.side_effect = WriteRecoveryRequiredError() if outcome == "needs_attention" else RuntimeError()
    spec = replace(TASK_TYPE_SPECS["document_processing"], execute=execute,
        runtime_factory=lambda: None, execution_scope=lambda *_: nullcontext(),
        on_success=continue_document_processing)
    async def scenario():
        async with task_system(spec) as system:
            # 业务事务回滚时受理记录也回滚。
            async with system.sessions() as session:
                await ensure_document_processing(session)
            assert await system.service.list_runs() == []
            async with system.sessions() as session:
                identity = await ensure_document_processing(session)
                await session.commit()
            system.clock.now = (await system.service.get_run(identity)).available_at
            if outcome == "cancelled":
                await system.service.cancel(identity)
            else:
                await system.worker.execute(identity, 1)
            record = await system.service.get_run(identity)
            assert record.status == outcome
            runs = await system.service.list_runs()
            assert len(runs) == (2 if outcome == "succeeded" else 1)
            if outcome == "succeeded":
                successor = next(item for item in runs if item.id != identity)
                assert successor.status == "queued" and successor.concurrency_key == spec.concurrency_key
                async def no_pending(_session):
                    return False
                monkeypatch.setattr("agent_lab.knowledge.task_intake.has_pending_work", no_pending)
                system.clock.now = successor.available_at
                await system.worker.execute(successor.id, 1)
                assert len(await system.service.list_runs()) == 2
    asyncio.run(scenario())
