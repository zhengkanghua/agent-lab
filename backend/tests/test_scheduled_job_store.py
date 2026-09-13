"""未知类型或数据库提交失败都不能对外承诺受理成功。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from agent_lab.services.scheduled_task_errors import ScheduledJobUnknownTypeError
from tests.task_helpers import add_job, task_system


def test_unknown_task_type_leaves_no_execution_or_request_receipt():
    async def scenario():
        async with task_system() as system:
            job = await add_job(system, task_type="no_such_type")
            with pytest.raises(ScheduledJobUnknownTypeError):
                await system.service.trigger(job.id, actor="user:1", request_key="a")
            with pytest.raises(ScheduledJobUnknownTypeError):
                await system.service.submit("no_such_type", {}, actor="user:1", request_key="b")
            assert await system.service.list_runs() == []
    asyncio.run(scenario())


def test_failed_acceptance_commit_does_not_publish(monkeypatch):
    async def scenario():
        async with task_system() as system:
            from sqlalchemy.ext.asyncio import AsyncSession
            dispatcher = AsyncMock()
            system.service.dispatcher = dispatcher
            monkeypatch.setattr(AsyncSession, "commit", AsyncMock(side_effect=RuntimeError("commit failed")))
            with pytest.raises(RuntimeError):
                await system.service.submit("test_echo", {}, actor="user:1", request_key="a")
            dispatcher.publish_due.assert_not_awaited()
            assert await system.service.list_runs() == []
    asyncio.run(scenario())
