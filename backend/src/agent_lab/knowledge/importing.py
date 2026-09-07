"""按来源编排通用导入；协议分页由外部适配器完成，应用只控制归属与提交。"""

import logging
from collections.abc import Sequence
from uuid import UUID

from agent_lab.domain.source_document import SourceDocument
from agent_lab.knowledge.document_contracts import SourceImportResult, SourceSyncFailure
from agent_lab.knowledge.domain import KnowledgeBaseInactiveError, KnowledgeBaseNotFoundError, require_active_knowledge_base
from agent_lab.knowledge.ports import ExternalSourceFactory, ImportWorkFactory

logger = logging.getLogger(__name__)


class SourceImportService:
    """每个来源单独失败隔离，网络请求期间不持有数据库事务。"""

    def __init__(self, external_source: ExternalSourceFactory, work: ImportWorkFactory):
        self._external_source = external_source
        self._work = work

    async def import_recent_per_source(self, *, limit_per_source: int = 2) -> SourceImportResult:
        if limit_per_source < 1:
            raise ValueError("limit_per_source 必须大于零")
        failures = []
        synchronized = advanced = 0
        async with self._external_source() as external:
            sources = await external.list_sources()
            for source in sources:
                try:
                    async with self._work() as work:
                        current = await work.sources.upsert(source)
                        target = await work.knowledge_bases.get(current.knowledge_base_id) if current.knowledge_base_id else None
                        await work.commit()
                    if target is None or not target.is_active:
                        continue
                    page = await external.read_page(source, expected_checkpoint=current.sync_checkpoint, limit=limit_per_source)
                    count, checkpoint_advanced = await self.save_source_page(
                        documents=page.documents, existing_source_id=current.id,
                        expected_checkpoint=current.sync_checkpoint, new_checkpoint=page.new_checkpoint,
                    )
                    synchronized += count
                    advanced += int(checkpoint_advanced)
                except Exception as exc:
                    failures.append(SourceSyncFailure(source.external_id, type(exc).__name__))
                    logger.error("来源同步失败 source=%s error_type=%s error_reason=%s", source.external_id, type(exc).__name__, getattr(exc, "reason", None) or "-")
        return SourceImportResult(len(sources), synchronized, advanced, tuple(failures))

    async def save_source_page(
        self, *, documents: Sequence[SourceDocument], existing_source_id: UUID,
        expected_checkpoint: str | None, new_checkpoint: str | None,
    ) -> tuple[int, bool]:
        """同一事务内复核绑定与启用状态、保存完整页面、条件推进 checkpoint。"""
        if not documents and new_checkpoint == expected_checkpoint:
            return 0, False
        async with self._work() as work:
            current = await work.sources.get_for_update(existing_source_id)
            if current is None:
                return 0, False
            target = await work.knowledge_bases.get_for_update(current.knowledge_base_id) if current.knowledge_base_id else None
            try:
                require_active_knowledge_base(target)
            except (KnowledgeBaseInactiveError, KnowledgeBaseNotFoundError):
                return 0, False
            for document in documents:
                await work.documents.upsert(document, source_id=current.id, knowledge_base_id=current.knowledge_base_id)
            advanced = False
            if new_checkpoint is not None and new_checkpoint != expected_checkpoint:
                advanced = await work.sources.update_sync_checkpoint(
                    source_id=current.id, expected_checkpoint=expected_checkpoint, new_checkpoint=new_checkpoint,
                )
            await work.commit()
            return len(documents), advanced
