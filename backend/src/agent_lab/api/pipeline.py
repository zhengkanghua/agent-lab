"""提供「有界、同步、手动」的写入口：POST /pipeline/run-once。

本模块位于 FastAPI 边界层，职责很窄：校验请求参数 → 按请求新建一个写 Runtime →
同步等待「FreshRSS 同步 + 向量索引」一轮跑完 → 返回脱敏统计。
三个关键词的含义：
- 有界（bounded）：每次只处理有限数量（limit_per_source / batch_size），绝不无限循环；
- 同步：请求一直等到这一轮结束才返回，不创建后台任务；
- 手动：只有显式调用这个接口才会执行，应用启动时什么都不做。

它不创建 asyncio 后台 Task、队列、Scheduler、Worker 或 WebSocket，也不复用只读的
Vector Search Runtime（写和读是两个独立的 Runtime，权限分开）。

写 Runtime 工厂的取用在 ``agent_lab.api.dependencies``，批次级异常到 HTTP 错误的映射表
在 ``agent_lab.api.error_contract``；本模块只负责编排一轮执行和聚合脱敏统计。
"""

import logging
from collections import Counter
from collections.abc import Iterable
from datetime import timedelta

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from agent_lab.api.dependencies import (
    PipelineWriteRuntimeFactory,
    PipelineWriteRuntimeUnavailableError,
    get_pipeline_write_runtime_factory,
)
from agent_lab.api.error_contract import build_pipeline_error_response
from agent_lab.pipeline.write_runtime import (
    PipelineRunOnceExecutionResult,
    PipelineWriteRuntime,
)
from agent_lab.schemas.pipeline import (
    PipelineErrorResponse,
    PipelineFailureType,
    PipelineIndexStatistics,
    PipelineRunOnceRequest,
    PipelineRunOnceResponse,
    PipelineSyncStatistics,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.post(
    "/run-once",
    response_model=PipelineRunOnceResponse,
    status_code=status.HTTP_200_OK,
    summary="手动执行一次新闻增量同步与向量索引",
    description=(
        "在当前 HTTP 请求内依次执行 FreshRSS 增量同步和一个待索引批次。"
        "这是有外部写入副作用的同步手动操作，不会创建后台任务；来源级或单篇失败"
        "仍返回 200，并通过 ok=false、失败数量和 error_type 明确报告。"
    ),
    response_description="本轮同步与索引完成后的脱敏统计。",
    responses={
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": PipelineErrorResponse,
            "description": "未分类的内部执行或资源关闭错误。",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "model": PipelineErrorResponse,
            "description": "FreshRSS、Ollama 或 Qdrant 返回不可接受的上游响应。",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": PipelineErrorResponse,
            "description": "配置、PostgreSQL 或写入上游当前不可用。",
        },
        status.HTTP_504_GATEWAY_TIMEOUT: {
            "model": PipelineErrorResponse,
            "description": "FreshRSS、Ollama 或整轮流水线操作超时。",
        },
    },
)
async def run_pipeline_once(
    body: PipelineRunOnceRequest,
    request: Request,
) -> PipelineRunOnceResponse | JSONResponse:
    """在当前 HTTP 请求里同步跑完一轮「同步 + 索引」，然后把结果返回给客户端。

    执行骨架（try/finally 保证 Runtime 一定被关闭）：
    1. 从应用 state 取出写 Runtime 工厂，新建一个本次请求专用的 Runtime；
    2. runtime.run_once(...) 依次做 FreshRSS 增量同步 → 文档入库 → Chunk/Embedding →
       Qdrant 写入；
    3. finally 里无论成败都关闭 Runtime，关闭失败也要记日志且不能掩盖主异常。

    注意两个「失败语义」的区别：
    - 来源级/单篇级失败：属于业务范围内的部分失败，返回 HTTP 200，用 ok=false、
      失败数量、error_type 明确报告；
    - 批次级失败（配置错、上游整体不可用等）：返回脱敏的 5xx。

    Args:
        body: 已完成默认值和最小/最大值校验的手动执行参数。
        request: 当前 FastAPI 请求，用于取得 composition root 注册的写 Runtime 工厂；
            本方法不读取或记录请求原始 body。

    Returns:
        完成时返回同步和索引安全统计；批次级失败返回类型化、脱敏 5xx JSON。

    Notes:
        本方法会执行 FreshRSS/PostgreSQL/Ollama/Qdrant 写入 I/O，并等待它们结束后才
        响应。Runtime 按请求创建并始终关闭；不会向 ``VectorSearchRuntime`` 请求写入
        组件，也不会留下后台任务。来源级和单篇索引失败返回 HTTP 200 且 ``ok=false``。
    """

    runtime: PipelineWriteRuntime | None = None
    operation_error: Exception | None = None
    result: PipelineRunOnceExecutionResult | None = None
    try:
        # 1、从应用 state 取出工厂，新建「本次请求专用」的写 Runtime
        runtime_factory: PipelineWriteRuntimeFactory = (
            get_pipeline_write_runtime_factory(request)
        )
        runtime = runtime_factory()
        # 2、同步跑完一轮：FreshRSS 同步 → 入库 → 索引（切分/向量化/写 Qdrant）
        result = await runtime.run_once(
            limit_per_source=body.limit_per_source,
            batch_size=body.batch_size,
            stale_after=timedelta(minutes=body.stale_after_minutes),
        )
    except Exception as exc:
        # 3、批次级异常先记下，统一在最后映射成脱敏 5xx
        operation_error = exc
    finally:
        # 4、无论成败都关闭 Runtime；关闭失败也要记日志，且不能掩盖主异常
        if runtime is not None:
            try:
                await runtime.close()
            except Exception as close_error:
                logger.error(
                    "流水线写入运行时关闭失败 error_type=%s",
                    type(close_error).__name__,
                )
                if operation_error is None:
                    operation_error = close_error

    if operation_error is not None:
        logger.error(
            "手动流水线请求失败 error_type=%s",
            type(operation_error).__name__,
        )
        return build_pipeline_error_response(operation_error)
    if result is None:
        # 该分支只保护未来重构时遗漏赋值，响应仍不暴露内部实现细节。
        return build_pipeline_error_response(
            RuntimeError("pipeline execution returned no result")
        )
    return _build_success_response(result)


def _build_success_response(
    result: PipelineRunOnceExecutionResult,
) -> PipelineRunOnceResponse:
    """把内部结果对象转换成对外 HTTP 模型（脱敏 + 聚合统计）。

    内部 PipelineRunOnceExecutionResult 里可能带文档 UUID、具体错误对象等细节；
    对外只暴露统计数字和按 error_type 聚合的失败列表，绝不含文档身份或异常文本。
    另外把同步/索引的失败合并成一个 ok 标志，方便客户端一眼判断整体成败。

    Args:
        result: 写 Runtime 返回的同步与索引结果。

    Returns:
        可由 FastAPI 序列化的安全响应模型。
    """

    sync_failure_types = _summarize_error_types(
        failure.error_type for failure in result.sync.failures
    )
    index_failure_types = _summarize_error_types(
        failure.error_type for failure in result.index.failures
    )
    return PipelineRunOnceResponse(
        ok=not sync_failure_types and not index_failure_types,
        sync=PipelineSyncStatistics(
            source_count=result.sync.source_count,
            successful_source_count=result.sync.successful_source_count,
            failed_source_count=result.sync.failed_source_count,
            synchronized_document_count=result.sync.synchronized_count,
            checkpoint_advanced_count=result.sync.checkpoint_advanced_count,
            failures=sync_failure_types,
        ),
        index=PipelineIndexStatistics(
            requeued_stale_document_count=result.index.requeued_stale_count,
            candidate_document_count=result.index.candidate_count,
            indexed_document_count=result.index.indexed_count,
            skipped_document_count=result.index.skipped_count,
            failed_document_count=result.index.failed_count,
            failures=index_failure_types,
        ),
    )


def _summarize_error_types(error_types: Iterable[str]) -> tuple[PipelineFailureType, ...]:
    """把一串失败异常类型聚合成「类型 → 数量」的统计。

    例如 ["OllamaTimeoutError", "OllamaTimeoutError", "FreshRSSConnectionError"]
    会变成 [(error_type="FreshRSSConnectionError", count=1),
            (error_type="OllamaTimeoutError", count=2)]。
    只暴露异常类名而不是来源/文档身份，是为了不泄露具体是哪个来源或哪篇文章失败；
    按字典序排序保证同一结果每次返回顺序一致（稳定输出）。

    Args:
        error_types: 来源级或文档级失败的异常类名迭代器。

    Returns:
        按类名字典序排列的 ``error_type/count`` 元组。
    """

    counts = Counter(error_types)
    return tuple(
        PipelineFailureType(error_type=error_type, count=counts[error_type])
        for error_type in sorted(counts)
    )


# 前两个名字的定义已分别移到 dependencies 与 error_contract；这里继续导出，是为了让
# 既有调用方（含测试）无需关心它们搬到了哪个模块。
__all__ = [
    "PipelineWriteRuntimeUnavailableError",
    "build_pipeline_error_response",
    "router",
]
