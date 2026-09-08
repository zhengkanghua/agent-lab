"""用户选择与已解析范围分别建模；空集合不能退化为全库查询。"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KnowledgeBaseSelection(BaseModel):
    """所有启用库，或明确选择的非空知识库列表。"""

    mode: Literal["all", "selected"]
    knowledge_base_ids: tuple[UUID, ...] = Field(default_factory=tuple)

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @model_validator(mode="after")
    def validate_selection(self) -> "KnowledgeBaseSelection":
        if self.mode == "all" and self.knowledge_base_ids:
            raise ValueError("所有知识库模式不能同时指定知识库列表")
        if self.mode == "selected" and not self.knowledge_base_ids:
            raise ValueError("至少选择一个知识库")
        return self


class KnowledgeBaseSummary(BaseModel):
    """查询时的知识库展示快照，改名后不改写历史记录。"""

    id: UUID
    key: str
    name: str
    description: str | None = None

    model_config = ConfigDict(from_attributes=True, frozen=True)


class ResolvedKnowledgeBaseScope(BaseModel):
    """应用已校验的本次范围，供过滤、展示与运行上下文复用。"""

    mode: Literal["all", "selected"]
    knowledge_bases: tuple[KnowledgeBaseSummary, ...] = Field(min_length=1)

    model_config = ConfigDict(frozen=True)

    @property
    def knowledge_base_ids(self) -> tuple[UUID, ...]:
        return tuple(item.id for item in self.knowledge_bases)


def validate_scope_compatibility(
    scope: KnowledgeBaseSelection | None,
    *legacy_ids: UUID | None,
    filter_ids: tuple[UUID, ...] | None = None,
) -> None:
    """新旧范围并存时必须表达同一个集合，不能隐式扩大或收窄。"""

    expected = set(filter_ids) if filter_ids is not None else None
    for identifier in legacy_ids:
        if identifier is not None:
            if expected is not None and expected != {identifier}:
                raise ValueError("知识库范围必须一致")
            expected = {identifier}
    if scope is not None and expected is not None:
        if scope.mode != "selected" or set(scope.knowledge_base_ids) != expected:
            raise ValueError("新旧知识库范围必须一致")


def require_explicit_scope(value: KnowledgeBaseSelection | None) -> KnowledgeBaseSelection:
    if value is None:
        raise ValueError("scope 不能为 null")
    return value
