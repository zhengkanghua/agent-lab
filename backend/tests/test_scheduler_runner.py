"""动态 Beat、配置生效和重叠规则的离线验证；不以 SQLite 证明 PostgreSQL 并发锁。"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_lab.models.scheduled_job import ScheduledJobRecord
from agent_lab.services.scheduled_job_service import ScheduledJobService
from agent_lab.tasks.beat import BeatController
from agent_lab.tasks.contracts import TaskOverlap
from agent_lab.tasks.cron import CronSchedule
from tests.task_helpers import add_job, execute_accepted, task_system


@pytest.mark.parametrize("status", ["queued", "waiting_resource", "running", "retry_wait", "needs_attention"])
def test_overlap_skips_new_cycle_and_conflicts_new_manual_request(status):
    async def scenario():
        async with task_system() as system:
            job = await add_job(system)
            first = await system.service.trigger(job.id, actor="user:1", request_key="first")
            async with system.sessions() as session:
                from agent_lab.models.scheduled_job import JobRunRecord
                current = await session.get(JobRunRecord, first.run_id)
                current.status = status
                await session.commit()
            skipped = await system.service.scheduled(job.id, 1, job.next_run_at, CronSchedule())
            assert skipped.status == "skipped"
            with pytest.raises(TaskOverlap) as error:
                await system.service.trigger(job.id, actor="user:1", request_key="second")
            assert error.value.run_id == first.run_id
            assert len(await system.service.list_runs()) == 2
    asyncio.run(scenario())


def test_edit_enabled_and_running_job_uses_snapshot_then_delete_preserves_history():
    async def scenario():
        async with task_system() as system:
            job = await add_job(system)
            accepted = await system.service.trigger(job.id, actor="user:1", request_key="a")
            async with system.sessions() as session:
                manager = ScheduledJobService(session, CronSchedule(clock=system.clock), system.registry)
                changed = await manager.update_job(job.id, params={"value": 33}, cron_expr="*/5 * * * *")
                assert changed.record.enabled and changed.record.config_version == 2
                assert changed.active_run.id == accepted.run_id
                await manager.update_job(job.id, enabled=False)
                await manager.delete_job(job.id)
            result = await execute_accepted(system, accepted)
            assert result.stats == {"value": 7} and result.source_job_id == job.id
            assert result.job_id is None
    asyncio.run(scenario())


def test_old_version_disabled_and_missed_cycles_do_not_accept():
    async def scenario():
        async with task_system() as system:
            job = await add_job(system)
            cron = CronSchedule(clock=system.clock)
            assert await system.service.scheduled(job.id, 0, job.next_run_at, cron) is None
            async with system.sessions() as session:
                record = await session.get(ScheduledJobRecord, job.id)
                record.enabled = False
                await session.commit()
            assert await system.service.scheduled(job.id, 1, job.next_run_at, cron) is None
            manual = await system.service.trigger(job.id, actor="user:1", request_key="a")
            await execute_accepted(system, manual)
            async with system.sessions() as session:
                record = await session.get(ScheduledJobRecord, job.id)
                record.enabled = True
                await session.commit()
            system.clock.advance(61)
            assert await system.service.scheduled(job.id, 1, job.next_run_at, cron) is None
            assert len(await system.service.list_runs()) == 1
            async with system.sessions() as session:
                assert (await session.get(ScheduledJobRecord, job.id)).next_run_at > system.clock()
    asyncio.run(scenario())


def test_beat_restart_drops_missed_cycles_and_slow_publish_does_not_block_schedule(monkeypatch):
    monkeypatch.setattr("agent_lab.tasks.beat.write_status", lambda **_: None)
    async def scenario():
        async with task_system() as system:
            job = await add_job(system, cron_expr="* * * * *")
            accepted = await system.service.submit("test_echo", {}, actor="user:1", request_key="queued")
            publishing = asyncio.Event()
            async def delayed_publish(**_):
                publishing.set()
                await asyncio.Event().wait()
            runtime = SimpleNamespace(store=system.store, service=system.service, worker=system.worker,
                                      dispatcher=SimpleNamespace(publish_due=delayed_publish))
            controller = BeatController(runtime, cron=CronSchedule(clock=system.clock))
            system.clock.advance(5 * 60)
            await controller.start()
            await controller.tick()
            await publishing.wait()
            assert len(await system.service.list_runs()) == 1
            system.clock.advance(60)
            await controller.tick()
            records = await system.service.list_runs()
            assert len(records) == 2
            assert (await system.service.get_run(accepted.run_id)).status == "queued"
            await controller.close()
    asyncio.run(scenario())
