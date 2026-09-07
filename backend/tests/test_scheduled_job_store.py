"""任务受理规则的离线测试，只替换数据库查询，保留生产 Store 的判断。"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.models.scheduled_job import ScheduledJobRecord
from agent_lab.repositories.scheduled_job_repository import (
    ScheduledJobRepository,
    ScheduledJobStore,
)
from agent_lab.services.scheduled_task_errors import ScheduledJobUnknownTypeError


def test_unknown_task_type_is_rejected_before_saving_a_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未知任务不得被受理为 running，也不能提交执行记录。"""

    job = ScheduledJobRecord(id=uuid4(), task_type="no_such_type")
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    monkeypatch.setattr(
        ScheduledJobRepository, "lock_job", AsyncMock(return_value=job)
    )
    monkeypatch.setattr(
        ScheduledJobRepository, "active_run", AsyncMock(return_value=None)
    )
    store = ScheduledJobStore(lambda: session)

    with pytest.raises(ScheduledJobUnknownTypeError):
        asyncio.run(store.claim_run(
            job.id, trigger_type="manual", started_at=datetime.now(UTC), owner="test"
        ))

    session.add.assert_not_called()
    session.commit.assert_not_awaited()
