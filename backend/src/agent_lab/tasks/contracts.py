"""周期与一次性执行共用的状态、策略和安全错误契约。"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type RunStatus = Literal[
    "queued", "waiting_resource", "running", "retry_wait", "needs_attention",
    "succeeded", "failed", "cancelled", "skipped",
]
ACTIVE_STATUSES = ("queued", "waiting_resource", "running", "retry_wait", "needs_attention")
DELIVERABLE_STATUSES = ("queued", "waiting_resource", "retry_wait")
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "skipped")


class ExecutionPolicy(BaseModel):
    """每次受理冻结一份；之后修改默认值不改变已有执行。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    max_retries: int = Field(default=3, ge=0, le=20, description="初次尝试之外允许的自动重试次数。")
    retry_delay_seconds: int = Field(default=30, ge=1, le=86400, description="第一次自动重试的间隔秒数，之后逐次翻倍，最长一天。")
    history_retention_days: int = Field(default=30, ge=1, le=36500, description="普通已结束执行从结束时间起保留的天数。")

    def delay(self, attempts: int) -> int:
        return min(self.retry_delay_seconds * 2 ** max(0, attempts - 1), 86400)


class TaskError(Exception):
    """只携带预写中文说明；不将上游异常文本暴露给 HTTP。"""

    code = "task_operation_conflict"
    detail = "当前任务执行状态不允许此操作。"
    status_code = 409

    def __init__(self, *, run_id: UUID | None = None):
        super().__init__(self.code)
        self.run_id = run_id


class TaskNotFound(TaskError):
    code = "task_run_not_found"
    detail = "任务执行不存在。"
    status_code = 404


class TaskDetailsExpired(TaskError):
    code = "task_run_expired"
    detail = "任务执行详情已过保留期，原执行编号仍保留。"
    status_code = 410


class RequestConflict(TaskError):
    code = "task_request_conflict"
    detail = "请求标识已用于不同内容，请核对原操作。"


class TaskOverlap(TaskError):
    code = "scheduled_job_already_running"
    detail = "任务正在执行中，请等待本轮结束后再触发。"


class RetryUnavailable(TaskError):
    code = "task_retry_unavailable"
    detail = "只有保留完整参数且已确认结束的失败执行可以重试。"


class ResourceWait(Exception):
    """业务准备阶段资源忙；只延后检查，不计入业务尝试。"""

    def __init__(self, reason: str, *, check_after_seconds: float = 5):
        super().__init__(reason)
        self.reason = reason
        self.check_after_seconds = check_after_seconds


@dataclass(frozen=True)
class FailureDecision:
    """业务按已知结果分类，公共核心不猜测上游写入是否安全。"""

    retryable: bool = False
    needs_attention: bool = False
    reason: str = "task_failed"
    detail: str = "任务执行失败，请查看错误类型后决定是否重试。"
    stats: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RecoveryDecision:
    """仅业务有持久证据时提供恢复结论；缺省保留旧执行保护。"""

    action: Literal["verify", "retry", "succeeded"] = "verify"
    reason: str = "执行者或远端写入状态尚未确认，请核实旧进程与远端请求后使用维护入口。"
    stats: dict = field(default_factory=dict)
