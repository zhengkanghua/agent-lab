"""KnowledgeBase 应用用例、HTTP 权限与迁移的离线验证。"""

import asyncio
import importlib.util
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from agent_lab.api.knowledge_bases import get_knowledge_base_service
from agent_lab.auth.dependencies import current_active_user, current_superuser
from agent_lab.knowledge.adapters.postgres import postgres_knowledge_base_work
from agent_lab.knowledge.application import KnowledgeBaseService
from agent_lab.knowledge.contracts import KnowledgeBaseCreateRequest, KnowledgeBaseUpdateRequest
from agent_lab.knowledge.domain import (
    KnowledgeBase,
    KnowledgeBaseInactiveError,
    KnowledgeBaseKeyConflictError,
    KnowledgeBaseNotFoundError,
    KnowledgeBaseStorageError,
)
from tests.app_helpers import create_offline_app
from tests.auth_helpers import authenticated_reader, authenticated_superuser


class MemoryStore:
    """以独立快照模拟提交与回滚，验证用例对事务端口的依赖。"""

    def __init__(self):
        self.records = {}
        self.commits = 0
        self.fail_commit = False

    @asynccontextmanager
    async def work(self):
        records = self.records.copy()
        store = self

        class Repository:
            async def list(self, *, include_inactive):
                return sorted(
                    (item for item in records.values() if include_inactive or item.is_active),
                    key=lambda item: item.key,
                )

            async def get_for_update(self, item_id):
                return records.get(item_id)

            async def get(self, item_id):
                return records.get(item_id)

            async def create(self, request):
                if any(item.key == request.key for item in records.values()):
                    raise KnowledgeBaseKeyConflictError()
                now = datetime(2026, 9, 6, tzinfo=UTC)
                item = KnowledgeBase(id=uuid4(), created_at=now, updated_at=now, **request.model_dump())
                records[item.id] = item
                return item

            async def update(self, item):
                updated = replace(item, updated_at=item.updated_at + timedelta(seconds=1))
                records[item.id] = updated
                return updated

        async def commit():
            if store.fail_commit:
                raise KnowledgeBaseStorageError("private storage details")
            store.records = records
            store.commits += 1

        yield SimpleNamespace(repository=Repository(), commit=commit)


def test_configuration_lifecycle_keeps_identity_and_avoids_noop_writes():
    async def verify():
        store = MemoryStore()
        service = KnowledgeBaseService(store.work)
        news = await service.create(KnowledgeBaseCreateRequest(key="news", name="新闻", description="现有说明"))
        other = await service.create(KnowledgeBaseCreateRequest(key="tech", name="技术资料"))
        with pytest.raises(KnowledgeBaseKeyConflictError):
            await service.create(KnowledgeBaseCreateRequest(key="news", name="另一名称"))
        updated = await service.update(news.id, KnowledgeBaseUpdateRequest(name="新闻档案", description=None))
        assert updated.key == news.key
        assert updated.created_at == news.created_at
        assert updated.description is None
        assert updated.updated_at > news.updated_at
        assert await service.update(news.id, KnowledgeBaseUpdateRequest(name=updated.name)) == updated
        assert store.commits == 3

        disabled = await service.update(news.id, KnowledgeBaseUpdateRequest(is_active=False))
        assert disabled.name == updated.name
        assert await service.list() == [other]
        assert await service.list(include_inactive=True) == [disabled, other]
        enabled = await service.update(news.id, KnowledgeBaseUpdateRequest(is_active=True))
        assert await service.list() == [enabled, other]
        with pytest.raises(KnowledgeBaseNotFoundError):
            await service.update(uuid4(), KnowledgeBaseUpdateRequest(name="缺失"))

        store.fail_commit = True
        with pytest.raises(KnowledgeBaseStorageError):
            await service.update(news.id, KnowledgeBaseUpdateRequest(name="不能提交"))
        assert (await service.list())[0] == enabled

    asyncio.run(verify())


def test_scope_uses_current_state_without_writing():
    async def verify():
        store = MemoryStore()
        service = KnowledgeBaseService(store.work)
        news = await service.create(KnowledgeBaseCreateRequest(key="news", name="新闻"))
        assert await service.require_active(news.id) == news
        assert store.commits == 1
        await service.update(news.id, KnowledgeBaseUpdateRequest(is_active=False))
        with pytest.raises(KnowledgeBaseInactiveError):
            await service.require_active(news.id)
        with pytest.raises(KnowledgeBaseNotFoundError):
            await service.require_active(uuid4())
        await service.update(news.id, KnowledgeBaseUpdateRequest(is_active=True))
        assert (await service.require_active(news.id)).is_active
        assert store.commits == 3

    asyncio.run(verify())


@pytest.mark.parametrize("body", [
    {}, {"key": "rename"}, {"name": None}, {"is_active": None},
    {"name": "  "}, {"is_active": "false"}, {"description": "x" * 2001},
])
def test_patch_rejects_ambiguous_or_unsupported_changes(body):
    with pytest.raises(ValidationError):
        KnowledgeBaseUpdateRequest.model_validate(body)


@pytest.mark.parametrize("key", ["News", "-news", "news-", "news--tech", "a/b", "新闻", "a" * 65])
def test_stable_key_has_one_canonical_shape(key):
    with pytest.raises(ValidationError):
        KnowledgeBaseCreateRequest(key=key, name="知识库")


def test_create_normalizes_names_and_defaults_to_active():
    request = KnowledgeBaseCreateRequest(key=" tech-notes ", name=" 技术资料 ", description=" 说明 ")
    assert request.key == "tech-notes"
    assert request.name == "技术资料"
    assert request.description == "说明"
    assert request.is_active is True


def make_app(store, *, role="superuser"):
    app = create_offline_app()
    service = KnowledgeBaseService(store.work)
    app.dependency_overrides[get_knowledge_base_service] = lambda: service
    if role == "superuser":
        app.dependency_overrides[current_active_user] = authenticated_superuser
        app.dependency_overrides[current_superuser] = authenticated_superuser
    elif role == "reader":
        app.dependency_overrides[current_active_user] = authenticated_reader
    return app


def test_http_management_is_typed_and_matches_configuration_behavior():
    async def verify():
        store = MemoryStore()
        app = make_app(store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            created = await client.post("/knowledge-bases", json={"key": "news", "name": "新闻"})
            assert created.status_code == 201
            item = created.json()
            assert item["is_active"] is True
            duplicate = await client.post("/knowledge-bases", json={"key": "news", "name": "重复"})
            assert duplicate.status_code == 409
            assert duplicate.json()["code"] == "knowledge_base_key_conflict"
            updated = await client.patch(f"/knowledge-bases/{item['id']}", json={"name": "新闻档案", "is_active": False})
            assert updated.status_code == 200
            assert updated.json()["key"] == "news"
            assert (await client.get("/knowledge-bases")).json() == []
            assert (await client.get("/knowledge-bases?include_inactive=true")).json() == [updated.json()]
            missing = await client.patch(f"/knowledge-bases/{uuid4()}", json={"name": "缺失"})
            assert missing.status_code == 404
            assert missing.json()["code"] == "knowledge_base_not_found"
            invalid = await client.patch(f"/knowledge-bases/{item['id']}", json={"key": "private-input"})
            assert invalid.status_code == 422
            assert invalid.json()["code"] == "invalid_request"
            assert "private-input" not in invalid.text
            assert (await client.delete(f"/knowledge-bases/{item['id']}")).status_code == 405

            store.fail_commit = True
            failure = await client.post("/knowledge-bases", json={"key": "failed", "name": "故障"})
            assert failure.status_code == 503
            assert failure.json() == {"code": "knowledge_base_storage_unavailable", "detail": "知识库存储当前不可用。", "retryable": True}
            assert "private storage" not in failure.text

    asyncio.run(verify())


def test_anonymous_and_reader_cannot_manage_configuration():
    async def verify():
        store = MemoryStore()
        for role in ("anonymous", "reader"):
            app = make_app(store, role=role)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
                listed = await client.get("/knowledge-bases")
                assert listed.status_code == (401 if role == "anonymous" else 200)
                assert (await client.get("/knowledge-bases?include_inactive=true")).status_code in (401, 403)
                assert (await client.post("/knowledge-bases", json={"key": "news", "name": "新闻"})).status_code in (401, 403)
                assert (await client.patch(f"/knowledge-bases/{uuid4()}", json={"is_active": False})).status_code in (401, 403)
        assert store.commits == 0

    asyncio.run(verify())


def test_adapter_converts_database_failure_without_exposing_connection_details():
    @asynccontextmanager
    async def failed_session():
        raise OperationalError("private SQL", {}, RuntimeError("private connection"))
        yield

    async def verify():
        with pytest.raises(KnowledgeBaseStorageError) as caught:
            async with postgres_knowledge_base_work(failed_session):
                pytest.fail("故障 Session 不应进入工作单元")
        assert str(caught.value) == ""

    asyncio.run(verify())


def test_migration_renders_additive_postgresql_ddl_and_news_seed():
    migration_path = Path(__file__).parents[1] / "alembic/versions/a27d6b9e4301_add_knowledge_bases.py"
    spec = importlib.util.spec_from_file_location("knowledge_base_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(url="postgresql://", opts={"as_sql": True, "literal_binds": True, "output_buffer": output})
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE knowledge_bases" in sql
    assert "INSERT INTO knowledge_bases" in sql
    assert "'news'" in sql
    assert "uq_knowledge_bases_key" in sql
    assert "DROP " not in sql
    assert "DELETE " not in sql
    assert "ALTER TABLE" not in sql
