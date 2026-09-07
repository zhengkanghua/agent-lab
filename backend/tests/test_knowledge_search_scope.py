"""通过生产搜索用例、HTTP 和 Agent 验证范围准入，不连接外部服务。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from qdrant_client import AsyncQdrantClient, models

from agent_lab.agent.tools.search_news import build_search_news_tool
from agent_lab.agent.middleware import sanitize_tool_error
from agent_lab.api.dependencies import get_vector_search_service
from agent_lab.knowledge.application import KnowledgeBaseService
from agent_lab.knowledge.contracts import KnowledgeBaseCreateRequest, KnowledgeBaseUpdateRequest
from agent_lab.knowledge.domain import (
    DEFAULT_NEWS_KNOWLEDGE_BASE_ID,
    KnowledgeBaseInactiveError,
    KnowledgeBaseNotFoundError,
    KnowledgeBaseStorageError,
)
from agent_lab.schemas.document_search import DocumentSearchRequest
from agent_lab.schemas.vector_search import VectorSearchRequest
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_reader
from tests.test_knowledge_bases import MemoryStore
from tests.test_vector_search import FakeEmbeddings, build_payload, build_point, build_runtime_components


@pytest.mark.parametrize("method,request_type", [("search", VectorSearchRequest), ("search_documents", DocumentSearchRequest)])
@pytest.mark.parametrize("failure", [None, KnowledgeBaseInactiveError, KnowledgeBaseNotFoundError, KnowledgeBaseStorageError])
def test_scope_failure_precedes_embedding_and_vector_io(method, request_type, failure):
    async def verify():
        embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
        scope = SimpleNamespace(require_active=AsyncMock(side_effect=failure() if failure else None))
        service, _, _, _ = build_runtime_components(
            fake_embeddings=embeddings, client=SimpleNamespace(), knowledge_base_scope=scope,
        )
        request = request_type(query="private query", knowledge_base_id=uuid4() if failure else None)
        with pytest.raises(failure or ValueError):
            await getattr(service, method)(request)
        assert embeddings.query_calls == []
        assert scope.require_active.await_count == (1 if failure else 0)

    asyncio.run(verify())


async def prepare_shared_collection():
    store = MemoryStore()
    scope = KnowledgeBaseService(store.work)
    news = await scope.create(KnowledgeBaseCreateRequest(key="news", name="新闻"))
    del store.records[news.id]
    news = replace(news, id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID)
    store.records[news.id] = news
    other = await scope.create(KnowledgeBaseCreateRequest(key="tech", name="技术"))
    client = AsyncQdrantClient(location=":memory:")
    embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
    service, _, spec, settings = build_runtime_components(
        fake_embeddings=embeddings, client=client, knowledge_base_scope=scope,
    )
    await client.create_collection(settings.collection_name, vectors_config=spec.vector_params)
    await client.update_collection_aliases([models.CreateAliasOperation(create_alias=models.CreateAlias(
        collection_name=settings.collection_name, alias_name=settings.collection_alias,
    ))])
    await client.upsert(settings.collection_alias, points=[
        build_point(payload=build_payload(knowledge_base_id=str(news.id), title="NEWS_DOCUMENT")),
        build_point(payload=build_payload(knowledge_base_id=str(other.id), title="OTHER_DOCUMENT")),
    ])
    return scope, news, other, client, embeddings, service


@pytest.mark.parametrize("path", ["/vector-search", "/document-search"])
@pytest.mark.parametrize("scope_input", ["default", "explicit", "nested"])
def test_http_disabled_scope_keeps_points_but_rejects_queries(path, scope_input):
    async def verify():
        scope, news, other, client, embeddings, service = await prepare_shared_collection()
        app = create_offline_app()
        allow_reader(app)
        app.dependency_overrides[get_vector_search_service] = lambda: service
        body = {"query": "review"}
        target = news if scope_input == "default" else other
        if scope_input == "explicit":
            body["knowledge_base_id"] = str(target.id)
        elif scope_input == "nested":
            body["filters"] = {"knowledge_base_id": str(target.id)}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as http:
                before = await http.post(path, json=body)
                assert before.status_code == 200
                assert {item["knowledge_base_id"] for item in before.json()} == {str(target.id)}
                await scope.update(target.id, KnowledgeBaseUpdateRequest(is_active=False))
                rejected = await http.post(path, json=body)
                assert rejected.status_code == 409
                assert rejected.json() == {"code": "knowledge_base_inactive", "detail": "知识库已停用。", "retryable": False}
                assert len(embeddings.query_calls) == 1
                missing = await http.post(path, json={"query": "private query", "knowledge_base_id": str(uuid4())})
                assert missing.status_code == 404
                assert missing.json()["code"] == "knowledge_base_not_found"
                await scope.update(target.id, KnowledgeBaseUpdateRequest(is_active=True))
                resumed = await http.post(path, json=body)
                assert resumed.json() == before.json()
        finally:
            await client.close()

    asyncio.run(verify())


@pytest.mark.parametrize("within_days", [None, True])
def test_agent_stays_in_news_and_honors_disabled_state(within_days):
    async def verify():
        scope, news, _, client, embeddings, service = await prepare_shared_collection()
        try:
            tool = build_search_news_tool(service)
            args = {"query": "review"}
            if within_days is not None:
                # 使用 Tool 允许的最大时间窗口，样本发布时间位于该范围内。
                from agent_lab.agent.limits import SEARCH_TOOL_MAX_WITHIN_DAYS
                args["within_days"] = SEARCH_TOOL_MAX_WITHIN_DAYS
            result = await tool.ainvoke(args)
            assert "NEWS_DOCUMENT" in result
            assert "OTHER_DOCUMENT" not in result
            await scope.update(news.id, KnowledgeBaseUpdateRequest(is_active=False))
            with pytest.raises(KnowledgeBaseInactiveError) as caught:
                await tool.ainvoke(args)
            assert sanitize_tool_error(caught.value, SimpleNamespace(tool_call={"name": "search_news"})) == "工具调用失败：知识库已停用。"
            assert len(embeddings.query_calls) == 1
        finally:
            await client.close()

    asyncio.run(verify())
