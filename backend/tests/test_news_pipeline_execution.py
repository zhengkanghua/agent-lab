"""阶段 5 一次性新闻同步与待索引批次执行 Service 的完全离线测试。

测试注入 fake Session、Repository、FreshRSS Import Service 和 Document Indexing Service，
不访问真实 PostgreSQL、FreshRSS、Ollama 或 Qdrant。重点验证批次上限、stale requeue、
独立工作单元、顺序继续执行和安全失败摘要。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

import news_vector_service.services.news_pipeline_execution_service as execution_module
from news_vector_service.services.document_indexing_service import DocumentIndexingResult
from news_vector_service.services.freshrss_import_service import FreshRSSImportResult
from news_vector_service.services.news_pipeline_execution_service import (
    NewsPipelineExecutionService,
)


def run(coroutine: Any) -> Any:
    """执行测试协程，不引入额外异步测试插件。"""

    return asyncio.run(coroutine)


class FakeSessionContext:
    """为每次工作单元返回独立标识，不执行数据库 I/O。"""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *_args: Any) -> None:
        return None


class FakeSessionFactory:
    """记录创建次数并为每次调用生成不同 fake Session。"""

    def __init__(self) -> None:
        self.sessions: list[Any] = []

    def __call__(self) -> FakeSessionContext:
        session = SimpleNamespace(sequence=len(self.sessions))
        self.sessions.append(session)
        return FakeSessionContext(session)


class FakeImportService:
    """返回预设数量记录并捕获同步参数。"""

    def __init__(self, record_count: int = 3) -> None:
        self.records = [object() for _ in range(record_count)]
        self.calls: list[tuple[Any, int]] = []

    async def import_recent_per_source(
        self,
        session: Any,
        *,
        limit_per_source: int,
    ) -> FreshRSSImportResult:
        self.calls.append((session, limit_per_source))
        return FreshRSSImportResult(
            source_count=2,
            synchronized_count=len(self.records),
            checkpoint_advanced_count=2,
            failures=(),
        )


class FakeCandidateRepository:
    """返回预设候选并记录 stale cutoff 和批量上限。"""

    def __init__(self, candidate_ids: list[UUID], *, requeued: int = 0) -> None:
        self.candidate_ids = candidate_ids
        self.requeued = requeued
        self.started_before: datetime | None = None
        self.limit: int | None = None

    async def requeue_stale_processing(self, *, started_before: datetime) -> int:
        self.started_before = started_before
        return self.requeued

    async def list_index_candidate_ids(self, *, limit: int) -> list[UUID]:
        self.limit = limit
        return self.candidate_ids[:limit]


class FakeIndexingService:
    """按文档 ID 返回 indexed/skipped 或抛出预设异常。"""

    def __init__(self, behavior: dict[UUID, str | Exception]) -> None:
        self.behavior = behavior
        self.calls: list[tuple[Any, UUID]] = []

    async def index_document(
        self,
        session: Any,
        document_id: UUID,
    ) -> DocumentIndexingResult:
        self.calls.append((session, document_id))
        outcome = self.behavior[document_id]
        if isinstance(outcome, Exception):
            raise outcome
        return DocumentIndexingResult(
            document_id=document_id,
            index_revision=1,
            indexed=outcome == "indexed",
            skipped=outcome == "skipped",
        )


def test_sync_news_uses_one_session_and_reports_processed_count() -> None:
    session_factory = FakeSessionFactory()
    import_service = FakeImportService(record_count=4)
    executor = NewsPipelineExecutionService(session_factory)  # type: ignore[arg-type]

    result = run(
        executor.sync_news(  # type: ignore[arg-type]
            import_service,
            limit_per_source=2,
        )
    )

    assert result.synchronized_count == 4
    assert result.source_count == 2
    assert result.successful_source_count == 2
    assert result.checkpoint_advanced_count == 2
    assert import_service.calls == [(session_factory.sessions[0], 2)]
    assert len(session_factory.sessions) == 1


def test_sync_news_rejects_invalid_limit_before_opening_session() -> None:
    session_factory = FakeSessionFactory()
    executor = NewsPipelineExecutionService(session_factory)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="limit_per_source"):
        run(
            executor.sync_news(  # type: ignore[arg-type]
                FakeImportService(),
                limit_per_source=0,
            )
        )

    assert session_factory.sessions == []


def test_index_pending_requeues_then_uses_an_independent_session_per_document(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    document_ids = [uuid4(), uuid4(), uuid4(), uuid4()]
    fixed_now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    repository = FakeCandidateRepository(document_ids, requeued=2)
    monkeypatch.setattr(
        execution_module,
        "DocumentRepository",
        lambda _session: repository,
    )
    indexing_service = FakeIndexingService(
        {
            document_ids[0]: "indexed",
            document_ids[1]: "skipped",
            document_ids[2]: RuntimeError("secret remote response"),
            document_ids[3]: "indexed",
        }
    )
    session_factory = FakeSessionFactory()
    executor = NewsPipelineExecutionService(
        session_factory,  # type: ignore[arg-type]
        clock=lambda: fixed_now,
    )

    with caplog.at_level("INFO"):
        result = run(
            executor.index_pending(  # type: ignore[arg-type]
                indexing_service,
                batch_size=4,
                stale_after=timedelta(minutes=60),
            )
        )

    assert repository.started_before == fixed_now - timedelta(minutes=60)
    assert repository.limit == 4
    assert result.candidate_count == 4
    assert result.requeued_stale_count == 2
    assert result.indexed_count == 2
    assert result.skipped_count == 1
    assert result.failed_count == 1
    assert result.failures[0].document_id == document_ids[2]
    assert result.failures[0].error_type == "RuntimeError"
    assert [item[1] for item in indexing_service.calls] == document_ids
    assert len(session_factory.sessions) == 1 + len(document_ids)
    assert len({id(item[0]) for item in indexing_service.calls}) == len(document_ids)
    assert "secret remote response" not in caplog.text


def test_index_pending_processes_only_one_bounded_candidate_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_ids = [uuid4(), uuid4(), uuid4()]
    repository = FakeCandidateRepository(document_ids)
    monkeypatch.setattr(
        execution_module,
        "DocumentRepository",
        lambda _session: repository,
    )
    indexing_service = FakeIndexingService(
        {document_id: "indexed" for document_id in document_ids}
    )
    executor = NewsPipelineExecutionService(FakeSessionFactory())  # type: ignore[arg-type]

    result = run(
        executor.index_pending(  # type: ignore[arg-type]
            indexing_service,
            batch_size=2,
            stale_after=timedelta(hours=1),
        )
    )

    assert result.candidate_count == 2
    assert result.indexed_count == 2
    assert [item[1] for item in indexing_service.calls] == document_ids[:2]


@pytest.mark.parametrize(
    ("batch_size", "stale_after", "message"),
    [
        (0, timedelta(hours=1), "batch_size"),
        (1, timedelta(0), "stale_after"),
        (1, timedelta(seconds=-1), "stale_after"),
    ],
)
def test_index_pending_rejects_invalid_bounds_before_session_io(
    batch_size: int,
    stale_after: timedelta,
    message: str,
) -> None:
    session_factory = FakeSessionFactory()
    executor = NewsPipelineExecutionService(session_factory)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=message):
        run(
            executor.index_pending(  # type: ignore[arg-type]
                SimpleNamespace(),
                batch_size=batch_size,
                stale_after=stale_after,
            )
        )

    assert session_factory.sessions == []


def test_index_pending_rejects_naive_execution_clock() -> None:
    session_factory = FakeSessionFactory()
    executor = NewsPipelineExecutionService(
        session_factory,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 14, 12, 0),
    )

    with pytest.raises(ValueError, match="时区信息"):
        run(
            executor.index_pending(  # type: ignore[arg-type]
                SimpleNamespace(),
                batch_size=1,
                stale_after=timedelta(hours=1),
            )
        )

    assert session_factory.sessions == []
