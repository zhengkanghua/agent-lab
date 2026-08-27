"""DocumentIndexingService 状态编排、版本保护和失败处理的离线测试。"""

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.documents import Document

import news_vector_service.services.document_indexing_service as service_module
from news_vector_service.models.document import DocumentRecord
from news_vector_service.pipeline.ollama_embedding_provider import ChunkEmbedding
from news_vector_service.qdrant.index_spec import VectorIndexSpec
from news_vector_service.qdrant.index_spec import VectorIndexConfigurationError
from news_vector_service.qdrant.store import ReplaceChunksResult
from news_vector_service.services.document_indexing_service import (
    DocumentIndexingService,
)


def run(coroutine: Any) -> Any:
    """执行测试协程，不让默认离线测试依赖异步 pytest 插件。"""

    return asyncio.run(coroutine)


def build_record(*, revision: int = 3) -> DocumentRecord:
    """构造索引 Service 所需的最小 ORM 文档快照。"""

    return DocumentRecord(
        id=uuid4(),
        source_id=uuid4(),
        external_id="article/42",
        title="示例新闻",
        url="https://example.com/news/42",
        authors=[],
        labels=[],
        image_urls=[],
        content_text="新闻正文",
        content_hash="a" * 64,
        index_revision=revision,
    )


class FakeRepository:
    """记录状态方法调用并返回测试预设结果。"""

    def __init__(
        self,
        *,
        claimed: bool = True,
        marked_indexed: bool = True,
        marked_failed: bool = True,
        loaded_record: DocumentRecord | None = None,
    ) -> None:
        self.claimed = claimed
        self.marked_indexed = marked_indexed
        self.marked_failed = marked_failed
        self.loaded_record = loaded_record
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_with_source(self, document_id: Any) -> DocumentRecord | None:
        self.calls.append(("get_with_source", {"document_id": document_id}))
        return self.loaded_record

    async def claim_for_indexing(self, **kwargs: Any) -> bool:
        self.calls.append(("claim", kwargs))
        return self.claimed

    async def mark_indexed(self, **kwargs: Any) -> bool:
        self.calls.append(("indexed", kwargs))
        return self.marked_indexed

    async def mark_failed(self, **kwargs: Any) -> bool:
        self.calls.append(("failed", kwargs))
        return self.marked_failed

    async def release_stale_claim(self, **kwargs: Any) -> bool:
        self.calls.append(("release", kwargs))
        return True


class FakeChunkPipeline:
    """返回一项预设 Chunk，并记录是否被调用。"""

    def __init__(self, chunk: Document) -> None:
        self.chunk = chunk
        self.calls = 0
        self.encoding_name = "cl100k_base"
        self.chunk_size = 512
        self.chunk_overlap = 96

    def build_chunks(self, record: DocumentRecord) -> list[Document]:
        self.calls += 1
        return [self.chunk]


class FakeEmbeddingProvider:
    """返回预设向量或异常，并暴露本次真实维度。"""

    def __init__(
        self,
        *,
        vector: list[float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.vector = vector or [1.0, 0.0, 0.0]
        self.error = error
        self.dimension: int | None = None
        self.calls = 0
        self.embedding_model = "bge-m3:567m"

    async def embed_chunks(self, chunks: list[Document]) -> list[ChunkEmbedding]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        self.dimension = len(self.vector)
        return [
            ChunkEmbedding(chunk_id=chunks[0].id or "", embedding=self.vector)
        ]


class FakePointStore:
    """记录 Alias 写入输入，并返回预设替换结果或异常。"""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        index_spec: VectorIndexSpec | None = None,
    ) -> None:
        self.error = error
        self.index_spec = index_spec or VectorIndexSpec(dimension=3)
        self.calls: list[tuple[str, list[Document], list[list[float]]]] = []

    async def replace_document_chunks(
        self,
        document_id: str,
        chunks: list[Document],
        vectors: list[list[float]],
    ) -> ReplaceChunksResult:
        self.calls.append((document_id, chunks, vectors))
        if self.error is not None:
            raise self.error
        return ReplaceChunksResult(
            document_id=document_id,
            upserted_ids=tuple(chunk.id or "" for chunk in chunks),
            deleted_ids=(),
        )


def build_service(
    *,
    repository: FakeRepository,
    embedding_provider: FakeEmbeddingProvider | None = None,
    point_store: FakePointStore | None = None,
) -> tuple[
    DocumentIndexingService,
    FakeChunkPipeline,
    FakeEmbeddingProvider,
    FakePointStore,
]:
    """组装只使用 fake 的索引 Service。"""

    chunk = Document(id=str(uuid4()), page_content="Chunk 正文")
    pipeline = FakeChunkPipeline(chunk)
    provider = embedding_provider or FakeEmbeddingProvider()
    test_spec = VectorIndexSpec(dimension=3)
    store = point_store or FakePointStore(index_spec=test_spec)
    store.index_spec = test_spec
    service = DocumentIndexingService(
        chunk_pipeline=pipeline,  # type: ignore[arg-type]
        embedding_provider=provider,  # type: ignore[arg-type]
        point_store=store,  # type: ignore[arg-type]
        spec=test_spec,
    )
    return service, pipeline, provider, store


def install_repository(
    monkeypatch: pytest.MonkeyPatch,
    repository: FakeRepository,
) -> None:
    """让 Service 构造 fake Repository，避免访问真实 PostgreSQL。"""

    monkeypatch.setattr(
        service_module,
        "DocumentRepository",
        lambda _session: repository,
    )


def test_success_marks_indexed_only_after_qdrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record()
    repository = FakeRepository()
    install_repository(monkeypatch, repository)
    service, pipeline, provider, store = build_service(repository=repository)

    result = run(service.index_record(SimpleNamespace(), record))  # type: ignore[arg-type]

    assert result.indexed is True
    assert result.skipped is False
    assert pipeline.calls == 1
    assert provider.calls == 1
    assert store.calls[0][0] == str(record.id)
    assert store.calls[0][2] == [[1.0, 0.0, 0.0]]
    assert [name for name, _ in repository.calls] == ["claim", "indexed"]
    assert repository.calls[1][1] == {
        "document_id": record.id,
        "index_revision": 3,
        "content_hash": "a" * 64,
        "schema_version": "v1",
    }


def test_claim_conflict_skips_all_expensive_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record()
    repository = FakeRepository(claimed=False)
    install_repository(monkeypatch, repository)
    service, pipeline, provider, store = build_service(repository=repository)

    result = run(service.index_record(SimpleNamespace(), record))  # type: ignore[arg-type]

    assert result.skipped is True
    assert result.indexed is False
    assert pipeline.calls == 0
    assert provider.calls == 0
    assert store.calls == []
    assert [name for name, _ in repository.calls] == ["claim"]


def test_embedding_failure_marks_current_revision_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record()
    repository = FakeRepository()
    install_repository(monkeypatch, repository)
    provider = FakeEmbeddingProvider(error=RuntimeError("remote body secret-value"))
    service, _pipeline, _provider, store = build_service(
        repository=repository,
        embedding_provider=provider,
    )

    with pytest.raises(RuntimeError, match="secret-value"):
        run(service.index_record(SimpleNamespace(), record))  # type: ignore[arg-type]

    assert store.calls == []
    assert [name for name, _ in repository.calls] == ["claim", "failed"]
    saved_error = repository.calls[1][1]["error_message"]
    assert saved_error == "RuntimeError: indexing operation failed"
    assert "secret-value" not in saved_error


def test_qdrant_failure_marks_failed_after_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record()
    repository = FakeRepository()
    install_repository(monkeypatch, repository)
    store = FakePointStore(error=ValueError("Qdrant 写入失败"))
    service, _pipeline, provider, _store = build_service(
        repository=repository,
        point_store=store,
    )

    with pytest.raises(ValueError, match="Qdrant 写入失败"):
        run(service.index_record(SimpleNamespace(), record))  # type: ignore[arg-type]

    assert provider.calls == 1
    assert [name for name, _ in repository.calls] == ["claim", "failed"]


def test_revision_change_after_qdrant_releases_new_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record(revision=7)
    repository = FakeRepository(marked_indexed=False)
    install_repository(monkeypatch, repository)
    service, _pipeline, _provider, _store = build_service(repository=repository)

    result = run(service.index_record(SimpleNamespace(), record))  # type: ignore[arg-type]

    assert result.indexed is False
    assert result.skipped is True
    assert [name for name, _ in repository.calls] == [
        "claim",
        "indexed",
        "release",
    ]
    assert repository.calls[2][1] == {
        "document_id": record.id,
        "stale_revision": 7,
    }


def test_dimension_mismatch_never_calls_qdrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record()
    repository = FakeRepository()
    install_repository(monkeypatch, repository)
    provider = FakeEmbeddingProvider(vector=[1.0, 0.0, 0.0, 0.0])
    service, _pipeline, _provider, store = build_service(
        repository=repository,
        embedding_provider=provider,
    )

    with pytest.raises(ValueError, match="与索引规格"):
        run(service.index_record(SimpleNamespace(), record))  # type: ignore[arg-type]

    assert store.calls == []
    assert [name for name, _ in repository.calls] == ["claim", "failed"]


def test_empty_chunk_result_is_failed_without_deleting_qdrant_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record()
    repository = FakeRepository()
    install_repository(monkeypatch, repository)
    service, pipeline, provider, store = build_service(repository=repository)
    pipeline.build_chunks = lambda _record: []  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="未返回任何分块"):
        run(service.index_record(SimpleNamespace(), record))  # type: ignore[arg-type]

    assert provider.calls == 0
    assert store.calls == []
    assert [name for name, _ in repository.calls] == ["claim", "failed"]


def test_index_document_loads_record_before_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record()
    repository = FakeRepository(loaded_record=record)
    install_repository(monkeypatch, repository)
    service, _pipeline, _provider, _store = build_service(repository=repository)

    result = run(service.index_document(SimpleNamespace(), record.id))  # type: ignore[arg-type]

    assert result.indexed is True
    assert [name for name, _ in repository.calls] == [
        "get_with_source",
        "claim",
        "indexed",
    ]


@pytest.mark.parametrize("mismatch", ["model", "chunk", "store"])
def test_service_rejects_components_from_another_index_spec(mismatch: str) -> None:
    chunk = Document(id=str(uuid4()), page_content="Chunk 正文")
    pipeline = FakeChunkPipeline(chunk)
    provider = FakeEmbeddingProvider()
    expected_spec = VectorIndexSpec(dimension=3)
    store = FakePointStore(index_spec=expected_spec)

    if mismatch == "model":
        provider.embedding_model = "another-model"
    elif mismatch == "chunk":
        pipeline.chunk_size = 256
    else:
        store.index_spec = VectorIndexSpec(dimension=4)

    with pytest.raises(
        VectorIndexConfigurationError,
        match="与索引规格|VectorIndexSpec 不一致",
    ):
        DocumentIndexingService(
            chunk_pipeline=pipeline,  # type: ignore[arg-type]
            embedding_provider=provider,  # type: ignore[arg-type]
            point_store=store,  # type: ignore[arg-type]
            spec=expected_spec,
        )
