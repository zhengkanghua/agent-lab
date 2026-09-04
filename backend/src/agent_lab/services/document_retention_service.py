"""数据保留策略执行服务：定期删除超过保留期的旧新闻及其向量索引。

本服务只删除 `indexed` 状态的文档，避免与索引任务冲突；`failed` 文档会被 `index_pending`
任务自动重试，不在此清理。删除流程采用两阶段提交模拟：PostgreSQL 事务内先调用 Qdrant 删除，
成功后再执行 PG DELETE 并提交；Qdrant 失败时事务自动回滚，保证"Qdrant 失败不删 PG"。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.qdrant.store import QdrantChunkStore, QdrantPointStoreError
from agent_lab.repositories.document_repository import DocumentRepository


@dataclass(frozen=True, slots=True)
class PruneResult:
    """一次清理任务的执行统计。"""

    documents_deleted: int
    qdrant_points_deleted: int
    oldest_deleted_date: datetime | None
    dry_run: bool
    failed_batches: int

    def to_job_run_stats(self) -> dict[str, Any]:
        """转换为 JobRunRecord.stats JSON 格式。"""
        return {
            "documents_deleted": self.documents_deleted,
            "qdrant_points_deleted": self.qdrant_points_deleted,
            "oldest_deleted_date": (
                self.oldest_deleted_date.isoformat() if self.oldest_deleted_date else None
            ),
            "dry_run": self.dry_run,
            "failed_batches": self.failed_batches,
        }


class DocumentRetentionService:
    """负责删除超过保留期的旧新闻及其向量索引。

    Service 生命周期覆盖单次清理任务，不跨任务复用。每批删除在独立事务中完成，
    单批失败不影响其他批次。
    """

    def __init__(
        self,
        session: AsyncSession,
        document_repo: DocumentRepository,
        qdrant_store: QdrantChunkStore,
    ) -> None:
        """绑定数据库会话、Repository 和 Qdrant Store。

        Args:
            session: 由调用方管理的 AsyncSession，用于事务控制。
            document_repo: 文档 Repository，查询和删除文档。
            qdrant_store: Qdrant Store，删除向量索引。
        """
        self._session = session
        self._document_repo = document_repo
        self._qdrant_store = qdrant_store
        self._batch_size = 50

    async def prune_old_documents(
        self,
        retention_days: int,
        dry_run: bool = True,
    ) -> PruneResult:
        """删除发布时间超过保留期的旧新闻。

        Args:
            retention_days: 保留天数，30-730 天范围。
            dry_run: 预演模式，True 时只统计不删除。

        Returns:
            执行统计，包含删除数量、最旧日期和失败批次数。

        Raises:
            ValueError: retention_days 超出范围。
            Exception: PostgreSQL 或 Qdrant 操作失败时传播。

        Notes:
            删除条件：published_at < cutoff_date OR (published_at IS NULL AND
            created_at < cutoff_date)。只删除 `indexed` 状态文档，分批执行，
            每批独立事务。Qdrant 删除失败时 PostgreSQL 自动回滚。
        """
        if not 30 <= retention_days <= 730:
            raise ValueError(f"retention_days 必须在 30-730 范围内，实际：{retention_days}")

        cutoff_date = datetime.now(UTC) - timedelta(days=retention_days)
        total_documents_deleted = 0
        total_points_deleted = 0
        oldest_deleted: datetime | None = None
        failed_batches = 0

        while True:
            # 查询下一批待删除文档 ID
            doc_ids = await self._document_repo.query_documents_to_delete(
                cutoff_date=cutoff_date,
                batch_size=self._batch_size,
                status_filter=ProcessingStatus.INDEXED,
            )

            if not doc_ids:
                break

            # 处理这一批
            batch_result = await self._delete_batch(doc_ids, dry_run)
            if batch_result["success"]:
                total_documents_deleted += batch_result["documents_deleted"]
                total_points_deleted += batch_result["points_deleted"]
                if batch_result["oldest_date"] is not None:
                    if oldest_deleted is None or batch_result["oldest_date"] < oldest_deleted:
                        oldest_deleted = batch_result["oldest_date"]
            else:
                failed_batches += 1

        return PruneResult(
            documents_deleted=total_documents_deleted,
            qdrant_points_deleted=total_points_deleted,
            oldest_deleted_date=oldest_deleted,
            dry_run=dry_run,
            failed_batches=failed_batches,
        )

    async def _delete_batch(
        self,
        doc_ids: list[UUID],
        dry_run: bool,
    ) -> dict[str, Any]:
        """删除一批文档及其 Qdrant Point。

        Args:
            doc_ids: 要删除的文档 ID 列表。
            dry_run: True 时只统计不删除。

        Returns:
            批次结果字典，包含 success、documents_deleted、points_deleted、oldest_date。

        Notes:
            在 PostgreSQL 事务内执行：先 Qdrant 删除，成功后 PG DELETE，最后提交。
            Qdrant 失败时事务回滚，PG DELETE 不执行。需要查询最旧发布时间用于统计。
        """
        doc_id_strs = [str(doc_id) for doc_id in doc_ids]

        try:
            # dry_run 模式：统计 Point 数量，不删除
            if dry_run:
                points_count = await self._qdrant_store.count_by_document_ids(doc_id_strs)
                oldest_date = await self._query_oldest_published_at(doc_ids)
                return {
                    "success": True,
                    "documents_deleted": len(doc_ids),
                    "points_deleted": points_count,
                    "oldest_date": oldest_date,
                }

            # 真实删除模式：先查询最旧时间（删除前），再执行删除
            oldest_date = await self._query_oldest_published_at(doc_ids)

            # 1. 先删除 Qdrant Point（事务外异步调用）
            points_deleted = await self._qdrant_store.delete_by_document_ids(doc_id_strs)

            # 2. Qdrant 成功后，在同一事务内删除 PostgreSQL 文档
            documents_deleted = await self._document_repo.delete_by_ids(doc_ids)

            # 3. 提交事务
            await self._session.commit()

            return {
                "success": True,
                "documents_deleted": documents_deleted,
                "points_deleted": points_deleted,
                "oldest_date": oldest_date,
            }

        except QdrantPointStoreError as exc:
            # Qdrant 失败，回滚 PostgreSQL 事务
            await self._session.rollback()
            # 记录错误日志（简化版，生产应使用 structlog）
            print(
                f"ERROR: Batch failed: {len(doc_ids)} documents "
                f"(oldest: {oldest_date}) - Qdrant error: {exc} - Will retry in next execution"
            )
            return {
                "success": False,
                "documents_deleted": 0,
                "points_deleted": 0,
                "oldest_date": None,
            }

        except Exception as exc:
            # PostgreSQL 或其他异常，回滚事务
            await self._session.rollback()
            print(
                f"ERROR: Batch failed: {len(doc_ids)} documents - {type(exc).__name__}: {exc}"
            )
            return {
                "success": False,
                "documents_deleted": 0,
                "points_deleted": 0,
                "oldest_date": None,
            }

    async def _query_oldest_published_at(self, doc_ids: list[UUID]) -> datetime | None:
        """查询这批文档中最旧的发布时间。

        Args:
            doc_ids: 文档 ID 列表。

        Returns:
            最旧的 published_at 或 created_at；全部为 NULL 时返回 None。

        Notes:
            用于统计，需要在删除前查询。优先使用 published_at，NULL 时回退到 created_at。
        """
        from sqlalchemy import func, select, case

        from agent_lab.models.document import DocumentRecord

        # 构造 COALESCE(published_at, created_at) 并取最小值
        oldest_date_expr = func.min(
            func.coalesce(DocumentRecord.published_at, DocumentRecord.created_at)
        )

        statement = select(oldest_date_expr).where(DocumentRecord.id.in_(doc_ids))

        result = await self._session.scalar(statement)
        return result
