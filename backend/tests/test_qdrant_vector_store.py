"""Qdrant 配置、规格、Payload、Alias 生命周期与 Point Store 的离线测试。"""

import asyncio
import warnings
import math
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.documents import Document
from pydantic import SecretStr, ValidationError
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from news_vector_service.config.ollama_embedding import OllamaEmbeddingSettings
from news_vector_service.config.qdrant import QdrantSettings
from news_vector_service.qdrant.index_spec import (
    VectorIndexConfigurationError,
    VectorIndexSpec,
)
from news_vector_service.qdrant.lifecycle import (
    PAYLOAD_INDEX_SCHEMAS,
    QdrantAliasConflictError,
    QdrantCollectionLifecycle,
    build_qdrant_client,
)
from news_vector_service.qdrant.payload import (
    QdrantPayloadError,
    QdrantPayloadMapper,
)
from news_vector_service.qdrant.store import QdrantChunkStore, QdrantPointStoreError


def run(coroutine: Any) -> Any:
    """执行一个测试协程，保持默认 pytest 完全离线且无需异步插件。"""

    return asyncio.run(coroutine)


def qdrant_settings(
    *,
    environment: str = "test",
    generation: int = 1,
    batch_size: int = 2,
) -> QdrantSettings:
    """创建不读取项目 .env 的确定性 Qdrant 配置。"""

    return QdrantSettings(
        _env_file=None,
        base_url="http://qdrant.example.test:6333",
        api_key=SecretStr(""),
        request_timeout_seconds=5,
        environment=environment,
        collection_schema_version="v1",
        collection_generation=generation,
        write_batch_size=batch_size,
        vector_dimension=1024,
        distance="Cosine",
    )


def spec(*, dimension: int = 3) -> VectorIndexSpec:
    """构造小维度测试规格，生产默认仍为 1024。"""

    return VectorIndexSpec(dimension=dimension)


def build_chunk(
    *,
    chunk_id: str | None = None,
    document_id: str | None = None,
    chunk_index: int = 0,
    chunk_count: int = 1,
) -> Document:
    """构造满足阶段 2 Payload 契约的 LangChain Chunk。"""

    document_id = document_id or str(uuid4())
    metadata: dict[str, Any] = {
        "document_id": document_id,
        "source_id": str(uuid4()),
        "source_provider": "freshrss_main",
        "source_external_id": "feed/2",
        "document_external_id": "article/42",
        "content_hash": "a" * 64,
        "document_type": "article",
        "title": "示例新闻",
        "url": "https://example.com/news/42",
        "source_name": "示例来源",
        "authors": ["作者甲"],
        "labels": ["宏观", "利率"],
        "published_at": datetime(2026, 8, 13, 1, 2, 3, tzinfo=UTC).isoformat(),
        "source_updated_at": datetime(2026, 8, 13, 2, 3, 4, tzinfo=UTC).isoformat(),
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
    }
    return Document(
        id=chunk_id or str(uuid4()),
        page_content=f"第 {chunk_index + 1} 段新闻正文",
        metadata=metadata,
    )


def test_qdrant_settings_defaults_and_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "QDRANT_BASE_URL",
        "QDRANT_API_KEY",
        "QDRANT_REQUEST_TIMEOUT_SECONDS",
        "QDRANT_ENVIRONMENT",
        "QDRANT_COLLECTION_SCHEMA_VERSION",
        "QDRANT_COLLECTION_GENERATION",
        "QDRANT_WRITE_BATCH_SIZE",
        "QDRANT_VECTOR_DIMENSION",
        "QDRANT_DISTANCE",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = QdrantSettings(_env_file=None)

    assert str(settings.base_url) == "http://localhost:6333/"
    assert settings.api_key.get_secret_value() == ""
    assert settings.collection_name == "news_chunks_dev_v1_001"
    assert settings.collection_alias == "news_chunks_dev_current"
    assert settings.vector_dimension == 1024
    assert settings.distance == "Cosine"


def test_qdrant_settings_parse_environment_and_hide_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "qdrant-test-secret"
    monkeypatch.setenv("QDRANT_BASE_URL", "https://qdrant.example.test")
    monkeypatch.setenv("QDRANT_API_KEY", secret)
    monkeypatch.setenv("QDRANT_ENVIRONMENT", "PROD")
    monkeypatch.setenv("QDRANT_COLLECTION_SCHEMA_VERSION", "v2")
    monkeypatch.setenv("QDRANT_COLLECTION_GENERATION", "7")
    monkeypatch.setenv("QDRANT_WRITE_BATCH_SIZE", "12")

    settings = QdrantSettings(_env_file=None)

    assert settings.collection_name == "news_chunks_prod_v2_007"
    assert settings.collection_alias == "news_chunks_prod_current"
    assert settings.write_batch_size == 12
    assert secret not in repr(settings)
    assert secret not in str(settings)


def test_qdrant_client_repr_does_not_leak_secret() -> None:
    secret = "never-repr-qdrant-secret"
    settings = QdrantSettings(
        _env_file=None,
        base_url="https://qdrant.example.test",
        api_key=SecretStr(secret),
    )
    client = build_qdrant_client(settings)

    async def close_client() -> None:
        try:
            assert secret not in repr(client)
            assert client._init_options["check_compatibility"] is False  # noqa: SLF001
            assert client._init_options["port"] is None  # noqa: SLF001
            assert client._client.rest_uri == "https://qdrant.example.test/"  # noqa: SLF001
        finally:
            await client.close()

    run(close_client())


def test_qdrant_client_preserves_explicit_reverse_proxy_port() -> None:
    settings = QdrantSettings(
        _env_file=None,
        base_url="https://qdrant.example.test:7443/prefix",
    )
    client = build_qdrant_client(settings)

    async def close_client() -> None:
        try:
            assert client._client.rest_uri == (  # noqa: SLF001
                "https://qdrant.example.test:7443/prefix"
            )
        finally:
            await client.close()

    run(close_client())


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": "not-a-url"},
        {"environment": "prod/current"},
        {"collection_schema_version": "version-one"},
        {"collection_generation": 0},
        {"write_batch_size": 0},
        {"vector_dimension": 0},
        {"distance": "Dot"},
    ],
)
def test_qdrant_settings_reject_invalid_values(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "_env_file": None,
        "base_url": "http://qdrant.example.test",
        "environment": "test",
        "collection_schema_version": "v1",
        "collection_generation": 1,
        "write_batch_size": 2,
        "vector_dimension": 1024,
        "distance": "Cosine",
    }
    values.update(overrides)

    with pytest.raises(ValidationError):
        QdrantSettings(**values)  # type: ignore[arg-type]


def test_index_spec_is_built_from_settings() -> None:
    qdrant = qdrant_settings()
    ollama = OllamaEmbeddingSettings(
        _env_file=None,
        embedding_model="bge-m3:567m",
    )

    result = VectorIndexSpec.from_settings(qdrant, ollama)

    assert result.dimension == 1024
    assert result.distance == models.Distance.COSINE
    assert result.embedding_model == "bge-m3:567m"
    assert result.vector_params.size == 1024
    assert result.collection_metadata["chunk_size"] == 512


def test_payload_mapper_keeps_news_time_and_explicit_fields() -> None:
    chunk = build_chunk()

    payload = QdrantPayloadMapper(spec()).build(chunk)

    assert payload["page_content"] == chunk.page_content
    assert payload["published_at"] == "2026-08-13T01:02:03+00:00"
    assert payload["source_updated_at"] == "2026-08-13T02:03:04+00:00"
    assert payload["document_id"] == chunk.metadata["document_id"]
    assert payload["labels"] == ["宏观", "利率"]
    assert payload["index_schema_version"] == "v1"
    assert payload["embedding_model"] == "bge-m3:567m"
    assert set(payload) == {
        "page_content",
        "document_id",
        "content_hash",
        "chunk_index",
        "chunk_count",
        "title",
        "url",
        "published_at",
        "source_updated_at",
        "document_type",
        "source_id",
        "source_provider",
        "source_name",
        "source_external_id",
        "document_external_id",
        "authors",
        "labels",
        "index_schema_version",
        "embedding_model",
    }


def test_payload_mapper_omits_missing_optional_news_time() -> None:
    chunk = build_chunk()
    chunk.metadata.pop("published_at")
    chunk.metadata.pop("source_updated_at")

    payload = QdrantPayloadMapper(spec()).build(chunk)

    assert "published_at" not in payload
    assert "source_updated_at" not in payload


def test_payload_mapper_rejects_missing_required_field() -> None:
    chunk = build_chunk()
    chunk.metadata.pop("content_hash")

    with pytest.raises(QdrantPayloadError, match="content_hash"):
        QdrantPayloadMapper(spec()).build(chunk)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("document_id", "not-a-uuid", "UUID"),
        ("content_hash", "short", "SHA-256"),
        ("chunk_count", 0, "chunk_count"),
        ("chunk_index", 1, "chunk_index"),
        ("published_at", "2026-08-13T01:02:03", "时区"),
    ],
)
def test_payload_mapper_rejects_invalid_structured_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    chunk = build_chunk()
    chunk.metadata[field] = value

    with pytest.raises(QdrantPayloadError, match=message):
        QdrantPayloadMapper(spec()).build(chunk)


def test_payload_index_plan_contains_news_time() -> None:
    assert PAYLOAD_INDEX_SCHEMAS == {
        "document_id": models.PayloadSchemaType.KEYWORD,
        "source_id": models.PayloadSchemaType.UUID,
        "source_provider": models.PayloadSchemaType.KEYWORD,
        "document_type": models.PayloadSchemaType.KEYWORD,
        "published_at": models.PayloadSchemaType.DATETIME,
        "labels": models.PayloadSchemaType.KEYWORD,
    }


def test_lifecycle_migrates_legacy_document_uuid_index_to_keyword() -> None:
    class IndexClient:
        """记录旧 document_id 索引删除与新 keyword 索引创建。"""

        def __init__(self) -> None:
            self.deleted: list[tuple[str, str]] = []
            self.created: list[tuple[str, str, models.PayloadSchemaType]] = []

        async def delete_payload_index(
            self,
            *,
            collection_name: str,
            field_name: str,
            **_kwargs: Any,
        ) -> Any:
            self.deleted.append((collection_name, field_name))
            return SimpleNamespace(status=models.UpdateStatus.COMPLETED)

        async def create_payload_index(
            self,
            *,
            collection_name: str,
            field_name: str,
            field_schema: models.PayloadSchemaType,
            **_kwargs: Any,
        ) -> Any:
            self.created.append((collection_name, field_name, field_schema))
            return SimpleNamespace(status=models.UpdateStatus.COMPLETED)

    client = IndexClient()
    lifecycle = QdrantCollectionLifecycle(client, qdrant_settings(), spec())  # type: ignore[arg-type]
    payload_schema = {
        field_name: SimpleNamespace(
            data_type=(
                models.PayloadSchemaType.UUID
                if field_name == "document_id"
                else expected_type
            )
        )
        for field_name, expected_type in PAYLOAD_INDEX_SCHEMAS.items()
    }

    run(
        lifecycle._ensure_payload_indexes(  # noqa: SLF001
            "news_chunks_test_v1_001",
            SimpleNamespace(payload_schema=payload_schema),
        )
    )

    assert client.deleted == [("news_chunks_test_v1_001", "document_id")]
    assert client.created == [
        (
            "news_chunks_test_v1_001",
            "document_id",
            models.PayloadSchemaType.KEYWORD,
        )
    ]


def test_real_local_qdrant_lifecycle_creates_collection_alias_and_indexes() -> None:
    async def verify() -> None:
        client = AsyncQdrantClient(location=":memory:")
        settings = qdrant_settings()
        lifecycle = QdrantCollectionLifecycle(client, settings, spec())
        try:
            physical_name = await lifecycle.ensure_current_collection()
            aliases = await client.get_aliases()
            info = await client.get_collection(physical_name)

            assert physical_name == "news_chunks_test_v1_001"
            assert [
                (alias.alias_name, alias.collection_name) for alias in aliases.aliases
            ] == [("news_chunks_test_current", physical_name)]
            assert info.config.params.vectors.size == 3  # type: ignore[union-attr]
            assert info.config.params.vectors.distance == models.Distance.COSINE  # type: ignore[union-attr]
            assert info.config.metadata == spec().collection_metadata
        finally:
            await client.close()

    # qdrant-client 的本地后端只在进程中首次提示 Payload index 不影响内存查询；该提示
    # 可能已被更早的搜索测试触发，不能把 warning 出现次数当作生命周期业务契约。
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Payload indexes have no effect",
            category=UserWarning,
        )
        run(verify())


def test_lifecycle_rejects_existing_collection_with_wrong_dimension() -> None:
    async def verify() -> None:
        client = AsyncQdrantClient(location=":memory:")
        settings = qdrant_settings()
        await client.create_collection(
            settings.collection_name,
            vectors_config=models.VectorParams(
                size=4,
                distance=models.Distance.COSINE,
            ),
            metadata=spec().collection_metadata,
        )
        try:
            with pytest.raises(VectorIndexConfigurationError, match="维度不匹配"):
                await QdrantCollectionLifecycle(
                    client, settings, spec()
                ).ensure_current_collection()
        finally:
            await client.close()

    run(verify())


def test_lifecycle_rejects_alias_pointing_to_another_collection() -> None:
    async def verify() -> None:
        client = AsyncQdrantClient(location=":memory:")
        settings = qdrant_settings()
        await client.create_collection(
            "another_collection",
            vectors_config=spec().vector_params,
        )
        await client.update_collection_aliases(
            [
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name="another_collection",
                        alias_name=settings.collection_alias,
                    )
                )
            ]
        )
        try:
            with pytest.raises(QdrantAliasConflictError, match="指向"):
                await QdrantCollectionLifecycle(
                    client, settings, spec()
                ).ensure_current_collection()
        finally:
            await client.close()

    run(verify())


def test_lifecycle_switches_current_alias_atomically() -> None:
    async def verify() -> None:
        client = AsyncQdrantClient(location=":memory:")
        settings = qdrant_settings(generation=1)
        lifecycle = QdrantCollectionLifecycle(client, settings, spec())
        await lifecycle.ensure_current_collection()
        new_collection = "news_chunks_test_v1_002"
        try:
            await lifecycle.ensure_collection(new_collection)
            await lifecycle.switch_current_alias(new_collection)
            aliases = await client.get_aliases()
            assert [
                (alias.alias_name, alias.collection_name) for alias in aliases.aliases
            ] == [(settings.collection_alias, new_collection)]
            assert await client.collection_exists(settings.collection_name)
            assert await client.collection_exists(new_collection)
        finally:
            await client.close()

    run(verify())


def test_lifecycle_does_not_create_missing_alias_target() -> None:
    async def verify() -> None:
        client = AsyncQdrantClient(location=":memory:")
        settings = qdrant_settings()
        lifecycle = QdrantCollectionLifecycle(client, settings, spec())
        missing = "news_chunks_test_v1_999"
        try:
            with pytest.raises(QdrantLifecycleError, match="不存在"):
                await lifecycle.switch_current_alias(missing)
            assert not await client.collection_exists(missing)
        finally:
            await client.close()

    from news_vector_service.qdrant.lifecycle import QdrantLifecycleError

    run(verify())


def test_store_never_uses_physical_collection_for_point_io() -> None:
    class SpyClient:
        """记录 Point I/O 的 Collection 参数，并返回最小成功响应。"""

        def __init__(self) -> None:
            self.collection_names: list[str] = []
            self.scroll_calls = 0

        async def scroll(self, *, collection_name: str, **kwargs: Any) -> Any:
            self.collection_names.append(collection_name)
            self.scroll_calls += 1
            return ([], None)

        async def upsert(self, *, collection_name: str, **kwargs: Any) -> Any:
            self.collection_names.append(collection_name)
            return SimpleNamespace(status=models.UpdateStatus.COMPLETED)

        async def delete(self, *, collection_name: str, **kwargs: Any) -> Any:
            self.collection_names.append(collection_name)
            return SimpleNamespace(status=models.UpdateStatus.COMPLETED)

    from types import SimpleNamespace

    async def verify() -> None:
        client = SpyClient()
        settings = qdrant_settings()
        store = QdrantChunkStore(client, settings, spec())  # type: ignore[arg-type]
        document_id = str(uuid4())
        chunk = build_chunk(document_id=document_id)
        await store.replace_document_chunks(
            document_id,
            [chunk],
            [[1.0, 0.0, 0.0]],
        )
        assert client.collection_names
        assert set(client.collection_names) == {settings.collection_alias}
        assert settings.collection_name not in client.collection_names

    run(verify())


def test_store_write_batch_size_splits_upsert_requests() -> None:
    class BatchSpyClient:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        async def scroll(self, **kwargs: Any) -> Any:
            return ([], None)

        async def upsert(self, *, points: list[Any], **kwargs: Any) -> Any:
            self.batch_sizes.append(len(points))
            return SimpleNamespace(status=models.UpdateStatus.COMPLETED)

    from types import SimpleNamespace

    async def verify() -> None:
        client = BatchSpyClient()
        settings = qdrant_settings(batch_size=2)
        store = QdrantChunkStore(client, settings, spec())  # type: ignore[arg-type]
        document_id = str(uuid4())
        chunks = [
            build_chunk(
                document_id=document_id,
                chunk_index=index,
                chunk_count=5,
            )
            for index in range(5)
        ]
        await store.replace_document_chunks(
            document_id,
            chunks,
            [[1.0, 0.0, 0.0] for _ in chunks],
        )
        assert client.batch_sizes == [2, 2, 1]

    run(verify())


@pytest.mark.parametrize("value", ["", "not-a-uuid"])
def test_store_rejects_non_uuid_document_id_before_remote_call(value: str) -> None:
    class FailIfCalledClient:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"不允许调用 Qdrant 方法 {name}")

    store = QdrantChunkStore(
        FailIfCalledClient(),  # type: ignore[arg-type]
        qdrant_settings(),
        spec(),
    )

    with pytest.raises(QdrantPointStoreError, match="UUID"):
        run(store.list_point_ids(value))


def test_store_uses_alias_and_replaces_stale_points() -> None:
    async def verify() -> None:
        client = AsyncQdrantClient(location=":memory:")
        settings = qdrant_settings(batch_size=1)
        lifecycle = QdrantCollectionLifecycle(client, settings, spec())
        await lifecycle.ensure_current_collection()
        store = QdrantChunkStore(client, settings, spec())
        document_id = str(uuid4())
        old_chunks = [
            build_chunk(
                document_id=document_id,
                chunk_index=index,
                chunk_count=3,
            )
            for index in range(3)
        ]
        new_chunks = old_chunks[:2]
        new_chunks[0].metadata["chunk_count"] = 2
        new_chunks[1].metadata["chunk_count"] = 2
        try:
            first = await store.replace_document_chunks(
                document_id,
                old_chunks,
                [[3.0, 4.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            )
            second = await store.replace_document_chunks(
                document_id,
                new_chunks,
                [[3.0, 4.0, 0.0], [0.0, 1.0, 0.0]],
            )
            records, _ = await client.scroll(
                collection_name=settings.collection_alias,
                with_payload=True,
                with_vectors=True,
                limit=10,
            )

            assert store.collection_name == "news_chunks_test_current"
            assert first.deleted_ids == ()
            assert second.deleted_ids == (old_chunks[2].id,)
            assert {str(record.id) for record in records} == {
                new_chunks[0].id,
                new_chunks[1].id,
            }
            # Qdrant 的 Cosine Collection 会把有效向量按单位长度存储；应用不重复归一化。
            first_record = next(
                record for record in records if str(record.id) == new_chunks[0].id
            )
            assert first_record.vector == pytest.approx([0.6, 0.8, 0.0])
        finally:
            await client.close()

    run(verify())


@pytest.mark.parametrize(
    ("vector", "message"),
    [
        ([1.0, 2.0], "维度不匹配"),
        ([0.0, 0.0, 0.0], "L2 范数为零"),
        ([1.0, float("nan"), 0.0], "不是有限值"),
        ([1.0, float("inf"), 0.0], "不是有限值"),
        ([1.7e308, 1.7e308, 1.7e308], "L2 范数不是有限值"),
    ],
)
def test_store_rejects_invalid_vector_before_remote_call(
    vector: list[float], message: str
) -> None:
    class FailIfCalledClient:
        """任何 Qdrant 调用都说明输入校验发生得太晚。"""

        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"不允许调用 Qdrant 方法 {name}")

    chunk = build_chunk()
    store = QdrantChunkStore(
        FailIfCalledClient(),  # type: ignore[arg-type]
        qdrant_settings(),
        spec(),
    )

    with pytest.raises(QdrantPointStoreError, match=message):
        run(
            store.replace_document_chunks(
                str(chunk.metadata["document_id"]),
                [chunk],
                [vector],
            )
        )


def test_store_empty_replacement_deletes_existing_document_points() -> None:
    async def verify() -> None:
        client = AsyncQdrantClient(location=":memory:")
        settings = qdrant_settings()
        await QdrantCollectionLifecycle(
            client, settings, spec()
        ).ensure_current_collection()
        store = QdrantChunkStore(client, settings, spec())
        document_id = str(uuid4())
        chunk = build_chunk(document_id=document_id)
        try:
            await store.replace_document_chunks(
                document_id, [chunk], [[1.0, 0.0, 0.0]]
            )
            result = await store.replace_document_chunks(document_id, [], [])
            assert result.upserted_ids == ()
            assert result.deleted_ids == (chunk.id,)
            assert await store.list_point_ids(document_id) == set()
        finally:
            await client.close()

    run(verify())
