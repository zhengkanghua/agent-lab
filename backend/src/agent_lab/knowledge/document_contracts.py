"""导入、索引和维护共享的纯数据契约，不携带 Session 或外部客户端。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from agent_lab.domain.enums import DocumentType
from agent_lab.domain.source_document import SourceDocument, SourceInfo
if TYPE_CHECKING:
    from agent_lab.schemas.vector_search import VectorSearchResult


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    """当前正文与可索引元数据的独立快照，可在事务关闭后安全使用。"""

    id: UUID
    knowledge_base_id: UUID
    source_id: UUID | None
    external_id: str | None
    source: SourceInfo | None
    document_type: DocumentType
    mime_type: str
    title: str
    url: str | None
    content_text: str
    content_hash: str
    index_revision: int
    authors: tuple[str, ...]
    labels: tuple[str, ...]
    published_at: datetime | None
    source_updated_at: datetime | None
    upload_filename: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentSearchGroup:
    """适配器已验证的非空命中组；Chunk 按 score 降序且文档元数据一致。"""

    document_id: UUID
    matches: tuple[VectorSearchResult, ...]


@dataclass(frozen=True, slots=True)
class ReplaceChunksResult:
    """一次整篇索引替换的已确认 Point 身份，不含正文或向量。"""

    document_id: str
    upserted_ids: tuple[str, ...]
    deleted_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RebuiltDocument:
    """新 generation 已验证的文档版本，不保存正文或向量。"""

    document_id: UUID
    knowledge_base_id: UUID
    revision: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class IndexRebuildResult:
    document_count: int
    point_count: int


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    """一次清理扫描中符合范围与保留期的文档版本。"""

    document_id: UUID
    revision: int
    retention_date: datetime


@dataclass(frozen=True, slots=True)
class DocumentDeletion:
    """持久删除意图的独立快照，远端确认与数据库删除可以分批恢复。"""

    document_id: UUID
    revision: int
    cutoff_date: datetime | None
    retention_date: datetime
    qdrant_deleted: bool


@dataclass(frozen=True, slots=True)
class ImportSourceState:
    """同步准入与 checkpoint 所需的来源状态。"""

    id: UUID
    knowledge_base_id: UUID | None
    sync_checkpoint: str | None


@dataclass(frozen=True, slots=True)
class SourceImportPage:
    """外部适配器已完整校验并映射的一页文档，以及成功后的续读位置。"""

    documents: tuple[SourceDocument, ...]
    new_checkpoint: str | None


@dataclass(frozen=True, slots=True)
class SourceSyncFailure:
    source_external_id: str
    error_type: str


@dataclass(frozen=True, slots=True)
class SourceImportResult:
    source_count: int
    synchronized_count: int
    checkpoint_advanced_count: int
    failures: tuple[SourceSyncFailure, ...]

    @property
    def failed_source_count(self) -> int:
        return len(self.failures)

    @property
    def successful_source_count(self) -> int:
        return self.source_count - self.failed_source_count
