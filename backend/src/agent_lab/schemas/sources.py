"""Source 配置列表和 KnowledgeBase 绑定请求的 HTTP 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SourceKnowledgeBaseBindingRequest(BaseModel):
    """把来源绑定到一个目标库；null 表示解除绑定。"""

    knowledge_base_id: UUID | None = Field(description="目标 KnowledgeBase ID；null 表示解除绑定。")

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class SourceResponse(BaseModel):
    """来源元数据、同步状态和当前绑定状态。"""

    id: UUID
    provider: str
    external_id: str
    name: str
    feed_url: str | None
    home_url: str | None
    knowledge_base_id: UUID | None
    knowledge_base_key: str | None
    sync_checkpoint: str | None
    sync_checkpoint_updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True, frozen=True)


class SourceErrorResponse(BaseModel):
    """来源绑定失败的稳定脱敏错误。"""

    code: str
    detail: str
    retryable: bool

    model_config = ConfigDict(frozen=True)
