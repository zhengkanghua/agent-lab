"""来源绑定的事务、写协调与 HTTP 契约；所有存储均为离线替身。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from agent_lab.api.sources import get_source_binding_service
from agent_lab.auth.dependencies import current_active_user, current_superuser
from agent_lab.domain.write_scope import WriteRecoveryRequiredError, WriteResourceBusyError
from agent_lab.knowledge.adapters.sources import postgres_source_binding_work
from agent_lab.knowledge.contracts import SourceView
from agent_lab.knowledge.domain import KnowledgeBase, SourceBindingError
from agent_lab.services.source_binding_service import SourceBindingService
from agent_lab.services.write_coordination import WriteCoordinator
from tests.app_helpers import create_offline_app
from tests.auth_helpers import authenticated_superuser
from tests.test_auth import auth_app, user


class BindingStore:
    """未提交的变更在工作单元退出时丢弃，并记录事务与占用的先后顺序。"""

    def __init__(self):
        now = datetime.now(UTC)
        self.target = KnowledgeBase(uuid4(), "tech", "技术资料", None, True, now, now)
        self.source = SourceView(uuid4(), "freshrss", "feed/1", "来源", None, None, None, None, "cursor", now)
        self.events = []
        self.documents = False
        self.deletions = False
        self.failure = None
        self.busy = None

    @asynccontextmanager
    async def hold(self, resources, *, wait=True):
        assert resources == ("sync", "index") and wait is False
        if self.busy:
            raise self.busy
        self.events.append("acquire")
        try:
            yield
        finally:
            self.events.append("release")

    @asynccontextmanager
    async def work(self):
        self.events.append("open")
        pending = self.source

        async def save(source):
            nonlocal pending
            self.events.append("save")
            pending = source

        async def commit():
            self.events.append("commit")
            if self.failure:
                raise self.failure
            self.source = pending

        try:
            yield SimpleNamespace(
                sources=SimpleNamespace(
                    list=AsyncMock(side_effect=lambda: [self.source]),
                    get_for_update=AsyncMock(side_effect=lambda source_id: self.source if source_id == self.source.id else None),
                    has_documents=AsyncMock(return_value=self.documents),
                    has_pending_deletions=AsyncMock(return_value=self.deletions),
                    save_binding=save,
                ),
                knowledge_bases=SimpleNamespace(get_for_update=AsyncMock(
                    side_effect=lambda target_id: self.target if target_id == self.target.id else None,
                )),
                commit=commit,
            )
        finally:
            self.events.append("close")


def test_empty_source_can_bind_rebind_and_unbind_with_new_checkpoint_baseline():
    async def verify():
        store = BindingStore()
        service = SourceBindingService(store.work, store)
        for target_id in (store.target.id, uuid4(), None):
            if target_id is not None:
                store.target = replace(store.target, id=target_id)
            store.events.clear()
            result = await service.bind(store.source.id, target_id)
            assert result == store.source
            assert result.knowledge_base_id == target_id
            assert result.knowledge_base_key == ("tech" if target_id else None)
            assert result.sync_checkpoint is result.sync_checkpoint_updated_at is None
            assert store.events == ["acquire", "open", "save", "commit", "close", "release"]
        store.events.clear()
        assert await service.bind(store.source.id, None) == store.source
        assert store.events == ["acquire", "open", "close", "release"]
        store.events.clear()
        assert await service.list_sources() == [store.source]
        assert store.events == ["open", "close"]

    asyncio.run(verify())


@pytest.mark.parametrize("failure", ["documents", "deletions", "commit"])
def test_rejected_binding_preserves_source_and_closes_transaction_before_release(failure):
    async def verify():
        store = BindingStore()
        before = store.source
        if failure == "commit":
            store.failure = SourceBindingError("source_database_unavailable", "来源存储当前不可用。")
        else:
            setattr(store, failure, True)
        with pytest.raises(SourceBindingError):
            await SourceBindingService(store.work, store).bind(before.id, store.target.id)
        assert store.source == before
        assert store.events[-2:] == ["close", "release"]
        assert ("save" in store.events) == (failure == "commit")

    asyncio.run(verify())


@pytest.mark.parametrize("case,status,code", [
    ("missing_source", 404, "source_not_found"),
    ("missing_target", 404, "knowledge_base_not_found"),
    ("inactive", 409, "knowledge_base_inactive"),
    ("busy", 409, "source_binding_conflict"),
    ("uncertain", 409, "source_write_recovery_required"),
    ("storage", 503, "source_database_unavailable"),
])
def test_http_binding_errors_are_stable_and_leave_configuration_unchanged(case, status, code):
    async def verify():
        store = BindingStore()
        before = store.source
        source_id, target_id = before.id, store.target.id
        if case == "missing_source":
            source_id = uuid4()
        elif case == "missing_target":
            target_id = uuid4()
        elif case == "inactive":
            store.target = replace(store.target, is_active=False)
        elif case == "busy":
            store.busy = WriteResourceBusyError()
        elif case == "uncertain":
            store.busy = WriteRecoveryRequiredError()
        elif case == "storage":
            store.failure = SourceBindingError(code, "来源存储当前不可用。")
        app = create_offline_app()
        app.dependency_overrides[get_source_binding_service] = lambda: SourceBindingService(store.work, store)
        app.dependency_overrides[current_active_user] = authenticated_superuser
        app.dependency_overrides[current_superuser] = authenticated_superuser
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            response = await client.patch(f"/sources/{source_id}/knowledge-base", json={"knowledge_base_id": str(target_id)})
        assert response.status_code == status
        assert response.json()["code"] == code
        assert store.source == before
        if case in ("busy", "uncertain"):
            assert store.events == []
        else:
            assert store.events[-2:] == ["close", "release"]

    asyncio.run(verify())


def test_sources_require_superuser_for_both_read_and_write():
    async def verify():
        for role in ("anonymous", "reader"):
            app, _ = auth_app(user())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
                if role == "reader":
                    assert (await client.post("/auth/login", data={"username": "reader@example.com", "password": "test-password"})).status_code == 204
                assert (await client.get("/sources")).status_code == (401 if role == "anonymous" else 403)
                response = await client.patch(f"/sources/{uuid4()}/knowledge-base", json={"knowledge_base_id": None})
                assert response.status_code == (401 if role == "anonymous" else 403)

    asyncio.run(verify())


@pytest.mark.parametrize("status,stale", [("active", False), ("waiting", False), ("uncertain", False), ("active", True)])
def test_nonwaiting_coordinator_keeps_other_occupancy_and_removes_only_its_request(status, stale):
    async def verify():
        now = datetime.now(UTC)
        existing = SimpleNamespace(id=uuid4(), status=status, resources=["sync"], started_at=now - timedelta(minutes=2), heartbeat_at=now - timedelta(minutes=1) if stale else now)
        operations = [existing]

        async def execute(statement, *args):
            if getattr(statement, "is_delete", False):
                operation_id = statement.compile().params["id_1"]
                operations[:] = [item for item in operations if item.id != operation_id]

        session = SimpleNamespace(add=operations.append, commit=AsyncMock(), execute=execute,
                                  scalars=AsyncMock(side_effect=lambda _: SimpleNamespace(all=lambda: operations)))

        @asynccontextmanager
        async def sessions():
            yield session

        error = WriteRecoveryRequiredError if stale or status == "uncertain" else WriteResourceBusyError
        with pytest.raises(error):
            async with WriteCoordinator(sessions).hold(("sync", "index"), wait=False):
                pytest.fail("占用期间不应进入业务事务")
        assert operations == [existing]
        assert existing.status == status

    asyncio.run(verify())


def test_source_adapter_closes_session_and_sanitizes_database_failure():
    events = []

    @asynccontextmanager
    async def sessions():
        try:
            yield SimpleNamespace(commit=AsyncMock(side_effect=OperationalError("private SQL", {}, RuntimeError("private connection"))))
        finally:
            events.append("closed")

    async def verify():
        with pytest.raises(SourceBindingError) as caught:
            async with postgres_source_binding_work(sessions) as work:
                await work.commit()
        assert events == ["closed"]
        assert caught.value.code == "source_database_unavailable"
        assert "private" not in caught.value.detail

    asyncio.run(verify())
