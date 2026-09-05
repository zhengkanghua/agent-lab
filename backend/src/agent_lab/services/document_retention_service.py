"""连续分批清理已索引旧 Document；预演和失败都沿稳定游标推进。"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agent_lab.domain.write_scope import WriteRecoveryRequiredError
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository
from agent_lab.services.write_coordination import ensure_write_confirmed

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PruneResult:
    documents_deleted: int
    qdrant_points_deleted: int
    oldest_deleted_date: datetime | None
    dry_run: bool
    failed_batches: int
    failures: dict[str, int] = field(default_factory=dict)
    failed_documents: int = 0

    def to_job_run_stats(self) -> dict:
        # Qdrant 返回写入确认而不是删除数量，数量是删除前的精确匹配计数。
        return {
            "documents_deleted": self.documents_deleted,
            "qdrant_points_deleted": self.qdrant_points_deleted,
            "point_count_basis": "matched_before_delete",
            "oldest_deleted_date": self.oldest_deleted_date.isoformat() if self.oldest_deleted_date else None,
            "dry_run": self.dry_run,
            "failed_batches": self.failed_batches,
            "failures": self.failures,
            "failed_documents": self.failed_documents,
        }


class DocumentRetentionService:
    """持有一次清理的 Repository 与公开 Qdrant 删除能力，不装配依赖。"""

    def __init__(self, repository: DocumentRetentionRepository, qdrant_store, *, clock=None) -> None:
        self._repository = repository
        self._qdrant_store = qdrant_store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._batch_size = 50

    async def prune_old_documents(self, retention_days: int, dry_run: bool = True) -> PruneResult:
        if not 30 <= retention_days <= 730:
            raise ValueError("retention_days 必须介于 30 和 730 之间。")
        cutoff = self._clock() - timedelta(days=retention_days)
        count = points = 0
        oldest = None
        failures: Counter = Counter()
        failed_documents = 0

        def result():
            return PruneResult(count, points, oldest, dry_run, sum(failures.values()), dict(failures), failed_documents)

        async def process(records):
            nonlocal count, points, oldest, failed_documents
            if not records:
                return
            try:
                if not dry_run:
                    await self._repository.verify(records)
                ids = [str(record.document_id) for record in records]
                matched = await self._qdrant_store.count_by_document_ids(ids)
                if dry_run:
                    deleted = len(records)
                else:
                    await self._qdrant_store.delete_by_document_ids(ids)
                    # 远端已确认的数量与数据库成功数分别累计，不能混成同一个结果。
                    points += matched
                    await self._repository.mark_qdrant_deleted(records)
                    deleted = await self._repository.finish(records)
                count += deleted
                if dry_run:
                    points += matched
                if deleted:
                    date = min(record.retention_date for record in records)
                    oldest = date if oldest is None else min(oldest, date)
            except Exception as exc:
                error_type = type(exc).__name__
                failures[error_type] += 1
                failed_documents += len(records)
                if not dry_run:
                    try:
                        await self._repository.record_error(records, error_type)
                    except Exception as record_error:
                        logger.error("清理失败信息保存失败 error_type=%s", type(record_error).__name__)
                logger.error("清理批次失败 count=%s error_type=%s", len(records), error_type)
                try:
                    ensure_write_confirmed()
                except WriteRecoveryRequiredError as interrupted:
                    interrupted.stats = result().to_job_run_stats()
                    raise

        # 先恢复上次未完成的意图；预演绝不写意图，也不执行恢复删除。
        if not dry_run:
            after_id = None
            while records := await self._repository.pending(after_id, self._batch_size):
                after_id = records[-1].document_id
                await process(records)

        after = None
        while candidates := await self._repository.candidates(cutoff, after, self._batch_size):
            after = candidates[-1]
            records = candidates if dry_run else await self._repository.prepare(candidates, cutoff)
            await process(records)
        return result()
