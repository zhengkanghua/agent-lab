"""同步与统一文档批次的边界和可观察统计，不模拟旧正文索引状态机。"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent_lab.knowledge.document_contracts import SourceImportResult
from agent_lab.knowledge.processing.batch import DocumentProcessingBatch
from agent_lab.knowledge.processing.lifecycle import ProcessingReceipt
from agent_lab.services.news_pipeline_execution_service import NewsPipelineExecutionService


def test_sync_reports_received_count_and_rejects_invalid_bound():
    async def verify():
        importer = SimpleNamespace(import_recent_per_source=AsyncMock(return_value=SourceImportResult(
            source_count=2, synchronized_count=4, checkpoint_advanced_count=2, failures=(),
        )))
        executor = NewsPipelineExecutionService()
        result = await executor.sync_news(importer, limit_per_source=2)
        assert (result.synchronized_count, result.successful_source_count, result.checkpoint_advanced_count) == (4, 2, 2)
        with pytest.raises(ValueError):
            await executor.sync_news(importer, limit_per_source=0)
        importer.import_recent_per_source.assert_awaited_once_with(limit_per_source=2)
    asyncio.run(verify())


def receipt(state, *, processing_id=None, document_id=None, error=None):
    return ProcessingReceipt(processing_id or uuid4(), document_id or uuid4(), state, "a" * 64, error_code=error)


def test_batch_keeps_review_failures_and_adoptions_distinct():
    async def verify():
        ready, review, failed = receipt("ready"), receipt("review"), receipt("failed", error="object_storage_get_failed")
        adopted = receipt("adopted", processing_id=ready.processing_id, document_id=ready.document_id)
        processing = SimpleNamespace(requeue_computations=AsyncMock(return_value=1),
                                     process=AsyncMock(side_effect=[ready, review, failed, None]))
        adoption = SimpleNamespace(process=AsyncMock(side_effect=[adopted, None]),
                                   cleanup_one=AsyncMock(side_effect=[True, False]))
        result = await DocumentProcessingBatch(processing, adoption).run(batch_size=10, stale_after=timedelta(hours=1))
        assert (result.candidate_count, result.parsed_count, result.indexed_count, result.review_count) == (3, 3, 1, 1)
        assert result.failed_count == 1 and result.failures[0].document_id == failed.document_id
        assert result.failures[0].error_type == "object_storage_get_failed"
        assert result.requeued_stale_count == result.cleaned_count == 1
        assert result.skipped_count == 0
    asyncio.run(verify())


def test_batch_does_not_drain_unbounded_work_or_retry_failures():
    async def verify():
        pending = [receipt("review") for _ in range(3)]
        processing = SimpleNamespace(requeue_computations=AsyncMock(return_value=0),
                                     process=AsyncMock(side_effect=pending))
        adoption = SimpleNamespace(process=AsyncMock(return_value=None), cleanup_one=AsyncMock(return_value=False))
        result = await DocumentProcessingBatch(processing, adoption).run(batch_size=2, stale_after=timedelta(hours=1))
        assert result.candidate_count == result.review_count == 2
        assert processing.process.await_count == 2
    asyncio.run(verify())


@pytest.mark.parametrize("batch_size, stale_after", [(0, timedelta(hours=1)), (1, timedelta(0))])
def test_invalid_batch_bounds_do_not_touch_dependencies(batch_size, stale_after):
    with pytest.raises(ValueError):
        asyncio.run(DocumentProcessingBatch(None, None).run(batch_size=batch_size, stale_after=stale_after))
