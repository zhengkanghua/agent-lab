"""把 checkpointer 存下的消息列表翻成「一轮一问一答」的回放结构。

本模块只做纯内存翻译：输入是一串 LangChain 消息，输出是 ``schemas.agent_thread`` 的回放模型。
它不读数据库、不调 checkpointer、不校验归属——取状态和校验归属都在 ``api/agent_threads.py``。

**为什么回放要从 checkpointer 读，而不是自己另存一份消息副本**：副本注定和真实上下文分叉。
``SummarizationMiddleware`` 会把早期历史压成摘要并**删掉原始消息**，副本压不到，于是界面上显示
完整二十轮、模型实际只看到「摘要 + 最近 20 条」。用户指着屏幕上写着的话问「你刚才说的那个」，
模型说不知道——两边各自都自洽，排查起来极费劲。

**摘要那条消息长什么样**（langchain 1.3.15 实测，见本模块的测试）：压缩动作是
``RemoveMessage(id=REMOVE_ALL_MESSAGES)`` 清空整个列表、再重建，摘要被包成一条 **HumanMessage**，
带 ``additional_kwargs={"lc_source": "summarization"}``，正文前面还有一句英文
``Here is a summary of the conversation to date:``。它长得和用户提问一模一样，只有那个
``lc_source`` 标记能区分——不认出来的话，用户会在自己的对话记录里看到一句从没问过的「提问」。
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from agent_lab.schemas.agent_thread import AgentReplayTrace, AgentReplayTurn


logger = logging.getLogger(__name__)

# SummarizationMiddleware 给摘要消息打的来源标记。这是上游的内部约定，不是公开契约，所以有一条
# 测试拿真实中间件跑一次压缩来钉住它：上游改了形态，那条测试会失败，而不是让英文摘要静默地
# 出现在用户的对话记录里冒充提问。
_SUMMARY_SOURCE_MARKER = "summarization"


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


def _text_of(message: BaseMessage) -> str:
    """取一条消息里可显示的纯文本。

    Args:
        message: 任意 LangChain 消息。

    Returns:
        文本内容；多模态或工具调用块混排时只保留 ``type == "text"`` 的部分。

    Notes:
        纯内存转换。与 ``agent/streaming.py`` 的 ``_token_event`` 同一套过滤逻辑，理由也一样：
        ``content`` 在工具调用阶段可能是含 ``tool_use`` 块的列表，原样拼出来会让用户看到半截 JSON。
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


@dataclass(slots=True)
class _OpenTurn:
    """正在累积中的一轮。

    存在的理由是 ``AgentReplayTurn`` 是 frozen 的，边扫边改就得每次重建一个对象。用一个可变的
    中间体累积、扫完再定型，比在循环里读写外层局部变量清楚——后者能跑，但改的人得先想清楚
    闭包捕获的是引用还是值。
    """

    question: str
    answer_parts: list[str] = field(default_factory=list)
    traces: list[AgentReplayTrace] = field(default_factory=list)

    def finish(self) -> AgentReplayTurn:
        """定型成对外的一轮。"""

        return AgentReplayTurn(
            question=self.question,
            answer="".join(self.answer_parts),
            traces=tuple(self.traces),
        )


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

        分轮规则：遇到一条**不是摘要**的 HumanMessage 就开一轮，之后的 AIMessage 文本累加进
        ``answer``、工具调用累加进 ``traces``，直到下一条用户提问。

        系统提示词不会出现在这里：它由 ``resolve_system_prompt`` 每次动态注入到模型请求里，
        不进 checkpointer 的消息历史（见 ``agent/middleware.py``）。

        出现在第一条用户提问之前的 AIMessage 会被丢弃并记一条 debug 日志。正常链路不该有这种
        消息，真出现了大概是上游改了状态结构——丢掉比凭空造一轮空提问好，后者会让用户以为
        自己问过什么。
    """

    turns: list[AgentReplayTurn] = []
    summarized = False
    summary: str | None = None

    results = _tool_result_index(messages)

    # 当前正在攒的一轮。``None`` 表示还没遇到第一条用户提问。
    open_turn: _OpenTurn | None = None

    for message in messages:
        # 1、摘要那条：它是 HumanMessage，但不是用户问的。单独收走，不开新一轮。
        if _is_summary_message(message):
            summarized = True
            summary = _text_of(message)
            continue

        # 2、真正的用户提问：收掉上一轮，开新一轮。
        if isinstance(message, HumanMessage):
            if open_turn is not None:
                turns.append(open_turn.finish())
            open_turn = _OpenTurn(question=_text_of(message))
            continue

        # 3、模型消息：文本进 answer，工具调用进 traces。两者可能同时存在（模型一边说话一边
        #    决定调工具），所以不是 if/else。
        if isinstance(message, AIMessage):
            if open_turn is None:
                logger.debug(
                    "回放时丢弃出现在首条提问之前的模型消息 message_type=%s",
                    type(message).__name__,
                )
                continue
            open_turn.answer_parts.append(_text_of(message))
            for tool_call in message.tool_calls or ():
                open_turn.traces.append(_build_trace(tool_call, results))

    if open_turn is not None:
        turns.append(open_turn.finish())
    return tuple(turns), summarized, summary


def _build_trace(
    tool_call: dict[str, Any],
    results: dict[str, ToolMessage],
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
    return AgentReplayTrace(
        tool=tool_call.get("name") or "unknown",
        arguments=dict(tool_call.get("args") or {}),
        content=None if result is None else str(result.content),
        failed=result is not None and result.status == "error",
    )


__all__ = ["build_replay_turns"]
