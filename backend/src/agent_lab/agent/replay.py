"""把 checkpointer 存下的消息列表翻成「一轮一问一答」的回合结构。

本模块只做纯内存翻译：输入是一串 LangChain 消息，输出是 ``schemas.agent_thread`` 的回放模型。
它不读数据库、不调 checkpointer、不校验归属——取状态在消费方（``agent/streaming.py`` 的终态事件、
``agent/runs.py`` 的接手重建、写入侧收尾）。

**谁还从 checkpointer 读**：终态事件（一次运行结束时把最终答案、完成状态与引用发给前端）与接手
时重建「本轮冻结的范围与模型」。两件事都发生在收尾前后，那时这一轮可能还没写进业务表。**用户回看**
不再走这里：它读 ``agent_thread_messages``（见 ADR 0043 与 ADR 0044），组装在
``agent/replay_rows.py``。两条路径共用本模块与 ``agent/evidence.py`` 里的纯函数，口径才不分叉。

**摘要那条消息长什么样**（langchain 1.3.15 实测，见本模块的测试）：压缩动作是
``RemoveMessage(id=REMOVE_ALL_MESSAGES)`` 清空整个列表、再重建，摘要被包成一条 **HumanMessage**，
带 ``additional_kwargs={"lc_source": "summarization"}``，正文前面还有一句英文
``Here is a summary of the conversation to date:``。它长得和用户提问一模一样，只有那个
``lc_source`` 标记能区分——不认出来的话，用户会在自己的对话记录里看到一句从没问过的「提问」。
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from agent_lab.schemas.agent_thread import AgentReplayTrace, AgentReplayTurn
from agent_lab.agent.evidence import is_complete_answer, resolve_citations, tool_evidence
from agent_lab.knowledge.scope import ResolvedKnowledgeBaseScope
from agent_lab.schemas.llm_models import ResolvedLlmModel


logger = logging.getLogger(__name__)

# SummarizationMiddleware 给摘要消息打的来源标记。这是上游的内部约定，不是公开契约，所以有一条
# 测试拿真实中间件跑一次压缩来钉住它：上游改了形态，那条测试会失败，而不是让英文摘要静默地
# 出现在用户的对话记录里冒充提问。
_SUMMARY_SOURCE_MARKER = "summarization"

# 一轮对外的完成态。两个字面量就是 ``schemas.agent_thread.AgentReplayTurn.status`` 的取值域。
ReplayTurnStatus = Literal["completed", "incomplete"]


def _is_summary_message(message: BaseMessage) -> bool:
    """判断一条消息是不是历史压缩产生的摘要。

    Args:
        message: checkpointer 状态里的一条消息。

    Returns:
        ``True`` 表示这是摘要伪提问，不该当成用户的一轮提问。

    Notes:
        纯判断，不执行 I/O。只认 ``additional_kwargs`` 里的来源标记，不去匹配正文前缀——
        前缀是上游的英文字面量，改了不会报错，只会让判断静默失效。
    """

    if not isinstance(message, HumanMessage):
        return False
    extra = getattr(message, "additional_kwargs", None) or {}
    return extra.get("lc_source") == _SUMMARY_SOURCE_MARKER


def summary_messages(messages: Sequence[BaseMessage]) -> tuple[BaseMessage, ...]:
    """取出会话快照里的摘要消息（压缩留下的伪提问），按它们在快照里的先后。

    写入侧要用它：``group_messages_by_run`` 会把摘要跳过（它不属于某一次对话），而摘要行承载
    压缩分界标记、必须落表（见 ``agent/thread_messages``）。这里只负责把摘要**筛出来**，不判断
    它属于哪一次运行——那个判据在写入侧，按 ``produced_by_run_id`` 与组绑定。

    Args:
        messages: ``graph.aget_state`` 给出的 ``messages`` 列表，按时间正序。

    Returns:
        摘要消息（压缩伪提问）的元组；没有被压缩过时是空元组。

    Notes:
        纯内存转换，不执行 I/O。读取路径不受影响：回放仍然不看摘要消息的内容。
    """

    return tuple(message for message in messages if _is_summary_message(message))


def text_of(message: BaseMessage) -> str:
    """取一条消息里可显示的纯文本。

    Args:
        message: 任意 LangChain 消息。

    Returns:
        文本内容；多模态或工具调用块混排时只保留 ``type == "text"`` 的部分。

    Notes:
        纯内存转换。与 ``agent/streaming.py`` 的 ``_token_event`` 同一套过滤逻辑，理由也一样：
        ``content`` 在工具调用阶段可能是含 ``tool_use`` 块的列表，原样拼出来会让用户看到半截 JSON。

        公开（不带下划线）是因为会话历史的写入方也用它：投影提问行与回答行的正文时，两边得是
        同一份文本，否则表里存的与回放显示的不是一回事。
    """

    content = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence):
        return ""
    return "".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _tool_result_index(messages: Iterable[BaseMessage]) -> dict[str, ToolMessage]:
    """把全部工具结果按 ``tool_call_id`` 建索引。

    Args:
        messages: 一个会话的全部消息。

    Returns:
        ``tool_call_id`` 到对应 ``ToolMessage`` 的映射。

    Notes:
        纯内存转换。先建全局索引再回填，而不是边扫边配：工具结果紧跟在调用之后是常见情形但不是
        保证，多个工具并发调用时结果的到达顺序可以任意。按 id 查表不依赖顺序。
    """

    index: dict[str, ToolMessage] = {}
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id:
            index[message.tool_call_id] = message
    return index


@dataclass(frozen=True, slots=True)
class RunMessageGroup:
    """一次运行对应的那一组原始消息。

    这份分组是「哪条消息属于哪一次运行」的唯一真源：回放靠它把消息切成一问一答，把会话历史写进
    业务表的写入方也靠它逐条落行。两边共用一份，分轮口径就不会分叉——分叉的后果是界面上的轮次
    与表里的行各说一套。
    """

    # 该组的运行标识，取自提问消息上的运行元数据。老会话的提问没盖元数据时是 ``None``——
    # 消费方按「运行未知」处理，这里不编一个占位 id，否则两次不同的运行会被并成一组。
    run_id: UUID | None

    # 该组按时间正序的全部原始消息，**这次运行的提问消息本身在第一位**。给的是消息对象本身而不是
    # 抽取后的字段：写入方要的 role、顺序号、正文与证据都长在这些原始对象上。
    messages: tuple[BaseMessage, ...]


def group_messages_by_run(messages: Sequence[BaseMessage]) -> tuple[RunMessageGroup, ...]:
    """把一串会话消息按「运行」切组，每组给出它的运行标识与该组的原始消息。

    Args:
        messages: ``graph.aget_state`` 给出的 ``messages`` 列表，按时间正序。

    Returns:
        按时间正序排列的组；每一组的首条消息是这次运行的提问。

    Notes:
        纯内存转换，不执行任何 I/O。

        切分规则：一条**不是摘要**的 HumanMessage 开一个新组，之后的消息都归入这一组，直到下一条
        提问。摘要消息不属于任何一组，直接跳过——它承载的是压缩边界，不是某一次运行的对话。

        出现在第一条提问之前的 AIMessage 会被丢弃并记一条 debug 日志。正常链路不该有这种消息，
        真出现了大概是上游改了状态结构——丢掉比凭空造一轮空提问好，后者会让用户以为自己问过什么。
    """

    groups: list[RunMessageGroup] = []
    # 正在攒的那一组；``None`` 表示还没遇到第一条提问。
    current: list[BaseMessage] | None = None
    current_run_id: UUID | None = None

    for message in messages:
        # 1、摘要那条：它是 HumanMessage，但不是用户问的。跳过，不归入任何一组。
        if _is_summary_message(message):
            continue

        # 2、真正的用户提问：收掉上一组，开新一组，提问消息自己进组。
        if isinstance(message, HumanMessage):
            if current is not None:
                groups.append(RunMessageGroup(run_id=current_run_id, messages=tuple(current)))
            current = [message]
            run_meta = message.additional_kwargs.get("agent_run") or {}
            current_run_id = UUID(run_meta["run_id"]) if run_meta.get("run_id") else None
            continue

        # 3、其余消息归入当前组。首条提问之前的那些没有归属，丢掉。
        if isinstance(message, AIMessage) and current is None:
            logger.debug(
                "按运行分组时丢弃出现在首条提问之前的模型消息 message_type=%s",
                type(message).__name__,
            )
            continue
        if current is not None:
            current.append(message)

    if current is not None:
        groups.append(RunMessageGroup(run_id=current_run_id, messages=tuple(current)))
    return tuple(groups)


def build_replay_turns(
    messages: Sequence[BaseMessage],
) -> tuple[tuple[AgentReplayTurn, ...], bool, str | None]:
    """把一个会话的消息列表翻成按时间排列的轮次。

    Args:
        messages: ``graph.aget_state`` 给出的 ``messages`` 列表，按时间正序。

    Returns:
        ``(轮次, 是否被压缩过, 摘要正文)``。没被压缩时后两项是 ``(False, None)``。

    Notes:
        纯内存转换，不执行任何 I/O。

        分轮交给 ``group_messages_by_run``（写入方也用它），这里只把每一组定型成对外的一轮：
        提问之后的 AIMessage 文本累加进 ``answer``、工具调用累加进 ``traces``。

        系统提示词不会出现在这里：它由 ``resolve_system_prompt`` 每次动态注入到模型请求里，
        不进 checkpointer 的消息历史（见 ``agent/middleware.py``）。

        「是否被压缩过」与摘要正文还要从这里返回，而摘要不属于任何一组，所以单独扫一遍取它们。
    """

    summarized = False
    summary: str | None = None
    for message in messages:
        if _is_summary_message(message):
            summarized = True
            summary = text_of(message)

    turns = tuple(_build_turn(group) for group in group_messages_by_run(messages))
    return turns, summarized, summary


def turn_status(
    *,
    completed: bool,
    traces: Sequence[AgentReplayTrace],
) -> ReplayTurnStatus:
    """一轮的完成态：被判定为完整作答，而且每条工具轨迹都拿到了结果。

    Args:
        completed: 这一轮是否完整作答。两条读取路径各给各的**事实**：读 checkpoint 那条现算
            （``is_complete_answer`` 加上当轮冻结的完成标记），读业务表那条读写入时冻结在提问
            行 ``run_meta.completed`` 上的同一份判定结果。
        traces: 这一轮的工具轨迹。

    Returns:
        ``"completed"`` 或 ``"incomplete"``。

    Notes:
        纯函数，不执行 I/O。判定规则放在这里共用，是因为两条路径必须给出同一个状态：终态事件说
        「答完了」、刷新后的回放说「没答完」，用户会以为这一轮丢了。
    """

    if completed and all(trace.content is not None for trace in traces):
        return "completed"
    return "incomplete"


def _build_turn(group: RunMessageGroup) -> AgentReplayTurn:
    """把一组消息定型成对外的一轮。"""

    question = group.messages[0]
    run_id = group.run_id
    # 提问消息同时是这一轮运行元数据的载体：当时的知识库范围与当轮选定的模型都从它的
    # ``agent_run`` 里取。两者都是**当时**的快照——条目后来改名或停用不改写已经发生过的那几轮。
    meta = question.additional_kwargs.get("agent_run") or {}
    scope = ResolvedKnowledgeBaseScope.model_validate(meta["scope"]) if meta.get("scope") else None
    llm_model = ResolvedLlmModel.model_validate(meta["llm_model"]) if meta.get("llm_model") else None
    messages = group.messages[1:]

    # Tool call ID 只在所属轮次配对，不能被另一轮相同 ID 的结果覆盖。
    results = _tool_result_index(messages)
    answer = "".join(text_of(message) for message in messages if isinstance(message, AIMessage))
    artifacts = [tool_evidence(message, run_id=run_id, scope=scope)
                 for message in messages] if run_id is not None and scope is not None else []
    evidence = [item for artifact in artifacts if artifact is not None for item in artifact.evidence]
    citations, invalid = resolve_citations(answer, evidence)
    traces = tuple(_build_trace(call, results, run_id, scope)
                   for message in messages if isinstance(message, AIMessage)
                   for call in message.tool_calls)
    last = messages[-1] if messages else None
    complete = is_complete_answer(last)
    if run_id is not None:
        last_meta = last.additional_kwargs.get("agent_run", {}) if isinstance(last, AIMessage) else {}
        complete = complete and last_meta.get("run_id") == str(run_id) and last_meta.get("completed") is True
    return AgentReplayTurn(
        question=text_of(question),
        answer=answer,
        traces=traces,
        run_id=run_id, scope=scope, llm_model=llm_model,
        status=turn_status(completed=complete, traces=traces),
        citations=citations, invalid_citations=invalid,
    )


def _build_trace(
    tool_call: dict[str, Any],
    results: dict[str, ToolMessage],
    run_id: UUID | None,
    scope: ResolvedKnowledgeBaseScope | None,
) -> AgentReplayTrace:
    """把一次工具调用连同它的结果合成一条轨迹。

    Args:
        tool_call: ``AIMessage.tool_calls`` 里的一项。
        results: 按 ``tool_call_id`` 建好的结果索引。

    Returns:
        合成后的轨迹；找不到结果时 ``content`` 为 ``None``。

    Notes:
        纯内存转换。``content`` 为 ``None`` 表示历史里只有调用没有结果，也就是那一轮在工具返回
        之前就中断了（用户取消、或运行报错）。这里不编一句「已中断」文案：回放的职责是如实反映
        存下来的东西，提示语归前端。
    """

    call_id = tool_call.get("id") or ""
    result = results.get(call_id) if call_id else None
    artifact = tool_evidence(result, run_id=run_id, scope=scope) if result is not None and run_id is not None and scope is not None else None
    return AgentReplayTrace(
        tool=tool_call.get("name") or "unknown",
        arguments=dict(tool_call.get("args") or {}),
        content=None if result is None else str(result.content),
        failed=result is not None and result.status == "error",
        scope=artifact.scope if artifact else None,
    )


__all__ = [
    "ReplayTurnStatus",
    "RunMessageGroup",
    "build_replay_turns",
    "group_messages_by_run",
    "summary_messages",
    "text_of",
    "turn_status",
]
