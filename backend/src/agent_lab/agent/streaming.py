"""把 LangGraph 的双模式流翻译成本项目的 SSE 事件序列。

**为什么要同时开两种 stream_mode**：
- ``messages`` 给的是模型逐 token 的输出增量——用户看到的「字一个个冒出来」；
- ``updates`` 给的是每个节点执行完的状态变化——工具调用和工具结果只在这里出现。

只开 ``messages`` 就看不到工具轨迹，只开 ``updates`` 就没有打字机效果。所以两个都开，再靠
``metadata["langgraph_node"]`` 把 token 归到模型节点、把工具消息归到工具节点。

本模块只做「翻译」和「异常分类」，不碰 HTTP：SSE 的行格式、心跳和响应头在
``api/agent_chat.py``。这样离线测试可以直接断言事件序列，不需要起一个 HTTP 服务。
"""

import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from langsmith import Client as LangSmithClient
from langsmith.run_helpers import tracing_context

from agent_lab.agent.context import AgentContext
from agent_lab.api.error_contract import (
    AGENT_CHAT_ERROR_RULES,
    resolve_error_contract,
)
from agent_lab.config.llm import LangSmithSettings
from agent_lab.schemas.agent_chat import (
    AgentChatEvent,
    AgentDoneEvent,
    AgentErrorEvent,
    AgentTokenEvent,
    AgentToolCallEvent,
    AgentToolResultEvent,
)


logger = logging.getLogger(__name__)

# LangGraph 给每个流事件带的节点名。模型节点和工具节点的产出要分开处理：
# 模型节点的 AIMessageChunk 是给用户看的回答增量，工具节点的 ToolMessage 是调用结果。
_MODEL_NODE = "model"
_TOOLS_NODE = "tools"


def _build_tracing_client(settings: LangSmithSettings) -> LangSmithClient | None:
    """按配置构造 LangSmith 客户端；未开启追踪时返回 ``None``。

    为什么要显式建客户端而不是让 SDK 读环境变量：langsmith 的 ``get_env_var`` 带
    ``lru_cache`` 且只认 ``os.environ``，而本项目的配置来自 pydantic-settings 读 ``.env``，
    从不写进 ``os.environ``。实测即使 ``os.environ.pop("LANGSMITH_TRACING")``，
    ``tracing_is_enabled()`` 仍返回 True——所以环境变量这条路在本项目里根本不通，只能把
    凭据直接交给客户端对象。

    Args:
        settings: LangSmith 开关、API Key、项目名和 endpoint。

    Returns:
        配置好的客户端，或 ``None`` 表示本次运行不上报。

    Notes:
        只构造对象，不发请求。追踪开关是「进程级」的：改了 ``.env`` 需要重启服务，
        不支持热切换。
    """

    if not settings.tracing:
        return None
    api_key = settings.api_key.get_secret_value()
    if not api_key:
        # 开了开关但没给 Key：不上报也不报错——追踪是可观测性，不该阻断业务。
        logger.warning("LANGSMITH_TRACING 已开启但未配置 API Key，本次运行不上报追踪。")
        return None
    return LangSmithClient(api_key=api_key, api_url=str(settings.endpoint))


def _token_event(chunk: AIMessage) -> AgentTokenEvent | None:
    """从模型输出增量里取出纯文本部分。

    为什么要过滤：``content`` 在工具调用阶段可能是空串，或是包含 ``tool_use`` 块的列表
    结构。把这些原样发给前端会让用户看到半截 JSON，所以只取文本块。

    Args:
        chunk: 模型节点产出的一个输出增量。支持流式的 provider 给的是
            ``AIMessageChunk``（``AIMessage`` 的子类，一次一小段），不支持流式的给的是
            一整条 ``AIMessage``。两种都按「追加文本」处理，拼出来的结果一样，所以这里
            按父类判断——只认子类会让非流式 provider 一个字都发不出来。

    Returns:
        ``AgentTokenEvent``，或 ``None`` 表示这个增量没有可显示的文本。

    Notes:
        纯内存转换，不执行 I/O。
    """

    content = chunk.content
    if isinstance(content, str):
        text = content
    else:
        # 列表形态：多模态/工具调用块混排，只保留 type == "text" 的部分。
        text = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if not text:
        return None
    return AgentTokenEvent(text=text)


def _tool_events(update: dict[str, Any]) -> list[AgentChatEvent]:
    """从一次节点状态更新里取出工具调用和工具结果事件。

    Args:
        update: ``updates`` 模式给出的 ``{节点名: 状态增量}`` 字典。

    Returns:
        本次更新对应的事件列表，按「先调用后结果」的自然顺序；无关更新返回空列表。

    Notes:
        纯内存转换，不执行 I/O。工具参数会原样带给前端作为调用轨迹展示——它们是模型
        生成的检索词，不含服务端凭据。
    """

    events: list[AgentChatEvent] = []
    # 1、只看模型节点和工具节点的更新，其余节点（如中间件内部状态）与工具轨迹无关。
    for node, payload in update.items():
        if node not in {_MODEL_NODE, _TOOLS_NODE} or not isinstance(payload, dict):
            continue
        for message in payload.get("messages") or ():
            # 2、模型节点：AIMessage 带 tool_calls 表示它决定要调工具。
            for tool_call in getattr(message, "tool_calls", None) or ():
                events.append(
                    AgentToolCallEvent(
                        tool=tool_call["name"],
                        arguments=dict(tool_call.get("args") or {}),
                    )
                )
            # 3、工具节点：ToolMessage 是执行结果。status == "error" 是
            #    ToolErrorMiddleware 兜底后打的标记，此时 content 已是安全文案。
            if isinstance(message, ToolMessage):
                events.append(
                    AgentToolResultEvent(
                        tool=message.name or "unknown",
                        content=str(message.content),
                        failed=message.status == "error",
                    )
                )
    return events


async def stream_agent_events(
    graph: CompiledStateGraph,
    *,
    message: str,
    thread_id: UUID,
    context: AgentContext,
    langsmith_settings: LangSmithSettings,
) -> AsyncIterator[AgentChatEvent]:
    """跑一次 Agent，把过程翻译成事件流。

    正常结束时最后一个事件是 ``done``；已分类的失败以 ``error`` 事件结束，**不抛异常**。
    原因是响应头在第一个 token 发出时就已经送出，之后没法再改 HTTP 状态码，所以流一旦
    开始，失败只能作为事件送达。

    Args:
        graph: 进程级共享的已编译 Agent 图。
        message: 用户这一轮的提问。
        thread_id: 会话 id；checkpointer 按它读写历史。
        context: 本次运行的上下文，目前只含可选的自定义系统提示词。
        langsmith_settings: 追踪开关与凭据。

    Yields:
        ``token`` / ``tool_call`` / ``tool_result`` 事件，最后是 ``done`` 或 ``error``。

    Notes:
        本函数执行模型 HTTP I/O、Qdrant 检索、PostgreSQL 读取和会话历史读写，但不写任何
        业务数据（见 ADR 0003）。异常只记类型名，不记 ``str(exc)``——上下文里有用户提问和
        新闻正文，异常文本可能把它们带进日志。
    """

    # 1、准备两样东西：config 里的 thread_id 是 checkpointer 定位历史的钥匙，
    #    client 为 None 表示这次不上报追踪。
    config = {"configurable": {"thread_id": str(thread_id)}}
    client = _build_tracing_client(langsmith_settings)
    try:
        # 2、开一个「只管本次运行」的追踪范围。tracing_context 不写 os.environ，
        #    所以并发请求之间不会互相污染，也不需要在进程启动时就决定好。
        with tracing_context(
            enabled=client is not None,
            project_name=langsmith_settings.project,
            client=client,
        ):
            # 3、跑图，同时订阅两种流。这里只传用户这一条新消息——历史由 checkpointer
            #    按 config 里的 thread_id 自己接在前面，不用我们拼。
            async for stream_mode, chunk in graph.astream(
                {"messages": [{"role": "user", "content": message}]},
                config=config,
                context=context,
                stream_mode=["updates", "messages"],
            ):
                # 4、messages 流 → 打字机效果。给的是 (消息增量, metadata) 二元组，
                #    只有模型节点产的文本才是用户要看的字，工具节点的要滤掉。
                if stream_mode == "messages":
                    part, metadata = chunk
                    if (
                        isinstance(part, AIMessage)
                        and metadata.get("langgraph_node") == _MODEL_NODE
                    ):
                        token = _token_event(part)
                        if token is not None:
                            yield token
                # 5、updates 流 → 工具轨迹。工具调用和工具结果只在这个流里出现，
                #    messages 流里没有。
                elif stream_mode == "updates":
                    for event in _tool_events(chunk):
                        yield event
    except Exception as exc:
        # 6、失败翻成一个 error 事件送出去，不往上抛。第一个 token 发走时响应头就定了，
        #    这之后改不了 HTTP 状态码，只能把失败当成流里的一条事件。
        rule = resolve_error_contract(exc, AGENT_CHAT_ERROR_RULES)
        logger.error(
            "Agent 运行失败 thread_id=%s error_type=%s code=%s",
            thread_id,
            type(exc).__name__,
            rule.code,
        )
        yield AgentErrorEvent(
            code=rule.code,
            detail=rule.detail,
            retryable=rule.retryable,
        )
        return
    # 7、正常收尾。done 带上 thread_id，前端拿它接着发下一轮。
    yield AgentDoneEvent(thread_id=thread_id)


__all__ = ["stream_agent_events"]
