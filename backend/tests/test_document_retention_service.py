"""使用不会自动消耗候选的替身验证遍历、预演、部分成功与删除待办。"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from agent_lab.repositories.document_retention_repository import RetentionCandidate
from agent_lab.services.document_retention_service import DocumentRetentionService, PruneResult

NOW = datetime(2026, 9, 5, tzinfo=UTC)
OLD = NOW - timedelta(days=200)


class FakeRepository:
    """查询由游标和当前记录决定；同一查询会再次返回同一批数据。"""

    def __init__(self, count=120):
        self.documents = {
            UUID(int=i): RetentionCandidate(UUID(int=i), 1, OLD)
            for i in range(1, count + 1)
        }
        self.intents = {}
        self.queries = []
        self.writes = []
        self.fail_finish = False

    async def candidates(self, cutoff, after, limit):
        self.queries.append((cutoff, after, limit))
        assert len(self.queries) < 40, "候选遍历没有推进"
        return sorted([
            item for item in self.documents.values()
            if item.retention_date < cutoff and item.document_id not in self.intents
            and (after is None or (item.retention_date, item.document_id) > (after.retention_date, after.document_id))
        ], key=lambda item: (item.retention_date, item.document_id))[:limit]

    async def pending(self, after, limit):
        return [self.intents[key] for key in sorted(self.intents) if after is None or key > after][:limit]

    async def prepare(self, candidates, cutoff):
        self.writes.append("prepare")
        records = [SimpleNamespace(
            document_id=item.document_id, revision=item.revision,
            retention_date=item.retention_date, cutoff_date=cutoff,
            qdrant_deleted=False, error_type=None,
        ) for item in candidates]
        self.intents.update({item.document_id: item for item in records})
        return records

    async def mark_qdrant_deleted(self, records):
        self.writes.append("qdrant_confirmed")
        for item in records:
            self.intents[item.document_id].qdrant_deleted = True

    async def verify(self, records):
        for record in records:
            current = self.documents.get(record.document_id)
            if current and current.revision != record.revision:
                raise RuntimeError("版本已改变")

    async def finish(self, records):
        self.writes.append("finish")
        if self.fail_finish:
            raise RuntimeError("模拟数据库收尾失败，不应回显")
        for item in records:
            self.documents.pop(item.document_id, None)
            self.intents.pop(item.document_id, None)
        return len(records)

    async def record_error(self, records, error_type):
        self.writes.append("error")
        for item in records:
            self.intents[item.document_id].error_type = error_type


class FakeQdrant:
    def __init__(self):
        self.deleted = set()
        self.count_calls = []
        self.delete_calls = []
        self.fail_count_at = None
        self.fail_delete = False

    async def count_by_document_ids(self, ids):
        self.count_calls.append(ids)
        if len(self.count_calls) == self.fail_count_at:
            raise RuntimeError("统计请求失败")
        return sum(2 for item in ids if item not in self.deleted)

    async def delete_by_document_ids(self, ids):
        self.delete_calls.append(ids)
        if self.fail_delete:
            raise RuntimeError("删除请求失败")
        self.deleted.update(ids)
        # 生产 Qdrant 只有完成确认，不返回删除数量。


def prune(repo, store, *, dry_run=False):
    return asyncio.run(DocumentRetentionService(repo, store, clock=lambda: NOW).prune_old_documents(180, dry_run))


@pytest.mark.parametrize("dry_run", [True, False])
def test_continuous_batches_have_no_total_limit_and_use_fixed_cutoff(dry_run):
    repo, store = FakeRepository(1000), FakeQdrant()
    result = prune(repo, store, dry_run=dry_run)
    assert result.documents_deleted == 1000
    assert result.qdrant_points_deleted == 2000
    assert len(store.count_calls) == 20
    assert all(len(ids) <= 50 for ids in store.count_calls)
    assert {query[0] for query in repo.queries} == {NOW - timedelta(days=180)}
    assert result.oldest_deleted_date == OLD
    if dry_run:
        assert len(repo.documents) == 1000
        assert repo.writes == []
        assert store.delete_calls == []
    else:
        assert repo.documents == repo.intents == {}
        assert repo.writes[:3] == ["prepare", "qdrant_confirmed", "finish"]


def test_failed_count_advances_to_other_batches_without_retrying_same_targets():
    repo, store = FakeRepository(), FakeQdrant()
    store.fail_count_at = 2
    result = prune(repo, store)
    assert result.documents_deleted == 70
    assert result.failed_batches == 1
    assert result.failed_documents == 50
    assert result.failures == {"RuntimeError": 1}
    assert len(store.count_calls) == 3
    assert len(repo.intents) == 50
    resumed = prune(repo, store)
    assert resumed.documents_deleted == 50
    assert repo.documents == repo.intents == {}


def test_remote_failure_preserves_intent_without_deleting_postgres():
    repo, store = FakeRepository(3), FakeQdrant()
    store.fail_delete = True
    result = prune(repo, store)
    assert result.documents_deleted == result.qdrant_points_deleted == 0
    assert result.failed_batches == 1
    assert len(repo.documents) == len(repo.intents) == 3
    assert all(item.error_type == "RuntimeError" for item in repo.intents.values())
    assert all(not item.qdrant_deleted for item in repo.intents.values())
    assert "finish" not in repo.writes


def test_postgres_failure_keeps_remote_progress_and_next_execution_finishes():
    repo, store = FakeRepository(3), FakeQdrant()
    repo.fail_finish = True
    first = prune(repo, store)
    assert first.documents_deleted == 0
    assert first.qdrant_points_deleted == 6
    assert len(repo.intents) == len(repo.documents) == 3
    assert all(item.qdrant_deleted for item in repo.intents.values())
    repo.fail_finish = False
    second = prune(repo, store)
    assert second.documents_deleted == 3
    assert second.qdrant_points_deleted == 0
    assert repo.documents == repo.intents == {}


def test_dry_run_does_not_recover_or_update_existing_intents():
    repo, store = FakeRepository(3), FakeQdrant()
    asyncio.run(repo.prepare(list(repo.documents.values()), NOW))
    repo.writes.clear()
    result = prune(repo, store, dry_run=True)
    assert result.documents_deleted == 0
    assert len(repo.intents) == 3
    assert repo.writes == store.delete_calls == []


def test_changed_version_is_not_deleted_when_resuming_intent():
    repo, store = FakeRepository(1), FakeQdrant()
    asyncio.run(repo.prepare(list(repo.documents.values()), NOW))
    document_id = UUID(int=1)
    repo.documents[document_id] = RetentionCandidate(document_id, 2, OLD)
    result = prune(repo, store)
    assert result.failed_documents == 1
    assert len(repo.documents) == len(repo.intents) == 1
    assert store.delete_calls == []


@pytest.mark.parametrize("days", [29, 731])
def test_retention_days_validation(days):
    repo, store = FakeRepository(), FakeQdrant()
    with pytest.raises(ValueError, match="retention_days"):
        asyncio.run(DocumentRetentionService(repo, store).prune_old_documents(days))
    assert repo.queries == []


def test_result_distinguishes_document_and_point_counts():
    stats = PruneResult(3, 8, OLD, True, 1, {"RuntimeError": 1}, 2).to_job_run_stats()
    assert stats["documents_deleted"] == 3
    assert stats["qdrant_points_deleted"] == 8
    assert stats["point_count_basis"] == "matched_before_delete"
    assert stats["oldest_deleted_date"] == OLD.isoformat()
    assert stats["dry_run"] is True
    assert stats["failed_documents"] == 2
