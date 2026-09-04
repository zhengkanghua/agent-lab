"""DocumentRetentionService 单元测试：验证删除逻辑、批次处理和失败回滚。

测试注入 fake Repository 和 Qdrant Store，不访问真实 PostgreSQL 或 Qdrant。
重点验证时间判断、状态过滤、分批删除、dry_run 模式和 Qdrant 失败回滚。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.qdrant.store import QdrantPointStoreError
from agent_lab.services.document_retention_service import (
    DocumentRetentionService,
    PruneResult,
)


def run(coroutine: Any) -> Any:
    """执行测试协程，不引入额外异步测试插件。"""
    return asyncio.run(coroutine)


class FakeSession:
    """记录 commit/rollback 调用，不执行数据库 I/O。"""

    def __init__(self) -> None:
        self.committed = 0
        self.rolled_back = 0
        self.scalars_results: list[Any] = []

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        self.rolled_back += 1

    async def scalar(self, statement: Any) -> datetime | None:
        """返回预设的最旧发布时间。"""
        if self.scalars_results:
            return self.scalars_results.pop(0)
        return None


class FakeDocumentRepository:
    """返回预设文档 ID 并记录删除调用。"""

    def __init__(
        self,
        *,
        batches: list[list[UUID]],
        oldest_dates: list[datetime | None] | None = None,
    ) -> None:
        self.batches = batches
        self.oldest_dates = oldest_dates or [None] * len(batches)
        self.current_batch = 0
        self.query_calls: list[tuple[datetime, int, ProcessingStatus]] = []
        self.delete_calls: list[list[UUID]] = []

    async def query_documents_to_delete(
        self,
        *,
        cutoff_date: datetime,
        batch_size: int,
        status_filter: ProcessingStatus,
    ) -> list[UUID]:
        self.query_calls.append((cutoff_date, batch_size, status_filter))
        if self.current_batch < len(self.batches):
            batch = self.batches[self.current_batch]
            self.current_batch += 1
            return batch
        return []

    async def delete_by_ids(self, document_ids: list[UUID]) -> int:
        self.delete_calls.append(document_ids)
        return len(document_ids)


class FakeQdrantStore:
    """模拟 Qdrant 删除和统计，可配置失败行为。"""

    def __init__(
        self,
        *,
        points_per_doc: int = 3,
        fail_on_batch: int | None = None,
    ) -> None:
        self.points_per_doc = points_per_doc
        self.fail_on_batch = fail_on_batch
        self.delete_calls: list[list[str]] = []
        self.count_calls: list[list[str]] = []
        self.current_delete = 0

    async def delete_by_document_ids(self, document_ids: list[str]) -> int:
        self.delete_calls.append(document_ids)
        if self.fail_on_batch is not None and self.current_delete == self.fail_on_batch:
            self.current_delete += 1
            raise QdrantPointStoreError("模拟 Qdrant 删除失败")
        self.current_delete += 1
        return len(document_ids) * self.points_per_doc

    async def count_by_document_ids(self, document_ids: list[str]) -> int:
        self.count_calls.append(document_ids)
        return len(document_ids) * self.points_per_doc


def test_prune_deletes_old_documents_only() -> None:
    """验证只删除超过保留期的文档，新文档保留。"""

    old_ids = [uuid4() for _ in range(5)]
    session = FakeSession()
    session.scalars_results = [datetime(2025, 1, 1, tzinfo=UTC)]  # 最旧日期

    repo = FakeDocumentRepository(
        batches=[old_ids, []],  # 第一批返回 5 个，第二批空表示结束
        oldest_dates=[datetime(2025, 1, 1, tzinfo=UTC)],
    )
    store = FakeQdrantStore(points_per_doc=2)

    service = DocumentRetentionService(session, repo, store)
    result = run(service.prune_old_documents(retention_days=180, dry_run=False))

    # 验证删除了 5 篇文档
    assert result.documents_deleted == 5
    assert result.qdrant_points_deleted == 10  # 5 * 2
    assert result.dry_run is False
    assert result.failed_batches == 0

    # 验证调用了正确的方法
    assert len(repo.query_calls) == 2  # 查询了两次（第二次返回空）
    assert len(repo.delete_calls) == 1  # 删除了一批
    assert repo.delete_calls[0] == old_ids

    assert len(store.delete_calls) == 1
    assert store.delete_calls[0] == [str(doc_id) for doc_id in old_ids]

    # 验证事务提交
    assert session.committed == 1
    assert session.rolled_back == 0


def test_dry_run_does_not_delete() -> None:
    """验证 dry_run=True 时只统计不删除。"""

    doc_ids = [uuid4() for _ in range(3)]
    session = FakeSession()
    session.scalars_results = [datetime(2025, 1, 1, tzinfo=UTC)]

    repo = FakeDocumentRepository(
        batches=[doc_ids, []],
        oldest_dates=[datetime(2025, 1, 1, tzinfo=UTC)],
    )
    store = FakeQdrantStore(points_per_doc=2)

    service = DocumentRetentionService(session, repo, store)
    result = run(service.prune_old_documents(retention_days=180, dry_run=True))

    # 验证统计正确
    assert result.documents_deleted == 3
    assert result.qdrant_points_deleted == 6  # 3 * 2
    assert result.dry_run is True
    assert result.failed_batches == 0

    # 验证只调用了 count，没有调用 delete
    assert len(store.count_calls) == 1
    assert len(store.delete_calls) == 0
    assert len(repo.delete_calls) == 0

    # dry_run 不提交事务
    assert session.committed == 0
    assert session.rolled_back == 0


def test_only_deletes_indexed_status() -> None:
    """验证只删除 indexed 状态文档，pending/processing 不删。"""

    doc_ids = [uuid4() for _ in range(5)]
    session = FakeSession()
    session.scalars_results = [datetime(2025, 1, 1, tzinfo=UTC)]

    repo = FakeDocumentRepository(
        batches=[doc_ids, []],
        oldest_dates=[datetime(2025, 1, 1, tzinfo=UTC)],
    )
    store = FakeQdrantStore()

    service = DocumentRetentionService(session, repo, store)
    run(service.prune_old_documents(retention_days=180, dry_run=False))

    # 验证查询时传入了 indexed 状态过滤
    assert len(repo.query_calls) == 2
    for call in repo.query_calls:
        cutoff_date, batch_size, status_filter = call
        assert status_filter == ProcessingStatus.INDEXED
        assert batch_size == 50  # 默认批量大小


def test_batch_deletion_processes_all() -> None:
    """验证分批删除：200 篇文档分 4 批（50/批）全部删除。"""

    # 准备 4 批，每批 50 个
    batches = [[uuid4() for _ in range(50)] for _ in range(4)]
    batches.append([])  # 最后一批空表示结束

    session = FakeSession()
    session.scalars_results = [datetime(2025, 1, 1, tzinfo=UTC)] * 4

    repo = FakeDocumentRepository(
        batches=batches,
        oldest_dates=[datetime(2025, 1, 1, tzinfo=UTC)] * 4,
    )
    store = FakeQdrantStore(points_per_doc=2)

    service = DocumentRetentionService(session, repo, store)
    result = run(service.prune_old_documents(retention_days=180, dry_run=False))

    # 验证删除了 200 篇文档
    assert result.documents_deleted == 200
    assert result.qdrant_points_deleted == 400  # 200 * 2
    assert result.failed_batches == 0

    # 验证分 4 批删除
    assert len(repo.delete_calls) == 4
    assert len(store.delete_calls) == 4

    # 验证事务提交了 4 次（每批一次）
    assert session.committed == 4
    assert session.rolled_back == 0


def test_qdrant_failure_rolls_back_pg() -> None:
    """验证 Qdrant 删除失败时 PostgreSQL 自动回滚。"""

    doc_ids = [uuid4() for _ in range(3)]
    session = FakeSession()
    session.scalars_results = [datetime(2025, 1, 1, tzinfo=UTC)]

    repo = FakeDocumentRepository(
        batches=[doc_ids, []],
        oldest_dates=[datetime(2025, 1, 1, tzinfo=UTC)],
    )
    # 配置第一批删除时失败
    store = FakeQdrantStore(fail_on_batch=0)

    service = DocumentRetentionService(session, repo, store)
    result = run(service.prune_old_documents(retention_days=180, dry_run=False))

    # 验证删除失败
    assert result.documents_deleted == 0
    assert result.qdrant_points_deleted == 0
    assert result.failed_batches == 1

    # 验证 Qdrant 尝试删除但失败
    assert len(store.delete_calls) == 1

    # 验证 PostgreSQL 未删除（因为异常在调用 delete_by_ids 前抛出）
    assert len(repo.delete_calls) == 0

    # 验证事务回滚
    assert session.committed == 0
    assert session.rolled_back == 1


def test_partial_batch_failure_continues() -> None:
    """验证 10 批中第 3 批失败，其他 9 批成功，failed_batches=1。"""

    # 准备 10 批，每批 5 个
    batches = [[uuid4() for _ in range(5)] for _ in range(10)]
    batches.append([])  # 结束标记

    session = FakeSession()
    session.scalars_results = [datetime(2025, 1, 1, tzinfo=UTC)] * 10

    repo = FakeDocumentRepository(
        batches=batches,
        oldest_dates=[datetime(2025, 1, 1, tzinfo=UTC)] * 10,
    )
    # 配置第 3 批（索引 2）删除时失败
    store = FakeQdrantStore(points_per_doc=2, fail_on_batch=2)

    service = DocumentRetentionService(session, repo, store)
    result = run(service.prune_old_documents(retention_days=180, dry_run=False))

    # 验证删除了 9 批共 45 篇文档（10 批 - 1 批失败）
    assert result.documents_deleted == 45
    assert result.qdrant_points_deleted == 90  # 45 * 2
    assert result.failed_batches == 1

    # 验证尝试删除了 10 次
    assert len(store.delete_calls) == 10

    # 验证 PostgreSQL 只删除了 9 批（失败的那批回滚了）
    assert len(repo.delete_calls) == 9

    # 验证事务：9 次提交，1 次回滚
    assert session.committed == 9
    assert session.rolled_back == 1


def test_retention_days_validation() -> None:
    """验证 retention_days 范围校验：30-730 天。"""

    session = FakeSession()
    repo = FakeDocumentRepository(batches=[[]])
    store = FakeQdrantStore()
    service = DocumentRetentionService(session, repo, store)

    # 小于 30 天应该抛异常
    with pytest.raises(ValueError, match="retention_days 必须在 30-730 范围内"):
        run(service.prune_old_documents(retention_days=29, dry_run=True))

    # 大于 730 天应该抛异常
    with pytest.raises(ValueError, match="retention_days 必须在 30-730 范围内"):
        run(service.prune_old_documents(retention_days=731, dry_run=True))

    # 30 和 730 都应该合法
    run(service.prune_old_documents(retention_days=30, dry_run=True))
    run(service.prune_old_documents(retention_days=730, dry_run=True))


def test_prune_result_to_job_run_stats() -> None:
    """验证 PruneResult 转换为 JobRunRecord.stats 格式。"""

    oldest_date = datetime(2025, 1, 1, 12, 30, tzinfo=UTC)
    result = PruneResult(
        documents_deleted=100,
        qdrant_points_deleted=250,
        oldest_deleted_date=oldest_date,
        dry_run=False,
        failed_batches=2,
    )

    stats = result.to_job_run_stats()

    assert stats["documents_deleted"] == 100
    assert stats["qdrant_points_deleted"] == 250
    assert stats["oldest_deleted_date"] == "2025-01-01T12:30:00+00:00"
    assert stats["dry_run"] is False
    assert stats["failed_batches"] == 2

    # 测试 None 的情况
    result_no_date = PruneResult(
        documents_deleted=0,
        qdrant_points_deleted=0,
        oldest_deleted_date=None,
        dry_run=True,
        failed_batches=0,
    )

    stats_no_date = result_no_date.to_job_run_stats()
    assert stats_no_date["oldest_deleted_date"] is None
