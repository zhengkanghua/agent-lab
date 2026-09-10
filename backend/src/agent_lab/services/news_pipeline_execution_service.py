"""一次 FreshRSS 同步的协调与安全汇总；文档处理由统一批次能力负责。"""

import logging
from contextlib import nullcontext
from dataclasses import dataclass

from agent_lab.knowledge.document_contracts import SourceSyncFailure
from agent_lab.knowledge.importing import SourceImportService
from agent_lab.knowledge.ports import KnowledgeWriteCoordinator
from agent_lab.knowledge.processing.batch import IndexExecutionFailure, PendingIndexExecutionResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NewsSyncExecutionResult:
    """已接收资料和 checkpoint 统计，不代表解析或采用完成。"""

    synchronized_count: int
    source_count: int = 0
    successful_source_count: int = 0
    checkpoint_advanced_count: int = 0
    failures: tuple[SourceSyncFailure, ...] = ()

    @property
    def failed_source_count(self) -> int:
        return len(self.failures)


class NewsPipelineExecutionService:
    def __init__(self, *, coordinator: KnowledgeWriteCoordinator | None = None):
        self._coordinator = coordinator

    def writing(self, resources: tuple[str, ...]):
        return self._coordinator.hold(resources) if self._coordinator else nullcontext()

    async def sync_news(self, import_service: SourceImportService, *, limit_per_source: int) -> NewsSyncExecutionResult:
        if limit_per_source < 1:
            raise ValueError("limit_per_source 必须大于零。")
        async with self.writing(("sync",)):
            result = await import_service.import_recent_per_source(limit_per_source=limit_per_source)
        logger.info("FreshRSS 接收完成 sources=%d documents=%d failed_sources=%d",
                    result.source_count, result.synchronized_count, result.failed_source_count)
        return NewsSyncExecutionResult(
            synchronized_count=result.synchronized_count, source_count=result.source_count,
            successful_source_count=result.successful_source_count,
            checkpoint_advanced_count=result.checkpoint_advanced_count, failures=result.failures,
        )


# CLI 与 HTTP 继续从本入口导入结果契约，执行逻辑只有 processing.batch 一份。
__all__ = ["IndexExecutionFailure", "PendingIndexExecutionResult", "NewsSyncExecutionResult", "NewsPipelineExecutionService"]
