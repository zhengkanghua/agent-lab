"""定时任务管理 API 的请求与响应模型。

字段 ``description`` 会进入 ``/openapi.json`` 并由前端 ``openapi-typescript`` 生成
类型，写的是「这个字段是什么」，供前端开发直接阅读；实现细节写在模块与类 docstring
（路由装饰器的 ``description=`` 优先于 handler docstring，见 backend/AGENTS.md）。
所有时刻字段一律 UTC ISO8601，展示时区的换算由前端负责。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CronValidateRequest(BaseModel):
    """cron 预览请求：只带一个待校验的表达式。"""

    cron_expr: str = Field(
        min_length=1,
        max_length=64,
        description="5 段式 cron 表达式（分 时 日 月 周），例如 */10 * * * *。",
    )


class CronValidateResponse(BaseModel):
    """cron 预览结果：校验通过时给出的未来执行时间。"""

    next_run_times: list[datetime] = Field(
        description="未来 3 次执行时间（UTC ISO8601），供前端换算展示。",
    )
    next_run_times_local: list[str] = Field(
        description=(
            "未来 3 次执行时间在服务端解释时区（SCHEDULER_TIMEZONE，默认上海）下的"
            " ISO8601 字符串，前端可直接展示或自行换算。"
        ),
    )


class ScheduledJobCreateRequest(BaseModel):
    """创建定时任务的请求体。"""

    key: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        description="业务唯一键：小写字母数字与短横线组成，创建后不可修改。",
    )
    task_type: str = Field(
        min_length=1,
        max_length=64,
        description="任务类型，可选取值见 GET /scheduled-jobs 返回的 task_types（目前为 freshrss_sync、index_pending）。",
    )
    cron_expr: str = Field(
        min_length=1,
        max_length=64,
        description="5 段式 cron 表达式；按服务端解释时区（默认上海）理解，存储与执行均为 UTC。",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "任务参数（JSON 对象），形状随任务类型：freshrss_sync 为 {limit_per_source}，"
            "index_pending 为 {batch_size, stale_after_minutes}；缺省字段用默认值。"
        ),
    )
    enabled: bool = Field(
        default=True,
        description="创建后是否立即参与 cron 调度。",
    )


class ScheduledJobUpdateRequest(BaseModel):
    """修改定时任务的请求体；未提供的字段保持不变。key 与任务类型不可修改。"""

    cron_expr: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="新的 5 段式 cron 表达式；不传表示不修改。",
    )
    params: dict[str, Any] | None = Field(
        default=None,
        description=(
            "新的任务参数（整体替换并重新校验）；不传（null）表示不修改，"
            "传空对象表示清空为该类型默认值。"
        ),
    )
    enabled: bool | None = Field(
        default=None,
        description="是否参与 cron 调度；不传表示不修改。",
    )


class JobRunResponse(BaseModel):
    """一条任务执行记录：只含脱敏统计，不含正文、身份或异常文本。"""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID = Field(description="执行记录 id。")
    job_id: UUID = Field(description="所属定时任务 id。")
    trigger_type: str = Field(description="触发方式：scheduled（cron 到点）或 manual（手动触发）。")
    status: str = Field(description="执行状态：running、succeeded、failed 或 skipped。")
    started_at: datetime = Field(description="开始（或跳过判定发生）时刻，UTC。")
    finished_at: datetime | None = Field(
        description="结束时刻，UTC；running 与 skipped 状态下为空。",
    )
    stats: dict[str, Any] = Field(
        description=(
            "脱敏统计：数量与按异常类型的聚合计数；skipped 记录含 reason 字段，"
            "批次级失败的记录含 error_reason（稳定失败原因枚举）。"
        ),
    )
    error_type: str | None = Field(
        description="批次级失败的异常类名（只含类型名，无异常文本）；成功与跳过时为空。",
    )


class ScheduledJobResponse(BaseModel):
    """一条定时任务的完整视图：配置 + 调度状态 + 最近一次执行摘要。"""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(description="定时任务 id。")
    key: str = Field(description="业务唯一键，创建后不可修改。")
    task_type: str = Field(description="任务类型（freshrss_sync 或 index_pending）。")
    cron_expr: str = Field(description="5 段式 cron 表达式原样字符串。")
    params: dict[str, Any] = Field(description="任务参数（已按类型规范化的 JSON 对象）。")
    enabled: bool = Field(description="是否参与 cron 调度。")
    next_run_at: datetime | None = Field(
        description="下次计划执行时间（UTC）；调度器未启动（SCHEDULER_ENABLED=false）或任务停用/未注册时为空。",
    )
    last_run: JobRunResponse | None = Field(
        description="最近一次执行记录；尚无历史时为空。",
    )
    created_at: datetime = Field(description="创建时间，UTC。")
    updated_at: datetime = Field(description="最近一次配置修改时间，UTC。")


class ScheduledJobTriggerResponse(BaseModel):
    """手动触发的受理回执：执行已在后台开始，结果通过执行历史查询。"""

    model_config = ConfigDict(frozen=True)

    job_id: UUID = Field(description="被触发的定时任务 id。")
    run_id: UUID = Field(description="新创建的执行记录 id，可凭它到执行历史里跟踪。")
    status: str = Field(description="受理时的执行状态，固定为 running。")


class ScheduledJobErrorResponse(BaseModel):
    """定时任务管理 API 的稳定、脱敏错误结构。"""

    code: str = Field(description="供前端稳定识别的错误代码。")
    detail: str = Field(description="不含异常文本、凭据或内部路径的安全说明。")
    retryable: bool = Field(description="相同请求稍后重试是否可能成功。")


__all__ = [
    "CronValidateRequest",
    "CronValidateResponse",
    "JobRunResponse",
    "ScheduledJobCreateRequest",
    "ScheduledJobErrorResponse",
    "ScheduledJobResponse",
    "ScheduledJobTriggerResponse",
    "ScheduledJobUpdateRequest",
]
