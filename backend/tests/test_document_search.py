"""文档级 grouped vector search 的离线契约和排序测试。

测试只使用 fake Embedding 与 fake Qdrant grouped response，不访问项目环境变量、
Ollama、PostgreSQL 或远程 Qdrant。重点验证分组参数在基础设施层执行，而不是在
有限的前端条数上去重。
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

from agent_lab.api.error_contract import VectorSearchErrorResponse
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.search import (
    QdrantSearchResponseError,
    QdrantVectorSearch,
)
from agent_lab.schemas.document_search import (
    DEFAULT_DOCUMENT_LIMIT,
    MAX_DOCUMENT_LIMIT,
    MAX_MATCHES_PER_DOCUMENT,
    DocumentSearchRequest,
    DocumentSearchResult,
)
from agent_lab.schemas.vector_search import VectorSearchFilters
from agent_lab.services.vector_search_service import VectorSearchService
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_reader


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
        collection_schema_version="v3",
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
        "index_instance_id": str(document_id),
        "knowledge_base_id": str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID),
        "content_hash": content_hash,
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
        "title": title,
        "url": "https://example.com/news",
        "published_at": "2026-08-14T00:00:00+00:00",
        "document_type": "article",
        "mime_type": "text/plain",
        "source_id": str(uuid4()),
        "source_provider": "test",
        "source_name": "测试来源",
        "source_external_id": "feed/1",
        "document_external_id": f"article/{document_id}",
        "authors": [],
        "labels": ["宏观"],
        "index_schema_version": "v3",
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

    from tests.knowledge_helpers import ActiveKnowledgeBaseScope

    spec = VectorIndexSpec.from_settings(settings(), ollama_settings())
    provider = EmbeddingStub()
    component = QdrantVectorSearch(client, settings(), spec)
    return (
        VectorSearchService(
            embedding_provider=provider,  # type: ignore[arg-type]
            vector_search=component,
            spec=spec,
            knowledge_base_scope=ActiveKnowledgeBaseScope(),
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
                knowledge_base_id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID,
                document_limit=2,
                matches_per_document=2,
                score_threshold=0.6,
                filters=VectorSearchFilters(
                    published_from=datetime(2026, 8, 1, tzinfo=UTC)
                ),
            )
        )
    )

    assert [result.document_id for result in results] == [document_a, document_b]
    assert [result.best_score for result in results] == [0.91, 0.74]
    assert [match.score for match in results[0].additional_matches] == [0.86]
    assert results[0].best_match.chunk_index == 0
    # 来源、标签与 MIME 类型是命中载荷而不是过滤维度：它们仍需从 Payload 搬到结果里。
    assert results[0].source_name == "测试来源"
    assert results[0].labels == ["宏观"]
    assert results[0].mime_type == "text/plain"
    assert provider.queries == ["利率变化"]
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["collection_name"].endswith("_current")
    assert call["group_by"] == "index_instance_id"
    assert call["limit"] == 2
    assert call["group_size"] == 2
    assert call["score_threshold"] == pytest.approx(0.6)
    assert {condition.key: condition for condition in call["query_filter"].must} == {
        "knowledge_base_id": models.FieldCondition(
            key="knowledge_base_id",
            match=models.MatchValue(value=str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)),
        ),
        "published_at": models.FieldCondition(
            key="published_at",
            range=models.DatetimeRange(gte=datetime(2026, 8, 1, tzinfo=UTC)),
        ),
    }


def test_document_search_empty_groups_returns_empty_list() -> None:
    service, _provider = build_service(GroupedClient([]))

    assert run(service.search_documents(DocumentSearchRequest(query="无结果", knowledge_base_id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID))) == []


def test_document_search_service_applies_top_level_knowledge_base_scope() -> None:
    document_id = uuid4()
    client = GroupedClient(
        [
            SimpleNamespace(
                id=str(document_id),
                hits=[point(document_id, 0.8, chunk_index=0, chunk_count=1)],
            )
        ]
    )
    service, _provider = build_service(client)
    knowledge_base_id = uuid4()

    results = run(
        service.search_documents(
            DocumentSearchRequest(
                query="指定知识库",
                knowledge_base_id=knowledge_base_id,
            )
        )
    )

    assert len(results) == 1
    query_filter = client.calls[0]["query_filter"]
    assert query_filter is not None
    assert models.FieldCondition(
        key="knowledge_base_id",
        match=models.MatchValue(value=str(knowledge_base_id)),
    ) in query_filter.must


def test_equal_best_scores_break_ties_by_document_id_for_determinism() -> None:
    """最高分相同时按 document_id 字典序排，保证同样输入永远同样输出。"""

    first, second = sorted([uuid4(), uuid4()], key=str)
    # 故意用「字典序在后」的文档打头，验证排序键真的生效而不是保持输入顺序。
    client = GroupedClient(
        [
            SimpleNamespace(id=str(second), hits=[point(second, 0.8, chunk_index=0, chunk_count=1)]),
            SimpleNamespace(id=str(first), hits=[point(first, 0.8, chunk_index=0, chunk_count=1)]),
        ]
    )
    service, _provider = build_service(client)

    results = run(service.search_documents(DocumentSearchRequest(query="并列分数", knowledge_base_id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID)))

    assert [result.document_id for result in results] == [first, second]


def _group_id_not_uuid(document_id: UUID, _other_id: UUID) -> list[Any]:
    return [SimpleNamespace(id="not-a-uuid", hits=[point(document_id, 0.8, chunk_index=0, chunk_count=1)])]


def _group_without_hits(document_id: UUID, _other_id: UUID) -> list[Any]:
    return [SimpleNamespace(id=str(document_id), hits=[])]


def _same_document_in_two_groups(document_id: UUID, _other_id: UUID) -> list[Any]:
    return [
        SimpleNamespace(id=str(document_id), hits=[point(document_id, 0.8, chunk_index=0, chunk_count=1)]),
        SimpleNamespace(id=str(document_id), hits=[point(document_id, 0.7, chunk_index=0, chunk_count=1)]),
    ]


def _point_document_id_differs_from_group(document_id: UUID, other_id: UUID) -> list[Any]:
    return [SimpleNamespace(id=str(document_id), hits=[point(other_id, 0.8, chunk_index=0, chunk_count=1)])]


def _same_chunk_id_twice(document_id: UUID, _other_id: UUID) -> list[Any]:
    duplicate_chunk = models.ScoredPoint(
        id=uuid4(),
        version=1,
        score=0.8,
        payload=payload(document_id, chunk_index=0, chunk_count=2),
    )
    return [SimpleNamespace(id=str(document_id), hits=[duplicate_chunk, duplicate_chunk])]


def _inconsistent_document_metadata(document_id: UUID, _other_id: UUID) -> list[Any]:
    """同一 document_id 的两个 Chunk 给出不同标题。"""

    return [
        SimpleNamespace(
            id=str(document_id),
            hits=[
                point(document_id, 0.9, chunk_index=0, chunk_count=2, title="标题 A"),
                point(document_id, 0.8, chunk_index=1, chunk_count=2, title="标题 B"),
            ],
        )
    ]


@pytest.mark.parametrize(
    ("build_groups", "message"),
    [
        (_group_id_not_uuid, "不是 UUID"),
        (_group_without_hits, "没有命中结果"),
        (_same_document_in_two_groups, "重复出现了同一文档"),
        (_point_document_id_differs_from_group, "不同的 index_instance_id"),
        (_same_chunk_id_twice, "同一个 chunk_id"),
        (_inconsistent_document_metadata, "元数据不一致"),
    ],
    ids=[
        "group_id_not_uuid",
        "group_without_hits",
        "same_document_in_two_groups",
        "point_document_id_differs_from_group",
        "same_chunk_id_twice",
        "inconsistent_document_metadata",
    ],
)
def test_grouped_response_rejects_broken_group_contracts(
    build_groups: Any, message: str
) -> None:
    """search_groups 是分组不变量的唯一后端防线，逐项确认它仍然拒绝坏响应。

    用 parametrize 而不是循环：每个 case 独立报告，一个失败不会掩盖后面的 case。
    """

    document_id = uuid4()
    other_id = uuid4()
    service, _provider = build_service(GroupedClient(build_groups(document_id, other_id)))

    with pytest.raises(QdrantSearchResponseError, match=message):
        run(service.search_documents(DocumentSearchRequest(query="校验", knowledge_base_id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID)))


def test_document_search_request_rejects_sensitive_invalid_query_without_echo() -> None:
    sensitive = "private query " * 500
    with pytest.raises(ValidationError) as exc_info:
        DocumentSearchRequest(query=sensitive)
    assert sensitive not in str(exc_info.value)


def test_document_search_request_defaults_and_safe_repr() -> None:
    request = DocumentSearchRequest(query="包含敏感内容的检索词")

    assert request.document_limit == DEFAULT_DOCUMENT_LIMIT
    assert request.score_threshold is None
    assert request.filters.published_from is None
    assert request.filters.published_to is None
    assert "包含敏感内容" not in repr(request)


@pytest.mark.parametrize("value", ["", " \n\t "])
def test_document_search_request_rejects_empty_query(value: str) -> None:
    with pytest.raises(ValidationError, match="空白字符") as exc_info:
        DocumentSearchRequest(query=value)

    if value:
        assert value not in str(exc_info.value)


# 数量上限与 score_threshold 的非法取值。两个数量字段合成一条：断言完全相同（构造即抛
# ValidationError），分开写只是重复同一个 with 块。
# score_threshold 那几个值各有针对：±1.1 越界；nan/inf 非有限；True 是 bool（Python 里
# bool 是 int 的子类，不挡住的话 True 会被当成 1 收下）；"0.8" 是字符串，不能靠隐式转换。
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_limit", 0),
        ("document_limit", MAX_DOCUMENT_LIMIT + 1),
        ("matches_per_document", 0),
        ("matches_per_document", MAX_MATCHES_PER_DOCUMENT + 1),
        ("score_threshold", -1.1),
        ("score_threshold", 1.1),
        ("score_threshold", float("nan")),
        ("score_threshold", float("inf")),
        ("score_threshold", True),
        ("score_threshold", "0.8"),
    ],
)
def test_document_search_request_rejects_invalid_numeric_fields(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        DocumentSearchRequest(query="查询", **{field: value})  # type: ignore[arg-type]


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
    app = allow_reader(create_offline_app(runtime_factory=lambda: FakeRuntime(service)))

    response = run(
        http_request(
            app,
            json={
                "query": "央行利率",
                "document_limit": 7,
                "matches_per_document": 4,
                "filters": {"published_from": "2026-08-01T00:00:00+00:00"},
            },
        )
    )

    assert response.status_code == 200
    assert response.json() == []
    assert service.requests[0].document_limit == 7
    assert service.requests[0].matches_per_document == 4
    assert service.requests[0].knowledge_base_id == DEFAULT_NEWS_KNOWLEDGE_BASE_ID
    assert service.requests[0].filters.knowledge_base_id == DEFAULT_NEWS_KNOWLEDGE_BASE_ID


# 过滤维度只剩知识库范围与发布时间；四个没人填过的维度已从契约里删掉。因为
# VectorSearchFilters 是 extra="forbid"，带它们不再是静默忽略而是明确的 422。
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", str(uuid4())),
        ("source_provider", "freshrss_main"),
        ("document_type", "article"),
        ("labels", ["宏观"]),
    ],
)
def test_document_search_http_rejects_removed_filter_dimensions(
    field: str, value: object
) -> None:
    service = FakeDocumentSearchService()
    app = allow_reader(create_offline_app(runtime_factory=lambda: FakeRuntime(service)))

    response = run(
        http_request(app, json={"query": "旧维度", "filters": {field: value}})
    )

    assert response.status_code == 422
    locations = [error["loc"] for error in response.json()["detail"]]
    assert ["body", "filters", field] in locations
    assert service.requests == []


def test_document_search_http_preserves_explicit_knowledge_base_scope() -> None:
    """显式 KnowledgeBase ID 传入后必须成为 Qdrant 的强制过滤条件。"""

    service = FakeDocumentSearchService()
    app = allow_reader(create_offline_app(runtime_factory=lambda: FakeRuntime(service)))
    knowledge_base_id = uuid4()

    response = run(
        http_request(
            app,
            json={
                "query": "指定范围",
                "knowledge_base_id": str(knowledge_base_id),
            },
        )
    )

    assert response.status_code == 200
    forwarded = service.requests[0]
    assert forwarded.knowledge_base_id == knowledge_base_id
    assert forwarded.filters.knowledge_base_id == knowledge_base_id


def test_document_search_http_preserves_known_upstream_error_mapping() -> None:
    from agent_lab.qdrant.search import QdrantSearchTimeoutError

    class ErrorService(FakeDocumentSearchService):
        async def search_documents(self, request: DocumentSearchRequest) -> list[DocumentSearchResult]:
            """抛出已分类 timeout，验证共享错误映射。"""

            raise QdrantSearchTimeoutError("敏感的上游细节信息")

    service = ErrorService()
    app = allow_reader(create_offline_app(runtime_factory=lambda: FakeRuntime(service)))
    response = run(http_request(app, json={"query": "安全查询"}))

    assert response.status_code == 504
    parsed = VectorSearchErrorResponse.model_validate(response.json())
    assert parsed.code == "qdrant_timeout"
    assert "sensitive" not in response.text


async def request_without_lifespan(
    app: FastAPI, path: str = "/document-search", **kwargs: Any
) -> httpx.Response:
    """不进入 lifespan 直接发送 ASGI 请求。"""

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, **kwargs)


def test_missing_lifespan_runtime_is_a_stable_503() -> None:
    """Runtime 缺失时依赖在进入路由前投错，由应用级 handler 映射成同一份 503。

    不进入 lifespan，模拟进程启动失败或依赖被错误调用的边界；ASGITransport 本身不会
    自动管理 lifespan，因此这里不会构造 Runtime 或访问任一外部服务。
    """

    service = FakeDocumentSearchService()
    app = allow_reader(create_offline_app(runtime_factory=lambda: FakeRuntime(service)))

    response = run(request_without_lifespan(app, json={"query": "安全测试"}))

    assert response.status_code == 503
    assert response.json() == {
        "code": "search_runtime_unavailable",
        "detail": "向量检索运行时不可用。",
        "retryable": False,
    }
    assert service.requests == []


def test_contract_only_exposes_document_search() -> None:
    """对外契约只剩文档分组检索：旧 Chunk 级路由连同它的请求模型一起消失。"""

    service = FakeDocumentSearchService()
    app = allow_reader(create_offline_app(runtime_factory=lambda: FakeRuntime(service)))
    schema = app.openapi()

    operation = schema["paths"]["/document-search"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]
    assert operation["responses"]["200"]
    for status_code in ("502", "503", "504"):
        assert status_code in operation["responses"]

    assert "/vector-search" not in schema["paths"]
    assert "VectorSearchRequest" not in schema["components"]["schemas"]
    assert "ScopedVectorSearchResponse" not in schema["components"]["schemas"]

    # 过滤器只剩知识库范围与发布时间两个维度，四个空转维度的字段在契约里不存在。
    filter_properties = schema["components"]["schemas"]["VectorSearchFilters"]["properties"]
    assert set(filter_properties) == {
        "knowledge_base_id",
        "knowledge_base_ids",
        "published_from",
        "published_to",
    }

    # 路由不存在时就是普通 404：没有隐藏的内部别名。
    removed = run(
        request_without_lifespan(app, path="/vector-search", json={"query": "旧的按片段检索"})
    )
    assert removed.status_code == 404
