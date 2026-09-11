"""知识库用例依赖的持久化端口；实现方承担数据库细节。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from contextlib import AbstractAsyncContextManager
from typing import Protocol, TYPE_CHECKING
from uuid import UUID

from agent_lab.knowledge.contracts import KnowledgeBaseCreateRequest, SourceView
from agent_lab.knowledge.domain import KnowledgeBase
from agent_lab.knowledge.scope import KnowledgeBaseSelection, ResolvedKnowledgeBaseScope
from agent_lab.knowledge.document_contracts import (
    DocumentDeletion, DocumentSearchGroup, ImportSourceState,
    RetentionCandidate, SourceImportPage, RebuiltDocument,
)
from agent_lab.domain.source_document import SourceDocument, SourceInfo
from agent_lab.knowledge.processing.lifecycle import SourceReception
if TYPE_CHECKING:
    from agent_lab.knowledge.processing.indexing import IndexTarget
    from agent_lab.schemas.vector_search import VectorSearchFilters, VectorSearchResult


class KnowledgeBaseRepository(Protocol):
    """一个事务内的知识库读写；更新前锁定目标，唯一键由存储原子保护。"""

    async def list(self, *, include_inactive: bool) -> list[KnowledgeBase]: ...

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase | None: ...

    async def get_for_update(self, knowledge_base_id: UUID) -> KnowledgeBase | None: ...

    async def create(self, request: KnowledgeBaseCreateRequest) -> KnowledgeBase: ...

    async def update(self, knowledge_base: KnowledgeBase) -> KnowledgeBase: ...


class KnowledgeBaseScope(Protocol):
    """只读范围准入；调用方不依赖实际存储或事务实现。"""

    async def require_active(self, knowledge_base_id: UUID) -> KnowledgeBase: ...

    async def resolve_scope(self, selection: KnowledgeBaseSelection) -> ResolvedKnowledgeBaseScope: ...


class KnowledgeBaseUnitOfWork(Protocol):
    """每次调用独占一个工作单元，未提交的写入在退出时回滚。"""

    repository: KnowledgeBaseRepository

    async def commit(self) -> None: ...


type KnowledgeBaseUnitOfWorkFactory = Callable[
    [], AbstractAsyncContextManager[KnowledgeBaseUnitOfWork]
]


class KnowledgeWriteCoordinator(Protocol):
    """协调应用写入；配置变更要求立即判断冲突，批次任务可以等待。"""

    def hold(self, resources: tuple[str, ...], *, wait: bool = True) -> AbstractAsyncContextManager[None]: ...


class SourceBindingRepository(Protocol):
    """仅提供来源配置需要的存储操作，与同步协议解耦。"""

    async def list(self) -> list[SourceView]: ...

    async def get_for_update(self, source_id: UUID) -> SourceView | None: ...

    async def has_documents(self, source_id: UUID) -> bool: ...

    async def has_pending_deletions(self, source_id: UUID) -> bool: ...

    async def save_binding(self, source: SourceView) -> None: ...


class SourceBindingUnitOfWork(Protocol):
    """来源锁、目标知识库锁、绑定变更共用一个短事务。"""

    sources: SourceBindingRepository
    knowledge_bases: KnowledgeBaseRepository

    async def commit(self) -> None: ...


type SourceBindingUnitOfWorkFactory = Callable[
    [], AbstractAsyncContextManager[SourceBindingUnitOfWork]
]


class IndexSpecification(Protocol):
    """应用只读取索引规则，不依赖向量库的配置对象或 SDK。"""

    dimension: int
    embedding_model: str
    schema_version: str
    tokenizer: str
    chunk_size: int
    chunk_overlap: int


class EmbeddingProvider(Protocol):
    embedding_model: str
    dimension: int | None

    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


class VectorSearch(Protocol):
    index_spec: IndexSpecification

    async def search(self, vector: Sequence[float], *, top_k: int, score_threshold: float | None, filters: VectorSearchFilters) -> list[VectorSearchResult]: ...
    async def search_groups(self, vector: Sequence[float], *, document_limit: int, matches_per_document: int, score_threshold: float | None, filters: VectorSearchFilters) -> list[DocumentSearchGroup]: ...


class RetentionRepository(Protocol):
    """候选和待办返回独立快照；所有方法返回前结束数据库事务。"""

    async def candidates(self, cutoff: datetime, after: RetentionCandidate | None, limit: int, *, knowledge_base_ids: tuple[UUID, ...]) -> list[RetentionCandidate]: ...
    async def pending(self, after: UUID | None, limit: int, *, knowledge_base_ids: tuple[UUID, ...]) -> list[DocumentDeletion]: ...
    async def prepare(self, candidates: list[RetentionCandidate], cutoff: datetime) -> list[DocumentDeletion]: ...
    async def verify(self, records: list[DocumentDeletion]) -> None: ...
    async def finish(self, records: list[DocumentDeletion]) -> int: ...
    async def mark_qdrant_deleted(self, records: list[DocumentDeletion]) -> None: ...
    async def mark_object_deleted(self, document_id: UUID, reference) -> None: ...
    async def record_error(self, records: list[DocumentDeletion], error_type: str) -> None: ...


class DeletionStore(Protocol):
    async def count_by_document_ids(self, document_ids: list[str]) -> int: ...
    async def delete_by_document_ids(self, document_ids: list[str]) -> None: ...


class ExternalSource(Protocol):
    """外部来源协议适配器，在一次调用内维护分页协议状态。"""

    async def list_sources(self) -> list[SourceInfo]: ...
    async def read_page(self, source: SourceInfo, *, expected_checkpoint: str | None, limit: int) -> SourceImportPage: ...


type ExternalSourceFactory = Callable[[], AbstractAsyncContextManager[ExternalSource]]


class ImportSourceRepository(Protocol):
    async def get_for_update(self, source_id: UUID) -> ImportSourceState | None: ...
    async def upsert(self, source: SourceInfo) -> ImportSourceState: ...
    async def update_sync_checkpoint(self, *, source_id: UUID, expected_checkpoint: str | None, new_checkpoint: str) -> bool: ...


class ImportDocumentRepository(Protocol):
    async def prepare(self, document: SourceDocument, *, source_id: UUID, knowledge_base_id: UUID) -> SourceReception: ...
    async def confirm_receptions(self, processing_ids: Sequence[UUID], *, source_id: UUID, knowledge_base_id: UUID) -> None: ...


class ImportUnitOfWork(Protocol):
    sources: ImportSourceRepository
    documents: ImportDocumentRepository
    knowledge_bases: KnowledgeBaseRepository

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


type ImportWorkFactory = Callable[[], AbstractAsyncContextManager[ImportUnitOfWork]]


class RebuildRepository(Protocol):
    """重建读取已采用快照，保存新实例意图和发布屏障；方法返回前结束事务。"""

    async def require_ready(self) -> None: ...
    async def list_documents(self, *, after: UUID | None, limit: int) -> list[UUID]: ...
    async def prepare_document(self, document_id: UUID, *, index_spec: dict, location: dict) -> tuple[RebuiltDocument, IndexTarget] | None: ...
    async def mark_prepared(self, processing_id: UUID) -> None: ...
    async def begin_publication(self, documents: Sequence[RebuiltDocument]) -> None: ...
    async def mark_rebuilt(self, documents: Sequence[RebuiltDocument], *, schema_version: str) -> None: ...
    async def fail_build(self, documents: Sequence[RebuiltDocument]) -> None: ...
    async def pending_publication(self, collection: str) -> list[tuple[RebuiltDocument, IndexTarget, dict]]: ...
    async def abort_publication(self, documents: Sequence[RebuiltDocument]) -> None: ...


class RebuildTarget(Protocol):
    """隔离 generation 的构建与验收，发布前不改变业务读取目标。"""

    schema_version: str
    index_spec: dict
    collection_name: str
    location: dict | None

    async def prepare(self) -> None: ...
    async def write_document(self, target: IndexTarget) -> int: ...
    async def verify_document(self, target: IndexTarget) -> int: ...
    async def verify_total(self, expected_points: int) -> None: ...
    async def publish(self) -> None: ...
    async def current_target(self) -> str | None: ...
