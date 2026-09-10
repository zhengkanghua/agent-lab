"""后台、手动 Pipeline 和定时索引共用的有界文档处理批次。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from agent_lab.knowledge.processing.adoption import DocumentAdoptionApplication
from agent_lab.knowledge.processing.application import DocumentProcessingApplication


@dataclass(frozen=True, slots=True)
class IndexExecutionFailure:
    document_id: UUID
    error_type: str


@dataclass(frozen=True, slots=True)
class PendingIndexExecutionResult:
    """处理回执的安全汇总；待审核与未采用明确分开，不误报成索引成功。"""

    candidate_count: int
    requeued_stale_count: int
    indexed_count: int
    skipped_count: int
    failures: tuple[IndexExecutionFailure, ...]
    parsed_count: int = 0
    review_count: int = 0
    cleaned_count: int = 0

    @property
    def failed_count(self):
        return len(self.failures)


class DocumentProcessingBatch:
    """每阶段有界消费；失败不自动重领，远端不确定写入仍由既有协调器拦截。"""

    def __init__(self, processing: DocumentProcessingApplication, adoption: DocumentAdoptionApplication):
        self._processing, self._adoption = processing, adoption

    async def run(self, *, batch_size: int, stale_after: timedelta) -> PendingIndexExecutionResult:
        if batch_size < 1 or stale_after <= timedelta(0):
            raise ValueError("处理批量和解析领取超时必须大于零。")
        requeued = await self._processing.requeue_computations(started_before=datetime.now(UTC) - stale_after)
        receipts = {}
        parsed = 0
        for _ in range(batch_size):
            result = await self._processing.process()
            if result is None:
                break
            parsed += 1
            receipts[result.processing_id] = result
        for _ in range(batch_size):
            result = await self._adoption.process()
            if result is None:
                break
            receipts[result.processing_id] = result
        cleaned = 0
        for _ in range(batch_size):
            if not await self._adoption.cleanup_one():
                break
            cleaned += 1
        failures = tuple(IndexExecutionFailure(item.document_id, item.error_code or "document_processing_failed")
                         for item in receipts.values() if item.state in {"failed", "adoption_failed"})
        indexed = sum(item.state == "adopted" for item in receipts.values())
        review = sum(item.state == "review" for item in receipts.values())
        return PendingIndexExecutionResult(
            candidate_count=len(receipts), requeued_stale_count=requeued,
            indexed_count=indexed, skipped_count=len(receipts) - indexed - review - len(failures),
            failures=failures, parsed_count=parsed, review_count=review, cleaned_count=cleaned,
        )
