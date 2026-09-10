"""任务业务适配：公开 Runtime 调用与脱敏统计，不依赖调度器。"""

from collections import Counter
from datetime import timedelta


def _failures(values):
    return dict(sorted(Counter(failure.error_type for failure in values).items()))


async def sync_news(runtime, params):
    result = await runtime.sync_only(limit_per_source=params["limit_per_source"])
    return {
        "source_count": result.source_count,
        "successful_source_count": result.successful_source_count,
        "synchronized_document_count": result.synchronized_count,
        "checkpoint_advanced_count": result.checkpoint_advanced_count,
        "failed_source_count": result.failed_source_count,
        "failures": _failures(result.failures),
    }


async def index_pending(runtime, params):
    result = await runtime.index_only(
        batch_size=params["batch_size"], stale_after=timedelta(minutes=params["stale_after_minutes"]),
    )
    return {
        "requeued_stale_count": result.requeued_stale_count,
        "candidate_count": result.candidate_count,
        "parsed_count": result.parsed_count,
        "review_count": result.review_count,
        "cleaned_count": result.cleaned_count,
        "indexed_count": result.indexed_count,
        "skipped_count": result.skipped_count,
        "failed_count": result.failed_count,
        "failures": _failures(result.failures),
    }


async def prune_old_documents(runtime, params):
    result = await runtime.prune_old_documents(**params)
    return result.to_job_run_stats()
