"""冻结候选到索引的项目契约；向量化消费已经预览过的确切文本。"""

from collections.abc import Sequence
from datetime import datetime
from hashlib import sha256
from typing import Any, Protocol
from uuid import UUID, uuid5

from agent_lab.domain.enums import DocumentType
from agent_lab.knowledge.processing.contracts import DocumentPreview, ProcessingValue
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.knowledge.storage import ObjectReference


class IndexMetadata(ProcessingValue):
    """随候选冻结的展示及过滤字段，不在采用时重读已改变的来源元数据。"""

    document_type: DocumentType = DocumentType.OTHER
    source_id: UUID | None = None
    source_provider: str | None = None
    source_name: str | None = None
    source_external_id: str | None = None
    document_external_id: str | None = None
    url: str | None = None
    upload_filename: str | None = None
    authors: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    image_urls: tuple[str, ...] = ()
    published_at: datetime | None = None
    source_updated_at: datetime | None = None


class IndexTarget(ProcessingValue):
    """已保存写入意图的不可变采用目标；重试不得重新切块或换身份。"""

    processing_id: UUID
    document_id: UUID
    knowledge_base_id: UUID
    candidate_revision: int
    review_id: UUID
    version_id: UUID
    index_instance_id: UUID
    base_version_id: UUID | None
    preview: DocumentPreview
    metadata: IndexMetadata
    source: ObjectReference
    index_spec: dict[str, Any]
    manual: bool = False

    @property
    def content_hash(self) -> str:
        return sha256(self.preview.document.body.encode("utf-8")).hexdigest()

    @property
    def mime_type(self) -> str:
        return "text/plain" if self.preview.document.text_format == "plain" else "text/markdown"

    def chunk_id(self, sequence: int) -> str:
        return str(uuid5(self.index_instance_id, str(sequence)))


class CandidatePointStore(Protocol):
    async def prepare_candidate(self, target: IndexTarget, vectors: Sequence[Sequence[float]]) -> None: ...
    async def delete_instance(self, document_id: UUID, index_instance_id: UUID) -> None: ...


class TextEmbeddings(Protocol):
    embedding_model: str
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


class CandidateIndexer:
    """不接触 PostgreSQL 或 Docling，只把冻结清单向量化并写入隔离实例。"""

    def __init__(self, embeddings: TextEmbeddings, store: CandidatePointStore, index_spec: dict[str, Any]):
        self._embeddings, self._store = embeddings, store
        self.index_spec = index_spec

    async def prepare(self, target: IndexTarget) -> None:
        chunks = target.preview.chunk_result.chunks
        if target.index_spec != self.index_spec or self._embeddings.embedding_model != self.index_spec["embedding_model"]:
            raise ProcessingApplicationError("document_index_spec_changed")
        if not chunks or any(chunk.token_count > target.preview.chunk_result.specification.max_tokens for chunk in chunks):
            raise ProcessingApplicationError("document_preview_invalid")
        vectors = await self._embeddings.embed_documents([chunk.embedding_text for chunk in chunks])
        await self._store.prepare_candidate(target, vectors)

    async def cleanup(self, document_id: UUID, index_instance_id: UUID) -> None:
        await self._store.delete_instance(document_id, index_instance_id)
