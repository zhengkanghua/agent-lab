"""可跨调用入口传递的知识库配置 DTO。"""

from datetime import datetime
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


KnowledgeBaseKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
    ),
]
KnowledgeBaseName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
KnowledgeBaseDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2000)
]


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库；稳定键创建后不可修改，名称和描述可单独维护。"""

    key: KnowledgeBaseKey = Field(description="稳定业务键，只使用小写字母、数字和连字符，创建后不可修改。")
    name: KnowledgeBaseName = Field(description="知识库展示名称。")
    description: KnowledgeBaseDescription | None = Field(default=None, description="可选说明。")
    is_active: bool = Field(default=True, strict=True, description="知识库是否启用。")

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class KnowledgeBaseUpdateRequest(BaseModel):
    """仅修改明确提交的配置；description 为 null 时清空说明。"""

    name: KnowledgeBaseName | None = Field(default=None, description="新的展示名称。")
    description: KnowledgeBaseDescription | None = Field(default=None, description="新的说明；null 表示清空。")
    is_active: bool | None = Field(default=None, strict=True, description="是否启用。")

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @model_validator(mode="after")
    def require_changes(self) -> "KnowledgeBaseUpdateRequest":
        """区分未提交、明确清空和不允许的 null，防止空更新静默成功。"""

        if not self.model_fields_set:
            raise ValueError("至少提交一个可修改字段")
        for field in ("name", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError("名称和启用状态不能为 null")
        return self


class KnowledgeBaseResponse(BaseModel):
    """知识库配置的公开视图，不包含数据库内部对象。"""

    id: UUID = Field(description="KnowledgeBase 的全局 UUID。")
    key: str = Field(description="不可修改的稳定业务键。")
    name: str = Field(description="知识库展示名称。")
    description: str | None = Field(description="可选说明。")
    is_active: bool = Field(description="知识库是否启用。")
    created_at: datetime = Field(description="创建时间。")
    updated_at: datetime = Field(description="最后一次实际修改时间。")

    model_config = ConfigDict(from_attributes=True, frozen=True)


@dataclass(frozen=True, slots=True)
class SourceView:
    """可跨入口传递的 Source 配置和同步状态快照，不保留 ORM 对象。"""

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
