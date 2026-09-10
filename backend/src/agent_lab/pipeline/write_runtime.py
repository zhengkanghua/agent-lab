"""手动、CLI 和定时任务共用写入入口；文档处理使用独立的持久候选流程。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.knowledge.importing import SourceImportService
from agent_lab.knowledge.processing.batch import DocumentProcessingBatch, PendingIndexExecutionResult
from agent_lab.qdrant.lifecycle import build_qdrant_client
from agent_lab.qdrant.store import QdrantDeletionStore
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository
from agent_lab.services.document_retention_service import DocumentRetentionService
from agent_lab.services.news_pipeline_execution_service import NewsPipelineExecutionService, NewsSyncExecutionResult
from agent_lab.services.write_coordination import WriteCoordinator

type AsyncSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True, slots=True)
class PipelineRunOnceExecutionResult:
    sync: NewsSyncExecutionResult
    index: PendingIndexExecutionResult


@dataclass(slots=True)
class PipelineWriteRuntime:
    """构造不访问网络，只有执行相应能力才读取配置和创建客户端。"""

    executor: NewsPipelineExecutionService
    import_service: SourceImportService | None = None
    processing_batch: DocumentProcessingBatch | None = None
    import_factory: Callable | None = None
    processing_factory: Callable | None = None
    retention_settings_factory: Callable | None = None
    session_factory: AsyncSessionFactory | None = None
    retention_client: AsyncQdrantClient | None = None

    @classmethod
    def lazy(cls, *, session_factory, freshrss_factory, processing_factory, qdrant_settings_factory):
        return cls(
            executor=NewsPipelineExecutionService(coordinator=WriteCoordinator(session_factory)),
            import_factory=freshrss_factory, processing_factory=processing_factory,
            retention_settings_factory=qdrant_settings_factory, session_factory=session_factory,
        )

    @classmethod
    def build(cls, *, session_factory, freshrss_settings, qdrant_settings, ollama_settings):
        from agent_lab.knowledge.composition import build_document_processing_batch, build_source_import_service
        return cls.lazy(
            session_factory=session_factory,
            freshrss_factory=lambda: build_source_import_service(freshrss_settings, session_factory),
            processing_factory=lambda: build_document_processing_batch(
                session_factory, qdrant_settings=qdrant_settings, ollama_settings=ollama_settings,
            ),
            qdrant_settings_factory=lambda: qdrant_settings,
        )

    async def run_once(self, *, limit_per_source: int, batch_size: int, stale_after: timedelta):
        sync = await self.sync_only(limit_per_source=limit_per_source)
        index = await self.index_only(batch_size=batch_size, stale_after=stale_after)
        return PipelineRunOnceExecutionResult(sync, index)

    async def sync_only(self, *, limit_per_source: int) -> NewsSyncExecutionResult:
        if self.import_service is None:
            self.import_service = self.import_factory()
        return await self.executor.sync_news(self.import_service, limit_per_source=limit_per_source)

    async def index_only(self, *, batch_size: int, stale_after: timedelta) -> PendingIndexExecutionResult:
        """与独立 scheduler 共用解析、采用和回收，异常候选留给人工处理。"""
        if self.processing_batch is None:
            self.processing_batch = self.processing_factory()
        return await self.processing_batch.run(batch_size=batch_size, stale_after=stale_after)

    async def prune_old_documents(self, *, retention_days: int, dry_run: bool, knowledge_base_ids: list[UUID]):
        from agent_lab.knowledge.composition import build_document_storage
        async with self.executor.writing(("sync", "index")):
            settings = self.retention_settings_factory()
            if self.retention_client is None:
                self.retention_client = build_qdrant_client(settings)
            async with self.session_factory() as session:
                service = DocumentRetentionService(
                    DocumentRetentionRepository(session), QdrantDeletionStore(self.retention_client, settings),
                    build_document_storage,
                )
                return await service.prune_old_documents(retention_days, dry_run, knowledge_base_ids=tuple(knowledge_base_ids))

    async def close(self):
        """原件与索引客户端随各步骤关闭，这里只关闭本次创建的清理连接。"""
        if self.retention_client is not None:
            await self.retention_client.close()


__all__ = ["PipelineRunOnceExecutionResult", "PipelineWriteRuntime"]
