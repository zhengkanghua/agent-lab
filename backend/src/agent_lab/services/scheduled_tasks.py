"""任务业务适配：公开 Runtime 调用与脱敏统计，不依赖调度器。"""

from collections import Counter
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from datetime import timedelta

from agent_lab.domain.write_scope import WriteRecoveryRequiredError, WriteResourceBusyError
from agent_lab.ingestion.freshrss_client import FreshRSSConnectionError, FreshRSSTimeoutError, FreshRSSServiceError
from agent_lab.services.write_coordination import WriteCoordinator
from agent_lab.tasks.contracts import FailureDecision, ResourceWait

_pipeline_sync_release: ContextVar = ContextVar("pipeline_sync_release", default=None)


def writing_scope(resources):
    """任务准备阶段取得业务资源，正常占用让 Worker 返回并稍后检查。"""
    @asynccontextmanager
    async def scope(runtime, params):
        try:
            async with WriteCoordinator(runtime.session_factory).hold(resources, wait=False):
                yield
        except WriteResourceBusyError:
            raise ResourceWait("知识库写资源正被其他执行占用，释放后继续。") from None
    return scope


@asynccontextmanager
async def pipeline_scope(runtime, params):
    """只为同步准备资源；同步完成立即释放，后续采用沿用原有逐项 index 占用。"""
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(writing_scope(("sync",))(runtime, params))
        token = _pipeline_sync_release.set(stack.aclose)
        try:
            yield
        finally:
            _pipeline_sync_release.reset(token)


def classify_error(error):
    if isinstance(error, WriteRecoveryRequiredError):
        return FailureDecision(needs_attention=True, reason="write_outcome_unconfirmed",
            detail="旧执行者或远端写入状态尚未确认，请核实后使用维护入口。", stats=error.stats)
    # 只读 FreshRSS 短暂失败允许重试；不确定的远端写入已被写协调器转成待核实。
    safe = isinstance(error, (FreshRSSConnectionError, FreshRSSTimeoutError, FreshRSSServiceError))
    from agent_lab.api.error_contract import PIPELINE_ERROR_RULES, resolve_error_contract
    rule = resolve_error_contract(error, PIPELINE_ERROR_RULES)
    reason = getattr(error, "reason", None)
    if not isinstance(reason, str) or not reason.replace("_", "").isalnum() or len(reason) > 80:
        reason = rule.code
    return FailureDecision(retryable=safe, reason=reason, detail=rule.detail,
        stats={"error_code": rule.code, "retryable": safe})


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


async def run_pipeline(runtime, params):
    """同一 Pipeline 用例的结果只在执行详情里发布，保持原安全统计口径。"""
    from agent_lab.pipeline.write_runtime import PipelineRunOnceExecutionResult
    sync = await runtime.sync_only(limit_per_source=params["limit_per_source"])
    release_sync = _pipeline_sync_release.get()
    if release_sync is not None:
        await release_sync()
    index = await runtime.index_only(batch_size=params["batch_size"],
        stale_after=timedelta(minutes=params["stale_after_minutes"]))
    result = PipelineRunOnceExecutionResult(sync, index)
    return {
        "ok": not result.sync.failures and not result.index.failures,
        "execution_mode": "manual",
        "sync": {
            "source_count": result.sync.source_count,
            "successful_source_count": result.sync.successful_source_count,
            "failed_source_count": result.sync.failed_source_count,
            "synchronized_document_count": result.sync.synchronized_count,
            "checkpoint_advanced_count": result.sync.checkpoint_advanced_count,
            "failures": [{"error_type": key, "count": count} for key, count in _failures(result.sync.failures).items()],
        },
        "index": {
            "requeued_stale_document_count": result.index.requeued_stale_count,
            "candidate_document_count": result.index.candidate_count,
            "indexed_document_count": result.index.indexed_count,
            "skipped_document_count": result.index.skipped_count,
            "failed_document_count": result.index.failed_count,
            "parsed_document_count": result.index.parsed_count,
            "review_document_count": result.index.review_count,
            "cleaned_index_instance_count": result.index.cleaned_count,
            "failures": [{"error_type": key, "count": count} for key, count in _failures(result.index.failures).items()],
        },
    }
