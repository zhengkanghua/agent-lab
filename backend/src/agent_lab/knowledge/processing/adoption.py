"""采用编排：准备并核验新索引后，条件切换正式指向；失败保持旧版本。"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from agent_lab.domain.write_scope import WriteResourceBusyError
from agent_lab.knowledge.ports import KnowledgeWriteCoordinator
from agent_lab.knowledge.processing.indexing import CandidateIndexer, IndexTarget
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, ProcessingReceipt

logger = logging.getLogger(__name__)


class AdoptionRepository(Protocol):
    async def freeze(self, processing_id: UUID, *, candidate_revision: int, management_revision: int,
                     fingerprint: str, actor_id: UUID, conclusion: str | None, index_spec: dict) -> ProcessingReceipt: ...
    async def claim_next(self, index_spec: dict, processing_id: UUID | None = None) -> IndexTarget | ProcessingReceipt | None: ...
    async def complete(self, target: IndexTarget) -> bool: ...
    async def fail(self, target: IndexTarget, code: str) -> None: ...
    async def next_cleanup(self) -> tuple[UUID, UUID] | None: ...
    async def mark_cleaned(self, document_id: UUID, index_instance_id: UUID) -> None: ...


AdoptionWork = Callable[[], AbstractAsyncContextManager[AdoptionRepository]]
IndexerFactory = Callable[[], AbstractAsyncContextManager[CandidateIndexer]]


class DocumentAdoptionApplication:
    def __init__(self, work: AdoptionWork, coordinator: KnowledgeWriteCoordinator,
                 indexer: IndexerFactory, index_spec: dict):
        self._work, self._coordinator = work, coordinator
        self._indexer, self._index_spec = indexer, index_spec

    async def adopt(self, processing_id: UUID, *, candidate_revision: int, management_revision: int,
                    fingerprint: str, actor_id: UUID, conclusion: str | None = None) -> ProcessingReceipt:
        """人工确认只持久化决定和冻结目标，HTTP 不等待向量化或 Qdrant。"""
        async with self._work() as repository:
            return await repository.freeze(
                processing_id, candidate_revision=candidate_revision, management_revision=management_revision,
                fingerprint=fingerprint, actor_id=actor_id, conclusion=conclusion, index_spec=self._index_spec,
            )

    async def process(self, processing_id: UUID | None = None) -> ProcessingReceipt | None:
        """持久占用 index 资源，避免清理/重建/其他采用在远端写入期间交叉执行。"""
        try:
            async with self._coordinator.hold(("index",), wait=False):
                async with self._work() as repository:
                    target = await repository.claim_next(self._index_spec, processing_id)
                if target is None:
                    return None
                if isinstance(target, ProcessingReceipt):
                    return target
                try:
                    async with self._indexer() as indexer:
                        await indexer.prepare(target)
                    async with self._work() as repository:
                        if not await repository.complete(target):
                            raise ProcessingApplicationError("document_adoption_conflict")
                except Exception as exc:
                    code = exc.code if isinstance(exc, ProcessingApplicationError) else "document_index_prepare_failed"
                    logger.error("文档采用未完成 processing_id=%s error_type=%s", target.processing_id, type(exc).__name__)
                    async with self._work() as repository:
                        await repository.fail(target, code)
                    return ProcessingReceipt(target.processing_id, target.document_id, "adoption_failed", target.source.sha256, target.candidate_revision, code)
                return ProcessingReceipt(target.processing_id, target.document_id, "adopted", target.source.sha256, target.candidate_revision)
        except WriteResourceBusyError:
            return None

    async def cleanup_one(self) -> bool:
        """旧向量回收独立于采用结果；回收失败不反转已经成功的采用。"""
        try:
            async with self._coordinator.hold(("index",), wait=False):
                async with self._work() as repository:
                    item = await repository.next_cleanup()
                if item is None:
                    return False
                document_id, index_instance_id = item
                async with self._indexer() as indexer:
                    await indexer.cleanup(document_id, index_instance_id)
                async with self._work() as repository:
                    await repository.mark_cleaned(document_id, index_instance_id)
                return True
        except WriteResourceBusyError:
            return False
