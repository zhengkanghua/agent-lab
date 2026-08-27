"""文档级 grouped vector search 的离线契约和排序测试。

测试只使用 fake Embedding 与 fake Qdrant grouped response，不访问项目环境变量、
Ollama、PostgreSQL 或远程 Qdrant。重点验证分组参数在基础设施层执行，而不是在
有限的前端 top_k 结果上去重。
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr, ValidationError
from qdrant_client.http import models

from agent_lab.api.vector_search import VectorSearchErrorResponse
from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.main import create_app
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.search import (
    QdrantSearchResponseError,
    QdrantVectorSearch,
)
from agent_lab.schemas.document_search import (
    DocumentSearchRequest,
    DocumentSearchResult,
)
from agent_lab.schemas.vector_search import VectorSearchFilters
from agent_lab.services.vector_search_service import VectorSearchService
from tests.auth_helpers import allow_reader, skip_environment_admin_sync


def run(coroutine: Any) -> Any:
    """执行测试协程，不依赖 pytest-asyncio。"""

    return asyncio.run(coroutine)


def settings() -> QdrantSettings:
    """创建不读取 .env 的固定测试设置。"""

    return QdrantSettings(
        _env_file=None,
        base_url="http://qdrant.example.test",
        api_key=SecretStr(""),
        request_timeout_seconds=5,
        environment="document_search_test",
        collection_schema_version="v1",
        collection_generation=1,
        vector_dimension=3,
        distance="Cosine",
    )


def ollama_settings() -> OllamaEmbeddingSettings:
    """创建与测试 Provider 一致的 Embedding 设置。"""

    return OllamaEmbeddingSettings(
        _env_file=None,
        base_url="https://ollama.example.test",
        embedding_model="bge-m3:567m",
        api_key=SecretStr(""),
        embedding_request_timeout_seconds=5,
        embedding_batch_size=2,
    )


def payload(
    document_id: UUID,
    *,
    chunk_index: int,
    chunk_count: int,
    title: str = "示例新闻",
    score_label: str = "正文",
    content_hash: str = "a" * 64,
) -> dict[str, Any]:
    """构造一个完整的 Qdrant flat Payload。"""

    return {
        "page_content": f"{score_label}片段 {chunk_index}",
        "document_id": str(document_id),
        "content_hash": content_hash,
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
        "title": title,
        "url": "https://example.com/news",
        "published_at": "2026-08-14T00:00:00+00:00",
        "document_type": "article",
        "source_id": str(uuid4()),
        "source_provider": "test",
        "source_name": "测试来源",
        "source_external_id": "feed/1",
        "document_external_id": f"article/{document_id}",
        "authors": [],
        "labels": ["宏观"],
        "index_schema_version": "v1",
        "embedding_model": "bge-m3:567m",
    }


def point(
    document_id: UUID,
    score: float,
    *,
    chunk_index: int,
    chunk_count: int,
    title: str = "示例新闻",
    content_hash: str = "a" * 64,
) -> models.ScoredPoint:
    """构造 grouped response 中的 ScoredPoint。"""

    return models.ScoredPoint(
        id=uuid4(),
        version=1,
        score=score,
        payload=payload(
            document_id,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
            title=title,
            content_hash=content_hash,
        ),
    )


class EmbeddingStub:
    """只实现 Service 所需的 query Embedding 方法。"""

    embedding_model = "bge-m3:567m"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed_query(self, query: str) -> list[float]:
        """记录 query 并返回有限的三维向量。"""

        self.queries.append(query)
        return [1.0, 0.0, 0.0]


class GroupedClient:
    """记录正式 query_points_groups 参数并返回预置 groups。"""

    def __init__(self, groups: list[Any]) -> None:
        self.groups = groups
        self.calls: list[dict[str, Any]] = []

    async def query_points_groups(self, **kwargs: Any) -> Any:
        """记录一次分组查询，不执行网络 I/O。"""

        self.calls.append(kwargs)
        return SimpleNamespace(groups=self.groups)


def build_service(client: GroupedClient) -> tuple[VectorSearchService, EmbeddingStub]:
    """组装只读文档搜索 Service。"""

    spec = VectorIndexSpec.from_settings(settings(), ollama_settings())
    provider = EmbeddingStub()
    component = QdrantVectorSearch(client, settings(), spec)
    return (
        VectorSearchService(
            embedding_provider=provider,  # type: ignore[arg-type]
            vector_search=component,
            spec=spec,
        ),
        provider,
    )


def test_document_search_groups_and_sorts_documents_and_matches() -> None:
    document_a = uuid4()
    document_b = uuid4()
    client = GroupedClient(
        [
            # 故意以非最终顺序提供 groups，Service/Qdrant 边界必须按最高 score 排序。
            SimpleNamespace(
                id=str(document_b),
                hits=[point(document_b, 0.72, chunk_index=1, chunk_count=2), point(document_b, 0.74, chunk_index=0, chunk_count=2)],
            ),
            SimpleNamespace(
                id=str(document_a),
                hits=[point(document_a, 0.91, chunk_index=0, chunk_count=2), point(document_a, 0.86, chunk_index=1, chunk_count=2)],
            ),
        ]
    )
    service, provider = build_service(client)

    results = run(
        service.search_documents(
            DocumentSearchRequest(
                query="利率变化",
                document_limit=2,
                matches_per_document=2,
                score_threshold=0.6,
                filters=VectorSearchFilters(labels=["宏观"]),
            )
        )
    )

    assert [result.document_id for result in results] == [document_a, document_b]
    assert [result.best_score for result in results] == [0.91, 0.74]
    assert [match.score for match in results[0].additional_matches] == [0.86]
    assert results[0].best_match.chunk_index == 0
    assert provider.queries == ["利率变化"]
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["collection_name"].endswith("_current")
    assert call["group_by"] == "document_id"
    assert call["limit"] == 2
    assert call["group_size"] == 2
    assert call["score_threshold"] == pytest.approx(0.6)
    assert call["query_filter"].must[0].key == "labels"


def test_document_search_empty_groups_returns_empty_list() -> None:
    service, _provider = build_service(GroupedClient([]))

    assert run(service.search_documents(DocumentSearchRequest(query="无结果"))) == []


def test_grouped_response_rejects_mismatched_document_id() -> None:
    document_id = uuid4()
    other_id = uuid4()
    client = GroupedClient(
        [SimpleNamespace(id=str(document_id), hits=[point(other_id, 0.8, chunk_index=0, chunk_count=1)])]
    )
    service, _provider = build_service(client)

    with pytest.raises(QdrantSearchResponseError, match="不同的 document_id"):
        run(service.search_documents(DocumentSearchRequest(query="校验")))


def test_document_search_request_rejects_sensitive_invalid_query_without_echo() -> None:
    sensitive = "private query " * 500
    with pytest.raises(ValidationError) as exc_info:
        DocumentSearchRequest(query=sensitive)
    assert sensitive not in str(exc_info.value)


class FakeDocumentSearchService:
    """HTTP 层 fake，验证路由只转发已校验 DTO。"""

    def __init__(self, result: list[DocumentSearchResult] | None = None) -> None:
        self.result = result or []
        self.requests: list[DocumentSearchRequest] = []

    async def search_documents(self, request: DocumentSearchRequest) -> list[DocumentSearchResult]:
        """记录请求并返回固定结果。"""

        self.requests.append(request)
        return self.result


class FakeRuntime:
    """只提供生命周期关闭方法，避免 HTTP 测试创建真实客户端。"""

    def __init__(self, service: Any) -> None:
        self.service = service

    async def close(self) -> None:
        """不执行外部 I/O。"""


async def http_request(app: FastAPI, **kwargs: Any) -> httpx.Response:
    """在显式 lifespan 中发送一条 ASGI 请求。"""

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/document-search", **kwargs)


def test_document_search_http_forwards_limits_and_empty_success() -> None:
    service = FakeDocumentSearchService()
    app = allow_reader(
        create_app(  # type: ignore[arg-type]
            runtime_factory=lambda: FakeRuntime(service),
            environment_admin_sync=skip_environment_admin_sync,
        )
    )

    response = run(
        http_request(
            app,
            json={
                "query": "央行利率",
                "document_limit": 7,
                "matches_per_document": 4,
                "filters": {"labels": ["宏观"]},
            },
        )
    )

    assert response.status_code == 200
    assert response.json() == []
    assert service.requests[0].document_limit == 7
    assert service.requests[0].matches_per_document == 4


def test_document_search_http_preserves_known_upstream_error_mapping() -> None:
    from agent_lab.qdrant.search import QdrantSearchTimeoutError

    class ErrorService(FakeDocumentSearchService):
        async def search_documents(self, request: DocumentSearchRequest) -> list[DocumentSearchResult]:
            """抛出已分类 timeout，验证共享错误映射。"""

            raise QdrantSearchTimeoutError("敏感的上游细节信息")

    service = ErrorService()
    app = allow_reader(
        create_app(  # type: ignore[arg-type]
            runtime_factory=lambda: FakeRuntime(service),
            environment_admin_sync=skip_environment_admin_sync,
        )
    )
    response = run(http_request(app, json={"query": "安全查询"}))

    assert response.status_code == 504
    parsed = VectorSearchErrorResponse.model_validate(response.json())
    assert parsed.code == "qdrant_timeout"
    assert "sensitive" not in response.text
