"""实际 Tool 结果中的证据关系；流式与回放共用校验，不另建引用档案。"""

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_lab.knowledge.scope import ResolvedKnowledgeBaseScope

# 标识由应用生成。模型只能照抄，不能靠 document_id 或外链自行制造引用。
CITATION_PATTERN = re.compile(r"\[\[(E[^\]\n]*)\]\]")

# 送给模型的那份副本里，旧标识（本次提问之前的）换成这句。刻意用单层方括号：上面的
# 正则只认 ``[[E...]]``，单层它命不中，所以这句说明不会被再剥一遍，也不会被
# ``resolve_citations`` 抽成一条 "unavailable" 的非法引用。反过来，哪天有人把
# ``CITATION_PATTERN`` 放宽到单层，这句说明会连同用户真正写下的方括号文字一起被吃掉。
STALE_CITATION_NOTICE = "[出处已失效]"


def strip_stale_citations(content: str | list[Any]) -> str | list[Any]:
    """把旧引用标识换成失效说明，供「送给模型的那一份」使用。

    checkpoint 里的消息对象与 graph state 里的是同一批，就地改 ``message.content`` 会把
    剥离后的文本写进存档，用户回看那一轮时当年的引用就点不开了。所以本函数**不改入参**
    ——包括块列表里的块对象——由调用方拿 ``model_copy`` 接住返回值。

    正文两种形状都处理：``str``，以及多模态的块列表。列表里只有文本块动（``type ==
    "text"`` 的块，以及裸字符串），图片、工具调用参数这类非文本块原样返回，不改也不复制。

    Args:
        content: 一条消息的 ``content``。

    Returns:
        替换后的新对象；没有标识时内容与入参相同，但**不是同一个对象**（``str`` 除外，
        它不可变）。
    """

    if isinstance(content, str):
        return CITATION_PATTERN.sub(STALE_CITATION_NOTICE, content)

    def strip(block: Any) -> Any:
        """一格内容：是文本块就替换，其余原样返回。"""

        if isinstance(block, str):
            return CITATION_PATTERN.sub(STALE_CITATION_NOTICE, block)
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            return {**block, "text": CITATION_PATTERN.sub(STALE_CITATION_NOTICE, block["text"])}
        return block

    return [strip(block) for block in content]


class DocumentEvidence(BaseModel):
    """本次实际读到的一段内容；hash 始终对应这段内容取得时的正文版本。"""

    citation_id: str = Field(default_factory=lambda: f"E{uuid4().hex[:12]}", pattern=r"^E[0-9a-f]{12}$")
    document_id: UUID
    knowledge_base_id: UUID
    knowledge_base_name: str
    title: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_name: str | None = None
    upload_filename: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    kind: Literal["match", "document"]

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


def validate_tool_evidence(
    artifact: Any,
    *,
    run_id: UUID,
    scope: ResolvedKnowledgeBaseScope,
) -> ToolEvidence | None:
    """按「本次运行、允许范围内、形状完整」三条把一份证据块校验回来。

    Args:
        artifact: 待校验的证据块。两条来源共用它：checkpoint 里挂在 ``ToolMessage`` 上的
            ``artifact``，以及业务表 ``agent_thread_messages.evidence`` 列读回来的那份。
        run_id: 这一轮的运行标识。
        scope: 这一轮冻结的知识库范围。

    Returns:
        校验通过的证据块；形状不对、不属于本次运行、超出允许范围、或引用项落在该次调用自己
        声明的范围之外，都返回 ``None``。

    Notes:
        纯函数，不执行 I/O。**回放的两条路径共用它**：终态事件读 checkpoint，用户回看读业务表，
        两边要给出同一批引用，校验规则各写一份就会出现「Done 说有引用、刷新后回放说没有」。
    """

    try:
        value = ToolEvidence.model_validate(artifact)
    except ValidationError:
        return None
    allowed = set(scope.knowledge_base_ids)
    if value.run_id != run_id or not set(value.scope.knowledge_base_ids) <= allowed:
        return None
    if any(item.knowledge_base_id not in value.scope.knowledge_base_ids for item in value.evidence):
        return None
    return value


def tool_evidence(message: BaseMessage, *, run_id: UUID, scope: ResolvedKnowledgeBaseScope) -> ToolEvidence | None:
    """只接受本次运行、允许范围内的实际成功 Tool 结果。旧消息没有 artifact 时没有引用。"""

    if not isinstance(message, ToolMessage) or message.status == "error" or message.artifact is None:
        return None
    return validate_tool_evidence(message.artifact, run_id=run_id, scope=scope)


def resolve_citations(answer: str, evidence: list[DocumentEvidence]) -> tuple[tuple[DocumentEvidence, ...], tuple[str, ...]]:
    """身份与范围由证据入口确定；这里只关联答案标识，不承诺判断结论的事实正确性。"""
    available = {item.citation_id: item for item in evidence}
    identifiers = dict.fromkeys(CITATION_PATTERN.findall(answer))
    return (
        tuple(available[key] for key in identifiers if key in available),
        tuple(key for key in identifiers if key not in available),
    )
