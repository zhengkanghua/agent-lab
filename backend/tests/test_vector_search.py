"""阶段 3 Vector Search 的请求校验、只读编排、过滤和内存 Qdrant 测试。

默认测试只使用 fake Embeddings 与 qdrant-client ``:memory:`` 模式，不访问远程
Ollama、PostgreSQL 或远程 Qdrant。测试重点是 query/document Embedding 边界、current
Alias、Qdrant 原始 score 顺序、Payload 响应契约和搜索不执行任何写操作。
"""

import asyncio
import math
import warnings
from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from ollama import ResponseError
from pydantic import SecretStr, ValidationError
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import UnexpectedResponse

from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.domain.enums import DocumentType
from agent_lab.pipeline.ollama_embedding_provider import (
    OllamaEmbeddingProvider,
)
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.lifecycle import QdrantCollectionLifecycle
from agent_lab.qdrant.search import (
    QdrantSearchAuthenticationError,
    QdrantSearchConfigurationError,
    QdrantSearchConnectionError,
    QdrantSearchResponseError,
    QdrantSearchServiceError,
    QdrantSearchTargetNotFoundError,
    QdrantSearchTimeoutError,
    QdrantVectorSearch,
)
from agent_lab.schemas.vector_search import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    VectorSearchFilters,
    VectorSearchRequest,
)
from agent_lab.services.vector_search_service import (
    QueryVectorValidationError,
    VectorSearchService,
)


def run(coroutine: Any) -> Any:
    """执行一个测试协程，保持测试环境不依赖 pytest-asyncio。"""

    return asyncio.run(coroutine)


class FakeEmbeddings:
    """记录 LangChain 异步 Embedding 调用，确保 query/document 意图不混淆。"""

    def __init__(self, query_response: Any) -> None:
        self.query_response = query_response
        self.query_calls: list[str] = []
        self.document_calls: list[list[str]] = []

    async def aembed_query(self, text: str) -> list[float]:
        """返回预置 query 向量或抛出预置异常。"""

        self.query_calls.append(text)
        if isinstance(self.query_response, Exception):
            raise self.query_response
        return self.query_response

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """搜索流程若错误调用 document API，应在测试中明确暴露。"""

        self.document_calls.append(list(texts))
        raise AssertionError("Vector Search 必须调用 embed_query，而不是 embed_documents")


def qdrant_settings(*, environment: str = "search_test") -> QdrantSettings:
    """创建不读取项目 .env 的固定 Qdrant 配置。"""

    return QdrantSettings(
        _env_file=None,
        base_url="http://qdrant.example.test:6333",
        api_key=SecretStr("search-secret-must-not-leak"),
        request_timeout_seconds=7,
        environment=environment,
        collection_schema_version="v1",
        collection_generation=1,
        vector_dimension=3,
        distance="Cosine",
    )


def ollama_settings() -> OllamaEmbeddingSettings:
    """创建不读取项目 .env 的 fake Ollama 配置。"""

    return OllamaEmbeddingSettings(
        _env_file=None,
        base_url="https://ollama.example.test",
        embedding_model="bge-m3:567m",
        api_key=SecretStr("ollama-secret-must-not-leak"),
        embedding_request_timeout_seconds=7,
        embedding_batch_size=2,
    )


def build_payload(
    *,
    document_id: UUID | None = None,
    source_id: UUID | None = None,
    source_provider: str = "freshrss_main",
    document_type: str = "article",
    labels: list[str] | None = None,
    published_at: str | None = "2026-08-13T01:02:03+00:00",
    chunk_index: int = 0,
    chunk_count: int = 1,
    **overrides: Any,
) -> dict[str, Any]:
    """构造完整的阶段 2 扁平新闻 Payload。"""

    payload: dict[str, Any] = {
        "page_content": f"新闻正文 chunk {chunk_index}",
        "document_id": str(document_id or uuid4()),
        "content_hash": "a" * 64,
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
        "title": f"示例新闻 {chunk_index}",
        "url": "https://example.com/news",
        "document_type": document_type,
        "source_id": str(source_id or uuid4()),
        "source_provider": source_provider,
        "source_name": "示例来源",
        "source_external_id": "feed/2",
        "document_external_id": "article/42",
        "authors": ["作者甲"],
        "labels": labels if labels is not None else ["宏观", "利率"],
        "index_schema_version": "v1",
        "embedding_model": "bge-m3:567m",
    }
    if published_at is not None:
        payload["published_at"] = published_at
    payload.update(overrides)
    return payload


def build_point(
    *,
    point_id: UUID | None = None,
    vector: list[float] | None = None,
    payload: dict[str, Any] | None = None,
    score: float | None = None,
) -> models.PointStruct | models.ScoredPoint:
    """构造可用于内存写入或 fake 响应的 Qdrant Point。"""

    point_id = point_id or uuid4()
    vector = vector or [1.0, 0.0, 0.0]
    if score is None:
        return models.PointStruct(
            id=point_id,
            vector=vector,
            payload=payload or build_payload(),
        )
    return models.ScoredPoint(
        id=point_id,
        version=1,
        score=score,
        payload=payload or build_payload(),
    )


def build_runtime_components(
    *,
    fake_embeddings: FakeEmbeddings,
    client: AsyncQdrantClient | Any,
    environment: str = "search_test",
) -> tuple[VectorSearchService, QdrantVectorSearch, VectorIndexSpec, QdrantSettings]:
    """组装不访问网络的搜索 Service 与 Qdrant 组件。"""

    settings = qdrant_settings(environment=environment)
    spec = VectorIndexSpec.from_settings(settings, ollama_settings())
    provider = OllamaEmbeddingProvider(ollama_settings(), embeddings=fake_embeddings)  # type: ignore[arg-type]
    component = QdrantVectorSearch(client, settings, spec)
    service = VectorSearchService(
        embedding_provider=provider,
        vector_search=component,
        spec=spec,
    )
    return service, component, spec, settings


def test_request_defaults_and_safe_repr() -> None:
    request = VectorSearchRequest(query="包含敏感内容的检索词")

    assert request.top_k == DEFAULT_TOP_K
    assert request.score_threshold is None
    assert request.filters.labels == ()
    assert "包含敏感内容" not in repr(request)


@pytest.mark.parametrize(
    "value",
    ["", " \n\t "],
)
def test_request_rejects_empty_query(value: str) -> None:
    with pytest.raises(ValidationError, match="空白字符") as exc_info:
        VectorSearchRequest(query=value)

    if value:
        assert value not in str(exc_info.value)


# top_k 与 score_threshold 的非法取值。两个字段合成一条：断言完全相同（构造即抛
# ValidationError），分开写只是重复同一个 with 块。
# score_threshold 那几个值各有针对：±1.1 越界；nan/inf 非有限；True 是 bool（Python 里
# bool 是 int 的子类，不挡住的话 True 会被当成 1 收下）；"0.8" 是字符串，不能靠隐式转换。
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("top_k", 0),
        ("top_k", MAX_TOP_K + 1),
        ("score_threshold", -1.1),
        ("score_threshold", 1.1),
        ("score_threshold", float("nan")),
        ("score_threshold", float("inf")),
        ("score_threshold", True),
        ("score_threshold", "0.8"),
    ],
)
def test_request_rejects_invalid_numeric_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        VectorSearchRequest(query="查询", **{field: value})  # type: ignore[arg-type]


def test_filters_reject_naive_datetime_and_reversed_range() -> None:
    with pytest.raises(ValidationError, match="时区"):
        VectorSearchFilters(published_from=datetime(2026, 1, 1))
    with pytest.raises(ValidationError, match="晚于"):
        VectorSearchFilters(
            published_from=datetime(2026, 2, 1, tzinfo=UTC),
            published_to=datetime(2026, 1, 1, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="时间戳"):
        VectorSearchFilters(published_from=1786579200)  # type: ignore[arg-type]


def test_filters_define_empty_labels_as_no_filter_and_any_match() -> None:
    empty = VectorSearchFilters(labels=[])
    normalized = VectorSearchFilters(labels=[" 宏观 ", "宏观", "利率"])

    assert empty.labels == ()
    assert normalized.labels == ("宏观", "利率")
    built = QdrantVectorSearch._build_filter(normalized)  # noqa: SLF001
    assert built is not None
    assert built.must[0].key == "labels"  # type: ignore[union-attr]
    assert built.must[0].match.any == ["宏观", "利率"]  # type: ignore[union-attr]
    assert QdrantVectorSearch._build_filter(empty) is None  # noqa: SLF001


def test_each_filter_uses_the_expected_qdrant_model() -> None:
    source_id = uuid4()
    published_from = datetime(2026, 1, 1, tzinfo=UTC)
    published_to = datetime(2026, 2, 1, tzinfo=UTC)
    built = QdrantVectorSearch._build_filter(  # noqa: SLF001
        VectorSearchFilters(
            source_id=source_id,
            source_provider="provider_a",
            document_type=DocumentType.PRESS_RELEASE,
            labels=["宏观", "利率"],
            published_from=published_from,
            published_to=published_to,
        )
    )

    assert built is not None
    conditions = {condition.key: condition for condition in built.must}  # type: ignore[union-attr]
    assert conditions["source_id"].match == models.MatchValue(value=str(source_id))
    assert conditions["source_provider"].match == models.MatchValue(value="provider_a")
    assert conditions["document_type"].match == models.MatchValue(
        value="press_release"
    )
    assert conditions["labels"].match == models.MatchAny(any=["宏观", "利率"])
    assert conditions["published_at"].range == models.DatetimeRange(
        gte=published_from,
        lte=published_to,
    )


@pytest.mark.parametrize(
    ("field", "filters", "expected"),
    [
        (
            "published_at",
            VectorSearchFilters(published_from=datetime(2026, 1, 1, tzinfo=UTC)),
            models.DatetimeRange(gte=datetime(2026, 1, 1, tzinfo=UTC)),
        ),
        (
            "published_at",
            VectorSearchFilters(published_to=datetime(2026, 2, 1, tzinfo=UTC)),
            models.DatetimeRange(lte=datetime(2026, 2, 1, tzinfo=UTC)),
        ),
    ],
)
def test_each_time_boundary_can_be_used_independently(
    field: str,
    filters: VectorSearchFilters,
    expected: models.DatetimeRange,
) -> None:
    built = QdrantVectorSearch._build_filter(filters)  # noqa: SLF001

    assert built is not None
    condition = built.must[0]  # type: ignore[index]
    assert condition.key == field
    assert condition.range == expected


def test_query_service_uses_embed_query_and_qdrant_current_alias() -> None:
    async def verify() -> None:
        fake_embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
        client = AsyncQdrantClient(location=":memory:")
        service, component, spec, settings = build_runtime_components(
            fake_embeddings=fake_embeddings,
            client=client,
        )
        query_calls: list[dict[str, Any]] = []
        original_query_points = client.query_points

        async def spy_query_points(**kwargs: Any) -> Any:
            query_calls.append(kwargs)
            return await original_query_points(**kwargs)

        client.query_points = spy_query_points  # type: ignore[method-assign]
        collection = settings.collection_name
        await client.create_collection(collection, vectors_config=spec.vector_params)
        await client.update_collection_aliases(
            [
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name=collection,
                        alias_name=settings.collection_alias,
                    )
                )
            ]
        )
        point_id = uuid4()
        try:
            await client.upsert(
                collection_name=settings.collection_alias,
                points=[
                    build_point(
                        point_id=point_id,
                        vector=[1.0, 0.0, 0.0],
                        payload=build_payload(),
                    )
                ],
            )
            results = await service.search(VectorSearchRequest(query="安全 query"))
            assert fake_embeddings.query_calls == ["安全 query"]
            assert fake_embeddings.document_calls == []
            assert results[0].chunk_id == point_id
            assert results[0].score == pytest.approx(1.0)
            assert query_calls[0]["collection_name"] == settings.collection_alias
            assert collection not in [call["collection_name"] for call in query_calls]
            assert query_calls[0]["limit"] == DEFAULT_TOP_K
            assert query_calls[0]["with_vectors"] is False
        finally:
            await client.close()

    run(verify())


def test_memory_qdrant_cosine_score_threshold_and_order_are_preserved() -> None:
    async def verify() -> None:
        fake_embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
        client = AsyncQdrantClient(location=":memory:")
        service, _component, spec, settings = build_runtime_components(
            fake_embeddings=fake_embeddings,
            client=client,
            environment="threshold_test",
        )
        lifecycle = QdrantCollectionLifecycle(client, settings, spec)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            await lifecycle.ensure_current_collection()
        points = [
            build_point(
                point_id=uuid4(),
                vector=[1.0, 0.0, 0.0],
                payload=build_payload(labels=["命中"]),
            ),
            build_point(
                point_id=uuid4(),
                vector=[0.6, 0.8, 0.0],
                payload=build_payload(labels=["另一个"]),
            ),
            build_point(
                point_id=uuid4(),
                vector=[0.0, 1.0, 0.0],
                payload=build_payload(labels=["低分"]),
            ),
        ]
        try:
            await client.upsert(settings.collection_alias, points)
            all_results = await service.search(
                VectorSearchRequest(query="排序", top_k=3)
            )
            threshold_results = await service.search(
                VectorSearchRequest(query="阈值", top_k=3, score_threshold=0.7)
            )
            assert [result.chunk_id for result in all_results] == [
                UUID(str(point.id)) for point in points
            ]
            assert [result.score for result in all_results] == pytest.approx(
                [1.0, 0.6, 0.0]
            )
            assert [result.score for result in threshold_results] == pytest.approx(
                [1.0]
            )
        finally:
            await client.close()

    run(verify())


def test_memory_qdrant_combined_filters_and_missing_published_at_semantics() -> None:
    async def verify() -> None:
        fake_embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
        client = AsyncQdrantClient(location=":memory:")
        service, _component, spec, settings = build_runtime_components(
            fake_embeddings=fake_embeddings,
            client=client,
            environment="filter_test",
        )
        lifecycle = QdrantCollectionLifecycle(client, settings, spec)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            await lifecycle.ensure_current_collection()
        source_id = uuid4()
        matching = build_point(
            point_id=uuid4(),
            vector=[1.0, 0.0, 0.0],
            payload=build_payload(
                source_id=source_id,
                source_provider="provider_a",
                document_type="article",
                labels=["宏观", "利率"],
                published_at="2026-08-13T01:02:03+00:00",
            ),
        )
        missing_time = build_point(
            point_id=uuid4(),
            vector=[0.99, 0.01, 0.0],
            payload=build_payload(
                source_id=source_id,
                source_provider="provider_a",
                labels=["宏观"],
                published_at=None,
            ),
        )
        wrong_source = build_point(
            point_id=uuid4(),
            vector=[0.98, 0.02, 0.0],
            payload=build_payload(
                source_id=uuid4(),
                source_provider="provider_a",
                labels=["宏观"],
                published_at="2026-08-13T01:02:03+00:00",
            ),
        )
        try:
            await client.upsert(settings.collection_alias, [matching, missing_time, wrong_source])
            request = VectorSearchRequest(
                query="组合过滤",
                filters=VectorSearchFilters(
                    source_id=source_id,
                    source_provider="provider_a",
                    document_type=DocumentType.ARTICLE,
                    labels=["不存在", "利率"],
                    published_from=datetime(2026, 8, 1, tzinfo=UTC),
                    published_to=datetime(2026, 8, 31, tzinfo=UTC),
                ),
            )
            results = await service.search(request)
            assert [result.chunk_id for result in results] == [UUID(str(matching.id))]
            assert results[0].published_at == datetime(
                2026, 8, 13, 1, 2, 3, tzinfo=UTC
            )
        finally:
            await client.close()

    run(verify())


def test_search_service_rejects_invalid_query_vectors_before_qdrant_call() -> None:
    class FailIfCalledClient:
        """任何方法被调用都说明 query Vector 校验发生得太晚。"""

        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"不允许调用 Qdrant 方法 {name}")

    class InvalidVectorProvider:
        """绕过 Embedding Provider 响应校验，单测 Service 防御性校验。"""

        embedding_model = "bge-m3:567m"

        def __init__(self, vector: Sequence[object]) -> None:
            self.vector = vector
            self.query_calls: list[str] = []

        async def embed_query(self, text: str) -> Sequence[object]:
            self.query_calls.append(text)
            return self.vector

    vectors = [
        ([1.0, 2.0], "维度不匹配"),
        ([0.0, 0.0, 0.0], "L2 范数为零"),
        ([1.0, math.nan, 0.0], "不是有限值"),
        ([1.0, math.inf, 0.0], "不是有限值"),
        ([1.7e308, 1.7e308, 1.7e308], "L2 范数不是有限值"),
    ]
    for vector, message in vectors:
        settings = qdrant_settings()
        spec = VectorIndexSpec.from_settings(settings, ollama_settings())
        provider = InvalidVectorProvider(vector)
        component = QdrantVectorSearch(FailIfCalledClient(), settings, spec)
        service = VectorSearchService(
            embedding_provider=provider,  # type: ignore[arg-type]
            vector_search=component,
            spec=spec,
        )
        with pytest.raises(QueryVectorValidationError, match=message):
            run(service.search(VectorSearchRequest(query="向量校验")))
        assert provider.query_calls == ["向量校验"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            UnexpectedResponse(
                401,
                "Unauthorized",
                b"secret-key",
                httpx.Headers(),
            ),
            QdrantSearchAuthenticationError,
        ),
        (
            UnexpectedResponse(404, "Not Found", b"internal", httpx.Headers()),
            QdrantSearchTargetNotFoundError,
        ),
        (
            UnexpectedResponse(400, "Bad Request", b"dimension", httpx.Headers()),
            QdrantSearchConfigurationError,
        ),
        (
            UnexpectedResponse(500, "Server Error", b"internal", httpx.Headers()),
            QdrantSearchServiceError,
        ),
        (
            UnexpectedResponse(502, "Bad Gateway", b"internal", httpx.Headers()),
            QdrantSearchServiceError,
        ),
        (
            UnexpectedResponse(
                503,
                "Service Unavailable",
                b"internal",
                httpx.Headers(),
            ),
            QdrantSearchServiceError,
        ),
        (httpx.ReadTimeout("timeout"), QdrantSearchTimeoutError),
        (httpx.ConnectError("connection"), QdrantSearchConnectionError),
        (ConnectionError("connection"), QdrantSearchConnectionError),
    ],
)
def test_qdrant_search_maps_remote_errors_without_secrets(
    error: Exception,
    expected: type[Exception],
) -> None:
    class ErrorClient:
        async def query_points(self, **kwargs: Any) -> Any:
            raise error

    fake_embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
    _service, component, _spec, _settings = build_runtime_components(
        fake_embeddings=fake_embeddings,
        client=ErrorClient(),
    )
    secret = "secret-key"
    with pytest.raises(expected) as exc_info:
        run(
            component.search(
                [1.0, 0.0, 0.0],
                top_k=1,
                score_threshold=None,
                filters=VectorSearchFilters(),
            )
        )
    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)


def test_missing_alias_from_memory_qdrant_is_classified() -> None:
    async def verify() -> None:
        client = AsyncQdrantClient(location=":memory:")
        fake_embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
        _service, component, _spec, _settings = build_runtime_components(
            fake_embeddings=fake_embeddings,
            client=client,
            environment="missing_alias_test",
        )
        try:
            with pytest.raises(QdrantSearchTargetNotFoundError):
                await component.search(
                    [1.0, 0.0, 0.0],
                    top_k=1,
                    score_threshold=None,
                    filters=VectorSearchFilters(),
                )
        finally:
            await client.close()

    run(verify())


def test_search_response_requires_uuid_and_complete_typed_payload() -> None:
    valid_point_id = uuid4()
    valid_payload = build_payload()

    class ResponseClient:
        def __init__(self, point: Any) -> None:
            self.point = point

        async def query_points(self, **kwargs: Any) -> Any:
            return SimpleNamespace(points=[self.point])

    cases = [
        (SimpleNamespace(id="not-a-uuid", score=1.0, payload=valid_payload), "不是 UUID"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload=None), "缺少对象形式"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={**valid_payload, "document_id": "bad"}), "document_id"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={**valid_payload, "source_id": "bad"}), "source_id"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={**valid_payload, "document_id": uuid4()}), "document_id"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={key: value for key, value in valid_payload.items() if key != "page_content"}), "page_content"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={**valid_payload, "published_at": "2026-08-13T00:00:00"}), "published_at"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={**valid_payload, "published_at": 1786579200}), "published_at"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={**valid_payload, "labels": "宏观"}), "labels"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={**valid_payload, "chunk_index": 2}), "响应契约"),
        # index_schema_version 不进 VectorSearchResult，所以它的把关全靠 _map_point 里
        # 对 Payload 的等值比较：写坏、缺失都必须拒绝，否则会把别的索引空间的数据搜出来。
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={**valid_payload, "index_schema_version": "v2"}), "非预期的索引"),
        (SimpleNamespace(id=valid_point_id, score=1.0, payload={key: value for key, value in valid_payload.items() if key != "index_schema_version"}), "非预期的索引"),
    ]
    for point, message in cases:
        fake_embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
        _service, component, _spec, _settings = build_runtime_components(
            fake_embeddings=fake_embeddings,
            client=ResponseClient(point),
        )
        with pytest.raises(QdrantSearchResponseError, match=message):
            run(
                component.search(
                    [1.0, 0.0, 0.0],
                    top_k=1,
                    score_threshold=None,
                    filters=VectorSearchFilters(),
                )
            )


def test_optional_payload_fields_are_none_when_missing() -> None:
    point = models.ScoredPoint(
        id=uuid4(),
        version=1,
        score=0.75,
        payload=build_payload(published_at=None),
    )

    class ResponseClient:
        async def query_points(self, **kwargs: Any) -> Any:
            return SimpleNamespace(points=[point])

    fake_embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
    _service, component, _spec, _settings = build_runtime_components(
        fake_embeddings=fake_embeddings,
        client=ResponseClient(),
    )
    result = run(
        component.search(
            [1.0, 0.0, 0.0],
            top_k=1,
            score_threshold=None,
            filters=VectorSearchFilters(),
        )
    )[0]

    assert result.published_at is None
    assert result.source_updated_at is None
    assert result.previous_chunk_id is None
    assert result.next_chunk_id is None


def test_same_document_chunk_hits_are_not_aggregated_or_reordered() -> None:
    document_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    points = [
        models.ScoredPoint(
            id=first_id,
            version=1,
            score=0.91,
            payload=build_payload(
                document_id=document_id,
                chunk_index=0,
                chunk_count=2,
                next_chunk_id=str(second_id),
            ),
        ),
        models.ScoredPoint(
            id=second_id,
            version=1,
            score=0.82,
            payload=build_payload(
                document_id=document_id,
                chunk_index=1,
                chunk_count=2,
                previous_chunk_id=str(first_id),
            ),
        ),
    ]

    class ResponseClient:
        async def query_points(self, **kwargs: Any) -> Any:
            return SimpleNamespace(points=points)

    fake_embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
    _service, component, _spec, _settings = build_runtime_components(
        fake_embeddings=fake_embeddings,
        client=ResponseClient(),
    )
    results = run(
        component.search(
            [1.0, 0.0, 0.0],
            top_k=2,
            score_threshold=None,
            filters=VectorSearchFilters(),
        )
    )

    assert [result.chunk_id for result in results] == [first_id, second_id]
    assert [result.document_id for result in results] == [document_id, document_id]
    assert [result.score for result in results] == [0.91, 0.82]


def test_search_has_no_qdrant_write_methods_or_physical_collection_name() -> None:
    class ReadOnlySpyClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def query_points(self, **kwargs: Any) -> Any:
            self.calls.append("query_points")
            assert kwargs["collection_name"] == "news_chunks_read_only_test_current"
            return SimpleNamespace(
                points=[
                    models.ScoredPoint(
                        id=uuid4(),
                        version=1,
                        score=0.5,
                        payload=build_payload(),
                    )
                ]
            )

        def __getattr__(self, name: str) -> Any:
            if name in {"upsert", "delete", "create_collection", "update_collection_aliases"}:
                raise AssertionError(f"只读检索调用了被禁止的方法 {name}")
            raise AttributeError(name)

    fake_embeddings = FakeEmbeddings([1.0, 0.0, 0.0])
    client = ReadOnlySpyClient()
    _service, component, _spec, settings = build_runtime_components(
        fake_embeddings=fake_embeddings,
        client=client,
        environment="read_only_test",
    )
    results = run(
        component.search(
            [1.0, 0.0, 0.0],
            top_k=1,
            score_threshold=None,
            filters=VectorSearchFilters(),
        )
    )
    assert len(results) == 1
    assert client.calls == ["query_points"]
    assert component.collection_name == settings.collection_alias
    assert settings.collection_name not in repr(component)
