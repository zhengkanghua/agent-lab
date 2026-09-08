"""实际 Tool 结果中的证据关系；流式与回放共用校验，不另建引用档案。"""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_lab.knowledge.scope import ResolvedKnowledgeBaseScope

# 标识由应用生成。模型只能照抄，不能靠 document_id 或外链自行制造引用。
CITATION_PATTERN = re.compile(r"\[\[(E[^\]\n]*)\]\]")


class DocumentEvidence(BaseModel):
    """本次实际读到的一段内容；hash 始终对应这段内容取得时的正文版本。"""

    citation_id: str = Field(default_factory=lambda: f"E{uuid4().hex[:12]}", pattern=r"^E[0-9a-f]{12}$")
    document_id: UUID
    knowledge_base_id: UUID
    knowledge_base_name: str
    title: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    excerpt: str = Field(min_length=1, repr=False)
    source_name: str | None = None
    upload_filename: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    kind: Literal["match", "document"]
    truncated: bool = False

    # 序列化响应始终包含应用生成的 citation_id，OpenAPI 不能把它描述成可省略的输入默认值。
    model_config = ConfigDict(frozen=True, json_schema_serialization_defaults_required=True)


class ToolEvidence(BaseModel):
    """随 ToolMessage artifact 保存，随消息压缩退出可回放状态。"""

    run_id: UUID
    scope: ResolvedKnowledgeBaseScope
    evidence: tuple[DocumentEvidence, ...] = ()

    model_config = ConfigDict(frozen=True)


def is_complete_answer(message: BaseMessage | None) -> bool:
    """上游明确截断或过滤时保留文本，但不能把半句回放成完整答案。"""
    if not isinstance(message, AIMessage) or message.tool_calls or not message.text.strip():
        return False
    reasons = {message.response_metadata.get(key) for key in ("finish_reason", "stop_reason", "done_reason")}
    return not reasons.intersection({"length", "max_tokens", "content_filter", "error"})


def tool_evidence(message: BaseMessage, *, run_id: UUID, scope: ResolvedKnowledgeBaseScope) -> ToolEvidence | None:
    """只接受本次运行、允许范围内的实际成功 Tool 结果。旧消息没有 artifact 时没有引用。"""
    if not isinstance(message, ToolMessage) or message.status == "error" or message.artifact is None:
        return None
    try:
        value = ToolEvidence.model_validate(message.artifact)
    except ValidationError:
        return None
    allowed = set(scope.knowledge_base_ids)
    if value.run_id != run_id or not set(value.scope.knowledge_base_ids) <= allowed:
        return None
    if any(item.knowledge_base_id not in value.scope.knowledge_base_ids for item in value.evidence):
        return None
    return value


def resolve_citations(answer: str, evidence: list[DocumentEvidence]) -> tuple[tuple[DocumentEvidence, ...], tuple[str, ...]]:
    """身份与范围由证据入口确定；这里只关联答案标识，不承诺判断结论的事实正确性。"""
    available = {item.citation_id: item for item in evidence}
    identifiers = dict.fromkeys(CITATION_PATTERN.findall(answer))
    return (
        tuple(available[key] for key in identifiers if key in available),
        tuple(key for key in identifiers if key not in available),
    )
