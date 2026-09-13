"""任务管理公共 HTTP 契约；前端从 OpenAPI 生成状态与快照类型。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from agent_lab.tasks.contracts import ExecutionPolicy, RunStatus


class TaskAcceptedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)
    run_id: UUID
    job_id: UUID | None = None
    status: RunStatus | Literal["expired"]
    details_expired: bool = Field(default=False, description="原执行详情已过期，编号仍有效；此次没有新建执行。")


class TaskSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class JobRunResponse(BaseModel):
    """一次受理的管理详情，不包含上游异常文本、连接或凭据。"""

    model_config = ConfigDict(from_attributes=True, frozen=True)
    id: UUID
    job_id: UUID | None
    source_job_id: UUID | None
    task_type: str
    task_version: int
    trigger_type: str
    actor: str
    status: RunStatus
    accepted_at: datetime
    scheduled_for: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    available_at: datetime
    expires_at: datetime | None
    attempts: int
    delivery_count: int
    last_dispatched_at: datetime | None
    dispatch_error_type: str | None
    heartbeat_at: datetime | None
    owner: str | None
    wait_reason: str | None
    error_type: str | None
    stats: dict[str, Any]
    config_snapshot: dict[str, Any]
    policy_snapshot: ExecutionPolicy
    recovery: dict[str, Any]
    retry_of: UUID | None

    @computed_field
    @property
    def needs_attention(self) -> bool:
        return self.status == "needs_attention"

    @computed_field
    @property
    def can_cancel(self) -> bool:
        return self.status in {"queued", "waiting_resource", "retry_wait"}

    @computed_field
    @property
    def can_retry(self) -> bool:
        return self.status == "failed" and isinstance(self.config_snapshot.get("params"), dict)


class TaskPolicyChangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    actor: str
    changed_at: datetime
    previous: ExecutionPolicy
    current: ExecutionPolicy
