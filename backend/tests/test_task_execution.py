"""通过受理、执行与编号查询验证公共任务，不依赖 Celery eager 或真实外部服务。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.models.write_operation import WriteOperationRecord
from agent_lab.services.write_coordination import WriteCoordinator, discard_task_preparation
from agent_lab.tasks.contracts import (
    ExecutionPolicy, FailureDecision, RecoveryDecision, RequestConflict, ResourceWait,
    TaskDetailsExpired, TaskError, TaskOverlap,
)
from agent_lab.tasks.cron import CronSchedule
from agent_lab.tasks.dispatch import TaskDispatcher
from tests.task_helpers import add_job, echo_spec, execute_accepted, task_system


def test_new_type_executes_from_direct_and_scheduled_intake():
    async def scenario():
        async with task_system() as system:
            direct = await system.service.submit("test_echo", {"value": 11}, actor="user:1", request_key="a")
            assert direct.status == "queued" and direct.job_id is None
            assert (await execute_accepted(system, direct)).stats == {"value": 11}
            job = await add_job(system)
            scheduled = await system.service.scheduled(job.id, 1, job.next_run_at, CronSchedule("Asia/Shanghai"))
            assert (await execute_accepted(system, scheduled)).stats == {"value": 7}
    asyncio.run(scenario())


def test_request_identity_snapshot_deletion_and_expired_receipt():
    async def scenario():
        async with task_system() as system:
            job = await add_job(system)
            original = await system.service.trigger(job.id, actor="user:1", request_key="old")
            async with system.sessions() as session:
                current = await session.get(ScheduledJobRecord, job.id)
                current.params = {"value": 99}
                await session.commit()
            assert await system.service.trigger(job.id, actor="user:1", request_key="old") == original
            with pytest.raises(TaskOverlap) as error:
                await system.service.trigger(job.id, actor="user:1", request_key="new")
            assert error.value.run_id == original.run_id
            async with system.sessions() as session:
                await session.delete(await session.get(ScheduledJobRecord, job.id))
                await session.commit()
            run = await execute_accepted(system, original)
            assert run.job_id is None and run.source_job_id == job.id and run.stats == {"value": 7}
            system.clock.advance(31 * 86400)
            assert await system.store.prune_history() == 1
            expired = await system.service.trigger(job.id, actor="user:1", request_key="old")
            assert expired.run_id == original.run_id and expired.details_expired
            with pytest.raises(TaskDetailsExpired):
                await system.service.get_run(original.run_id)
            first = await system.service.submit("test_echo", {}, actor="user:1", request_key="direct")
            with pytest.raises(RequestConflict):
                await system.service.submit("test_echo", {"value": 2}, actor="user:1", request_key="direct")
            second = await system.service.submit("test_echo", {}, actor="user:1", request_key="another")
            assert second.run_id != first.run_id
    asyncio.run(scenario())


def test_publish_failure_and_lost_message_redeliver_without_reexecuting():
    async def scenario():
        async with task_system() as system:
            publish = AsyncMock(side_effect=ConnectionError("private-address"))
            dispatcher = TaskDispatcher(system.store, publish, retry_seconds=5)
            system.service.dispatcher = dispatcher
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            assert (await system.service.get_run(accepted.run_id)).dispatch_error_type == "ConnectionError"
            system.clock.advance(5)
            publish.side_effect = None
            await dispatcher.publish_due()
            # 已发布但消息丢失也会再次补投同一编号、同一代次。
            system.clock.advance(5)
            await dispatcher.publish_due()
            assert publish.await_count == 3
            run = await execute_accepted(system, accepted)
            await system.worker.execute(run.id, run.delivery_generation)
            assert (await system.service.get_run(run.id)).attempts == 1
    asyncio.run(scenario())


def test_retry_limits_frozen_policy_and_manual_retry_link():
    async def scenario():
        execute = AsyncMock(side_effect=TimeoutError("private-address"))
        spec = echo_spec(execute=execute, classify_error=lambda _: FailureDecision(retryable=True))
        async with task_system(spec) as system:
            job = await add_job(system, params={"value": 8})
            cron = CronSchedule(clock=system.clock)
            accepted = await system.service.scheduled(job.id, 1, job.next_run_at, cron)
            await system.service.update_policy(ExecutionPolicy(max_retries=0, history_retention_days=1), "user:1")
            for attempt in range(1, 5):
                run = await execute_accepted(system, accepted)
                assert run.attempts == attempt
                if attempt < 4:
                    assert run.status == "retry_wait"
                    assert run.available_at == system.clock() + timedelta(seconds=30 * 2 ** (attempt - 1))
                    system.clock.now = run.available_at
            assert run.status == "failed" and execute.await_count == 4
            retry = await system.service.retry(run.id, actor="user:1", request_key="retry")
            assert retry.run_id != run.id
            retried = await execute_accepted(system, retry)
            assert retried.retry_of == run.id and retried.config_snapshot["params"] == {"value": 8}
            assert retried.status == "failed" and retried.attempts == 1
            assert retried.policy_snapshot["max_retries"] == 0
            assert len(await system.service.policy_history()) == 1
            async with system.sessions() as session:
                current = await session.get(ScheduledJobRecord, job.id)
                assert current.enabled
                next_time = current.next_run_at
            system.clock.now = next_time
            next_cycle = await system.service.scheduled(job.id, 1, next_time, cron)
            assert next_cycle.run_id not in {run.id, retry.run_id}
            execute.side_effect = None
            execute.return_value = {"recovered": True}
            result = await execute_accepted(system, next_cycle)
            assert result.status == "succeeded" and result.attempts == 1
            assert result.policy_snapshot["max_retries"] == 0
    asyncio.run(scenario())


def test_wait_does_not_consume_attempt_and_cancel_invalidates_old_claim():
    @asynccontextmanager
    async def busy(_runtime, _params):
        raise ResourceWait("资源占用中")
        yield

    async def scenario():
        async with task_system(echo_spec(execution_scope=busy)) as system:
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            run = await execute_accepted(system, accepted)
            assert run.status == "waiting_resource" and run.attempts == 0
            assert run.wait_reason == "资源占用中"
            await system.service.cancel(run.id)
            system.clock.advance(10)
            await system.worker.execute(run.id, run.delivery_generation)
            assert (await system.service.get_run(run.id)).status == "cancelled"
    asyncio.run(scenario())


def test_prepared_resource_cancel_and_stale_recovery_leave_no_orphan():
    async def scenario(cancel):
        async with task_system() as system:
            entered, release = asyncio.Event(), asyncio.Event()
            business = AsyncMock(return_value={"ok": True})

            @asynccontextmanager
            async def prepare(_runtime, _params):
                async with WriteCoordinator(system.sessions).hold(("sync",), wait=False):
                    entered.set()
                    await release.wait()
                    yield

            spec = echo_spec(execution_scope=prepare, execute=business, discard_preparation=discard_task_preparation)
            system.registry._specs[spec.task_type] = spec
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            executing = asyncio.create_task(system.worker.execute(accepted.run_id, 1))
            await entered.wait()
            if cancel:
                await system.service.cancel(accepted.run_id)
            else:
                system.clock.advance(31)
                await system.worker.recover_stalled()
            release.set()
            await executing
            business.assert_not_awaited()
            async with system.sessions() as session:
                assert not (await session.scalars(select(WriteOperationRecord))).all()
            run = await system.service.get_run(accepted.run_id)
            assert run.status == ("cancelled" if cancel else "queued") and run.attempts == 0
    asyncio.run(scenario(True))
    asyncio.run(scenario(False))


def test_started_execution_refuses_cancel_and_late_result_cannot_overwrite_recovery():
    async def scenario():
        async with task_system() as system:
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            claim = await system.store.claim(accepted.run_id, 1, "test-owner")
            assert await system.store.start(claim.id, claim.claim_token)
            with pytest.raises(TaskError):
                await system.service.cancel(claim.id)
            system.clock.advance(31)
            await system.worker.recover_stalled()
            assert not await system.store.finish(claim.id, claim.claim_token, status="succeeded", stats={})
            assert (await system.service.get_run(claim.id)).status == "needs_attention"
    asyncio.run(scenario())


def test_safe_recovery_exhaustion_is_failed_and_success_callback_is_atomic():
    async def scenario():
        async with task_system() as system:
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            claim = await system.store.claim(accepted.run_id, 1, "test-owner")
            await system.store.start(claim.id, claim.claim_token)
            async with system.sessions() as session:
                run = await session.get(JobRunRecord, claim.id)
                run.attempts = 4
                await session.commit()
            system.clock.advance(31)
            stale, = await system.store.stale_claims()
            await system.store.recover_claim(stale, RecoveryDecision(action="retry", reason="业务已核实"))
            assert (await system.service.get_run(claim.id)).status == "failed"

            other = await system.service.submit("test_echo", {}, actor="user:1", request_key="b")
            claim = await system.store.claim(other.run_id, 1, "test-owner")
            await system.store.start(claim.id, claim.claim_token)
            callback = AsyncMock(side_effect=RuntimeError("交接提交失败"))
            with pytest.raises(RuntimeError):
                await system.store.finish(claim.id, claim.claim_token, status="succeeded", stats={}, on_success=callback)
            assert (await system.service.get_run(claim.id)).status == "running"
            callback.side_effect = None
            assert await system.store.finish(claim.id, claim.claim_token, status="succeeded", stats={}, on_success=callback)
            assert (await system.service.get_run(claim.id)).status == "succeeded"
    asyncio.run(scenario())


def test_terminal_save_failure_only_retries_save(caplog):
    async def scenario():
        business = AsyncMock(return_value={"done": 1})
        async with task_system(echo_spec(execute=business)) as system:
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            system.store.finish = AsyncMock(side_effect=RuntimeError("private-database-address"))
            await execute_accepted(system, accepted)
            business.assert_awaited_once()
            assert system.store.finish.await_count == 2
            assert (await system.service.get_run(accepted.run_id)).status == "running"
    asyncio.run(scenario())
    assert "private-database-address" not in caplog.text
    assert "status=succeeded" not in caplog.text
