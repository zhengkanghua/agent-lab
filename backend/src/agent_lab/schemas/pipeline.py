"""定义手动执行流水线 HTTP API 的请求、成功响应和错误响应模型。

本模块只定义「接口长什么样」，不做任何实际工作：不读取配置、不执行同步/索引。
对外暴露的统计和错误里刻意不含正文、向量、完整异常、密钥、数据库 URL 或第三方
响应结构——因为这些东西可能泄露敏感信息。

先记一组术语（后面字段描述里不再重复解释）：
- 幂等 upsert：同一篇文章反复处理也不会产生重复行（存在则更新、不存在则插入）；
- checkpoint：每个来源的同步进度标记，记录「上次同步到哪里」；
- pending/failed/processing/indexed：文档索引状态机——待处理 / 处理失败 /
  处理中 / 已索引；
- revision：正文版本号，内容变化时递增；
- stale processing：文档卡在「处理中」状态超过阈值，视为异常、可重新排队；
- 白名单分类：.env 里 FRESHRSS_SYNC_CATEGORIES 允许同步的 FreshRSS 分类。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_lab.pipeline.limits import (
    DEFAULT_INDEX_BATCH_SIZE,
    DEFAULT_LIMIT_PER_SOURCE,
    DEFAULT_STALE_AFTER_MINUTES,
    MAX_INDEX_BATCH_SIZE,
    MAX_LIMIT_PER_SOURCE,
    MAX_STALE_AFTER_MINUTES,
)


class PipelineRunOnceRequest(BaseModel):
    """一次手动执行允许调用方控制的三个「有界」参数。

    为什么全部要限范围：写操作会影响外部系统，必须让每次调用都是有限工作量，
    防止一次请求把来源数量和批次规模推爆。对象生命周期限于单个请求；模型拒绝
    未知字段，避免调用方误以为已经支持自动调度或后台执行选项。
    """

    limit_per_source: int = Field(
        default=DEFAULT_LIMIT_PER_SOURCE,
        ge=1,
        le=MAX_LIMIT_PER_SOURCE,
        description="本次每个白名单 FreshRSS 来源最多持久化的新闻数；范围 1..100。",
    )
    batch_size: int = Field(
        default=DEFAULT_INDEX_BATCH_SIZE,
        ge=1,
        le=MAX_INDEX_BATCH_SIZE,
        description="同步后本次最多处理的 pending/failed 文档数；范围 1..1000。",
    )
    stale_after_minutes: int = Field(
        default=DEFAULT_STALE_AFTER_MINUTES,
        ge=1,
        le=MAX_STALE_AFTER_MINUTES,
        description="processing 超过多少分钟后可重新排队；范围 1..10080。",
    )

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "limit_per_source": DEFAULT_LIMIT_PER_SOURCE,
                    "batch_size": DEFAULT_INDEX_BATCH_SIZE,
                    "stale_after_minutes": DEFAULT_STALE_AFTER_MINUTES,
                }
            ]
        },
    )


class PipelineFailureType(BaseModel):
    """聚合同一异常类型的安全失败数量。"""

    error_type: str = Field(
        min_length=1,
        description="Python 异常类名；不包含异常文本、正文、URL、凭据或第三方响应。",
    )
    count: int = Field(
        ge=1,
        description="本次结果中该异常类型出现的来源或文档数量。",
    )

    model_config = ConfigDict(frozen=True)


class PipelineSyncStatistics(BaseModel):
    """同步阶段（FreshRSS → PostgreSQL）的安全统计。

    只统计来源级别的成败数字，不暴露具体是哪个来源；失败只按异常类型聚合。
    """

    source_count: int = Field(
        ge=0,
        description="FreshRSS 中匹配同步分类白名单的来源数量。",
    )
    successful_source_count: int = Field(
        ge=0,
        description="本页读取、映射和事务处理成功的来源数量。",
    )
    failed_source_count: int = Field(
        ge=0,
        description="本页失败且 checkpoint 未推进的来源数量。",
    )
    synchronized_document_count: int = Field(
        ge=0,
        description="经过幂等 upsert 的新闻数，包含新增、更新和无变化命中。",
    )
    checkpoint_advanced_count: int = Field(
        ge=0,
        description="文档事务成功后实际推进 checkpoint 的来源数量。",
    )
    failures: tuple[PipelineFailureType, ...] = Field(
        default=(),
        description="按 error_type 聚合的来源失败，不包含来源正文或异常文本。",
    )


class PipelineIndexStatistics(BaseModel):
    """索引阶段（PostgreSQL 待处理文档 → Ollama → Qdrant）的安全统计。

    candidate → indexed / skipped / failed 是一轮索引批次的三种去向：正常完成、
    因状态竞争被跳过、失败被安全捕获。
    """

    requeued_stale_document_count: int = Field(
        ge=0,
        description="从超时 processing 状态重新排队的文档数量。",
    )
    candidate_document_count: int = Field(
        ge=0,
        description="本次批量上限内读取到的 pending/failed 候选数量。",
    )
    indexed_document_count: int = Field(
        ge=0,
        description="完成 Embedding、Point 写入并标记 indexed 的文档数量。",
    )
    skipped_document_count: int = Field(
        ge=0,
        description="因 revision 或状态竞争而未领取的候选数量。",
    )
    failed_document_count: int = Field(
        ge=0,
        description="单篇索引失败且已安全捕获的文档数量。",
    )
    failures: tuple[PipelineFailureType, ...] = Field(
        default=(),
        description="按 error_type 聚合的索引失败，不包含正文、Vector 或异常文本。",
    )


class PipelineRunOnceResponse(BaseModel):
    """一次手动执行完成后的类型化响应。

    ok = 同步和索引都没有「部分失败」；execution_mode 固定为 manual，明确告诉
    调用方请求结束后没有后台任务或自动调度在继续跑。
    """

    ok: bool = Field(
        description="同步来源和索引文档都没有隔离失败时为 true。",
    )
    execution_mode: Literal["manual"] = Field(
        default="manual",
        description="固定为 manual，表明请求结束后没有后台任务或自动调度。",
    )
    sync: PipelineSyncStatistics = Field(
        description="FreshRSS 与 PostgreSQL 增量同步阶段统计。",
    )
    index: PipelineIndexStatistics = Field(
        description="PostgreSQL、Ollama 与 Qdrant 索引阶段统计。",
    )


class PipelineErrorResponse(BaseModel):
    """批次级失败的稳定、脱敏 HTTP 错误响应。"""

    code: str = Field(
        min_length=1,
        description="供调用方分支处理的稳定机器错误码。",
    )
    detail: str = Field(
        min_length=1,
        description="不含原始异常、正文、Vector、密钥或连接地址的固定说明。",
    )
    error_type: str = Field(
        min_length=1,
        description="根异常 Python 类名；不包含 str(exception)，避免异常文本进响应。",
    )
    retryable: bool = Field(
        description="同样参数稍后重试是否可能成功；配置或认证错误通常为 false。",
    )


__all__ = [
    "PipelineErrorResponse",
    "PipelineFailureType",
    "PipelineIndexStatistics",
    "PipelineRunOnceRequest",
    "PipelineRunOnceResponse",
    "PipelineSyncStatistics",
]
