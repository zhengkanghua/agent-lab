"""阶段 4 FastAPI Vector Search HTTP 接口的完全离线测试。

测试使用内存 fake Runtime，不创建真实 Ollama/Qdrant client，不连接 PostgreSQL，也不
执行 Qdrant 写操作。它验证 HTTP JSON 契约、错误映射、lifespan 资源边界和敏感 query
不回显；阶段 3 的向量与 Alias 行为由 ``tests/test_vector_search.py`` 覆盖。
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from agent_lab.api.error_contract import VectorSearchErrorResponse
from agent_lab.pipeline.ollama_embedding_provider import (
    EmbeddingResponseError,
    OllamaAuthenticationError,
    OllamaConnectionError,
    OllamaEmbeddingError,
    OllamaModelNotFoundError,
    OllamaServiceError,
    OllamaTimeoutError,
)
from agent_lab.qdrant.search import (
    QdrantSearchAuthenticationError,
    QdrantSearchConfigurationError,
    QdrantSearchConnectionError,
    QdrantSearchResponseError,
    QdrantSearchServiceError,
    QdrantSearchTargetNotFoundError,
    QdrantSearchTimeoutError,
)
from agent_lab.schemas.vector_search import (
    VectorSearchFilters,
    VectorSearchRequest,
    VectorSearchResult,
)
from agent_lab.services.vector_search_service import (
    QueryVectorValidationError,
)
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_reader


def run(coroutine: Any) -> Any:
    """执行异步 HTTP 测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


def result() -> VectorSearchResult:
    """构造一个不依赖 Qdrant 的有效搜索结果。"""

    return VectorSearchResult(
        chunk_id=uuid4(),
        score=0.91,
        page_content="政策利率新闻正文",
        document_id=uuid4(),
        content_hash="a" * 64,
        chunk_index=0,
        chunk_count=1,
        title="政策利率新闻",
        url="https://example.com/news",
        published_at=datetime(2026, 8, 14, tzinfo=UTC),
        source_updated_at=None,
        document_type="article",
        source_id=uuid4(),
        source_provider="integration_test",
        source_name="测试来源",
        source_external_id="feed/1",
        document_external_id="article/1",
        authors=[],
        labels=["宏观"],
        previous_chunk_id=None,
        next_chunk_id=None,
        embedding_model="bge-m3:567m",
    )


class FakeSearchService:
    """记录请求并返回预置结果或预置异常，不执行任何网络 I/O。"""

    def __init__(
        self,
        *,
        results: list[VectorSearchResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.results = results if results is not None else [result()]
        self.error = error
        self.requests: list[VectorSearchRequest] = []

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchResult]:
        """记录已校验请求并返回预置行为。"""

        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.results


class FakeRuntime:
    """只暴露 API 所需的 service；若应用尝试 lifecycle 或写入会立即失败。"""

    def __init__(self, service: FakeSearchService) -> None:
        self.service = service
        self.closed = False

    async def close(self) -> None:
        """记录关闭，不访问外部资源。"""

        self.closed = True


def app_for(service: FakeSearchService) -> tuple[FastAPI, FakeRuntime]:
    """创建注入 fake Runtime 的 FastAPI 应用。"""

    runtime = FakeRuntime(service)
    app = allow_reader(create_offline_app(runtime_factory=lambda: runtime))
    return app, runtime


async def request(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    """在显式 lifespan 内发送一个 ASGI 请求。"""

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


def test_successful_search_returns_result_array_and_forwards_filters() -> None:
    service = FakeSearchService()
    app, runtime = app_for(service)
    source_id = uuid4()

    response = run(
        request(
            app,
            "POST",
            "/vector-search",
            json={
                "query": "央行利率",
                "top_k": 4,
                "score_threshold": 0.6,
                "filters": {
                    "source_id": str(source_id),
                    "source_provider": "freshrss_main",
                    "document_type": "article",
                    "labels": ["宏观", "利率"],
                    "published_from": "2026-08-01T00:00:00Z",
                    "published_to": "2026-08-31T23:59:59Z",
                },
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body[0]["chunk_id"] == str(service.results[0].chunk_id)
    assert body[0]["score"] == pytest.approx(0.91)
    assert len(service.requests) == 1
    forwarded = service.requests[0]
    assert forwarded.query == "央行利率"
    assert forwarded.top_k == 4
    assert forwarded.score_threshold == pytest.approx(0.6)
    assert forwarded.filters.source_id == source_id
    assert forwarded.filters.labels == ("宏观", "利率")
    assert runtime.closed is True


def test_empty_results_are_a_successful_empty_array() -> None:
    service = FakeSearchService(results=[])
    app, _runtime = app_for(service)

    response = run(
        request(
            app,
            "POST",
            "/vector-search",
            json={"query": "没有命中"},
        )
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "body",
    [
        {"query": "敏感 query 不应回显", "top_k": 0},
        {"query": "敏感 query 不应回显", "score_threshold": 1.5},
        {"query": "敏感 query 不应回显" * 500},
        {"query": ["敏感 query 不应回显"]},
        {
            "query": "敏感 query 不应回显",
            "filters": {"published_from": "2026-01-01T00:00:00"},
        },
    ],
)
def test_request_validation_returns_422_without_sensitive_query(
    body: dict[str, Any],
) -> None:
    service = FakeSearchService()
    app, _runtime = app_for(service)
    sensitive_query = body["query"]

    response = run(request(app, "POST", "/vector-search", json=body))

    assert response.status_code == 422
    if isinstance(sensitive_query, str):
        assert sensitive_query not in response.text
    else:
        assert sensitive_query[0] not in response.text
    assert service.requests == []


async def request_without_lifespan(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    """不进入 lifespan 直接发送 ASGI 请求。"""

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.parametrize(
    ("error", "status_code", "code", "retryable"),
    [
        (OllamaAuthenticationError("secret"), 502, "embedding_authentication_failed", False),
        (OllamaConnectionError("internal"), 503, "embedding_unavailable", True),
        (OllamaTimeoutError("internal"), 504, "embedding_timeout", True),
        (OllamaModelNotFoundError("internal"), 503, "embedding_model_not_found", False),
        (EmbeddingResponseError("internal"), 502, "embedding_response_invalid", False),
        (OllamaServiceError("internal"), 502, "embedding_unavailable", True),
        (OllamaEmbeddingError("internal"), 502, "embedding_unavailable", True),
        (QdrantSearchAuthenticationError("secret"), 502, "qdrant_authentication_failed", False),
        (QdrantSearchConnectionError("internal"), 503, "qdrant_unavailable", True),
        (QdrantSearchTimeoutError("internal"), 504, "qdrant_timeout", True),
        (QdrantSearchTargetNotFoundError("internal"), 503, "qdrant_target_missing", False),
        (QdrantSearchConfigurationError("internal"), 503, "qdrant_configuration_invalid", False),
        (QdrantSearchResponseError("internal"), 502, "qdrant_response_invalid", False),
        (QdrantSearchServiceError("internal"), 502, "qdrant_service_error", True),
        (QueryVectorValidationError("full vector must not leak"), 502, "embedding_response_invalid", False),
    ],
)
def test_known_upstream_errors_map_to_stable_http_contract(
    error: Exception,
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    service = FakeSearchService(error=error)
    app, _runtime = app_for(service)

    response = run(
        request(
            app,
            "POST",
            "/vector-search",
            json={"query": "安全短 query"},
        )
    )

    assert response.status_code == status_code
    body = response.json()
    parsed = VectorSearchErrorResponse.model_validate(body)
    assert parsed.code == code
    assert parsed.retryable is retryable
    assert "full vector" not in response.text
    assert "secret" not in response.text


def test_unknown_service_exception_is_not_silently_converted_to_empty_results() -> None:
    service = FakeSearchService(error=RuntimeError("非预期的实现故障"))
    app, _runtime = app_for(service)

    async def verify() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                with pytest.raises(RuntimeError, match="非预期的实现"):
                    await client.post(
                        "/vector-search",
                        json={"query": "安全短 query"},
                    )

    run(verify())


def test_openapi_exposes_search_route_and_error_schemas() -> None:
    service = FakeSearchService()
    app, _runtime = app_for(service)

    schema = app.openapi()
    operation = schema["paths"]["/vector-search"]["post"]

    assert operation["requestBody"]["content"]["application/json"]["schema"]
    assert operation["responses"]["200"]
    assert "502" in operation["responses"]
    assert "503" in operation["responses"]
    assert "504" in operation["responses"]


def test_missing_lifespan_runtime_is_the_same_503_for_both_search_routes() -> None:
    """Chunk 级与文档级搜索共用同一条 Runtime 缺失响应，不再各自处理一遍。

    不进入 lifespan，模拟进程启动失败或依赖被错误调用的边界；ASGITransport 本身不会
    自动管理 lifespan，因此这里不会构造 Runtime 或访问任一外部服务。
    """

    service = FakeSearchService()
    app, _runtime = app_for(service)

    chunk_response = run(
        request_without_lifespan(
            app,
            "POST",
            "/vector-search",
            json={"query": "安全测试"},
        )
    )
    document_response = run(
        request_without_lifespan(
            app,
            "POST",
            "/document-search",
            json={"query": "安全测试"},
        )
    )

    assert chunk_response.status_code == document_response.status_code == 503
    assert chunk_response.json() == {
        "code": "search_runtime_unavailable",
        "detail": "向量检索运行时不可用。",
        "retryable": False,
    }
    assert chunk_response.json() == document_response.json()
    assert service.requests == []
