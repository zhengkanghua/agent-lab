"""手动 Pipeline 的受理参数；执行状态、结果及错误由公共任务详情提供。"""

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
    未知字段，防止未支持的业务参数被静默忽略。
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
