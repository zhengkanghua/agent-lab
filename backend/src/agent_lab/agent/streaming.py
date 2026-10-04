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
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphDrained
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import RunControl
from langsmith import Client as LangSmithClient
from langsmith.run_helpers import tracing_context

from agent_lab.agent.context import AgentContext
from agent_lab.agent.evidence import tool_evidence
from agent_lab.agent.middleware import bind_context_window
from agent_lab.agent.replay import build_replay_turns
from agent_lab.api.error_contract import (
    AGENT_CHAT_ERROR_RULES,
    resolve_error_contract,
)
from agent_lab.config.llm import LangSmithSettings
from agent_lab.knowledge.scope import ResolvedKnowledgeBaseScope
from agent_lab.schemas.agent_chat import (
    AgentChatEvent,
    AgentDoneEvent,
    AgentErrorEvent,
    AgentTokenEvent,
    AgentToolCallEvent,
    AgentToolResultEvent,
    AgentRunStartedEvent,
)


logger = logging.getLogger(__name__)

# LangGraph 给每个流事件带的节点名。模型节点和工具节点的产出要分开处理：
# 模型节点的 AIMessageChunk 是给用户看的回答增量，工具节点的 ToolMessage 是调用结果。
# 名字公开了一份：运行驱动者补写未落库的文本时要把它当 ``as_node`` 传给 ``aupdate_state``，
# 而那个字符串必须与图里的节点名对得上。
MODEL_NODE = "model"
TOOLS_NODE = "tools"


@dataclass(frozen=True, slots=True)
class PersistedModelMessage:
    """内部信号：一个模型节点已完整结束，它的文本从这一刻起算「已经落库」。

    **它不进对外事件契约**（不在 ``schemas.agent_chat`` 那个可判别联合里）：浏览器不需要
    知道这条边界，让前端多认一种事件只会多一套它永远用不到的渲染分支。它只给运行驱动者看
    ——驱动者要按「最近一次已落库的模型消息」这个界限累积尚未落库的文本，用于运行被中断时
    的补写（见 ADR 0036）。

    为什么需要单独发一个信号：``updates`` 流里的模型消息本来就在事件流里经过，但当前只把
    「带工具调用的那些」翻成对外的 ``tool_call`` 事件，纯文本的模型消息被丢掉了。驱动者因此
    看不到那条边界，只能按整次运行累积——那会把先完成的节点已经落库的文本再写一遍。

    Attributes:
        text: 这个节点产出的模型文本；与 ``token`` 事件拼出来的那段是同一串字符。
    """

    text: str


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


def _message_text(chunk: AIMessage) -> str:
    """取出一条模型消息里可显示的纯文本部分。

    为什么要过滤：``content`` 在工具调用阶段可能是空串，或是包含 ``tool_use`` 块的列表
    结构。把这些原样发给前端会让用户看到半截 JSON，所以只取文本块。

    Args:
        chunk: 模型节点产出的一个输出增量或一条完整消息。

    Returns:
        可显示的文本；没有可显示内容时是空串。

    Notes:
        纯内存转换，不执行 I/O。``token`` 事件和内部落库信号共用它，两条路径拼出来的字符
        才会是同一份——分成两份实现的话，边界处少算了几个字就会让补写内容出现或丢掉一截。
    """

    content = chunk.content
    if isinstance(content, str):
        return content
    # 列表形态：多模态/工具调用块混排，只保留 type == "text" 的部分。
    return "".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _token_event(chunk: AIMessage) -> AgentTokenEvent | None:
    """从模型输出增量里取出纯文本部分，包成 ``token`` 事件。

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

    text = _message_text(chunk)
    if not text:
        return None
    return AgentTokenEvent(text=text)


def _persisted_model_texts(update: dict[str, Any]) -> list[str]:
    """取出一次节点更新里「刚写完 checkpoint」的模型文本。

    Args:
        update: ``updates`` 模式给出的 ``{节点名: 状态增量}`` 字典。

    Returns:
        本次更新里模型节点产出的非空文本，按出现顺序。非模型节点的更新返回空列表。

    Notes:
        纯内存转换，不执行 I/O。只认模型节点：工具节点的 ``ToolMessage`` 是调用结果而不是
        模型输出，把它当成边界会把边界推错位置。
    """

    payload = update.get(MODEL_NODE)
    if not isinstance(payload, dict):
        return []
    return [
        text
        for message in payload.get("messages") or ()
        if isinstance(message, AIMessage) and (text := _message_text(message))
    ]


def _add_usage(totals: dict[str, int], chunk: AIMessage) -> None:
    """把一个输出增量带的 token 用量累加进本次运行的合计。

    **为什么是累加而不是取最后一个值**：一轮对话里模型节点会被调用多次（每次工具调用之后
    都要再问一次模型），每次调用各自报一份用量。取最后一份只能看到最后那次，而我们想知道的
    是「这一轮总共烧了多少」。

    **为什么只从 messages 流里取**：``updates`` 流里的 ``AIMessage`` 也带 ``usage_metadata``，
    两边都取就会把每次调用算两遍。

    Args:
        totals: 就地累加的合计字典，键为 ``input``、``output``、``total``。
        chunk: 模型节点产出的一个输出增量。没有 ``usage_metadata`` 时什么都不做——
            大多数增量都不带，只有每次调用的收尾那个带。

    Notes:
        纯内存累加，不执行 I/O。数字来自上游 provider 的自报，不同 provider 的口径不完全
        一致（有的把 prompt 缓存命中单独算），所以它适合看趋势和抓异常，不适合用来对账。
    """

    usage = getattr(chunk, "usage_metadata", None)
    if not usage:
        return
    totals["input"] += usage.get("input_tokens") or 0
    totals["output"] += usage.get("output_tokens") or 0
    totals["total"] += usage.get("total_tokens") or 0


def _tool_events(update: dict[str, Any], context: AgentContext) -> list[AgentChatEvent]:
    """从一次节点状态更新里取出工具调用和工具结果事件。

    Args:
        update: ``updates`` 模式给出的 ``{节点名: 状态增量}`` 字典。

    Returns:
        本次更新对应的事件列表，按「先调用后结果」的自然顺序；无关更新返回空列表。

    Notes:
        纯内存转换，不执行 I/O。工具参数会原样带给前端作为调用轨迹展示——它们是模型
        生成的检索词，不含服务端凭据。

        两类事件都带 ``tool_call_id``，前端据此精确配对。模型可以在一轮里并发调用同一个
        工具多次（不同检索词），而结果的到达顺序没有保证，所以只发工具名会让前端把参数和
        结果对调。id 由模型生成、本就在 ``tool_calls`` 和 ``ToolMessage`` 上，这里只是带下去。
    """

    events: list[AgentChatEvent] = []
    # 1、只看模型节点和工具节点的更新，其余节点（如中间件内部状态）与工具轨迹无关。
    for node, payload in update.items():
        if node not in {MODEL_NODE, TOOLS_NODE} or not isinstance(payload, dict):
            continue
        for message in payload.get("messages") or ():
            # 2、模型节点：AIMessage 带 tool_calls 表示它决定要调工具。
            for tool_call in getattr(message, "tool_calls", None) or ():
                events.append(
                    AgentToolCallEvent(
                        tool_call_id=tool_call.get("id") or "",
                        tool=tool_call["name"],
                        arguments=dict(tool_call.get("args") or {}),
                    )
                )
            # 3、工具节点：ToolMessage 是执行结果。status == "error" 是
            #    ToolErrorMiddleware 兜底后打的标记，此时 content 已是安全文案。
            if isinstance(message, ToolMessage):
                artifact = tool_evidence(message, run_id=context.run_id, scope=context.scope) if context.scope is not None else None
                events.append(
                    AgentToolResultEvent(
                        tool_call_id=message.tool_call_id or "",
                        tool=message.name or "unknown",
                        content=str(message.content),
                        failed=message.status == "error",
                        scope=artifact.scope if artifact else None,
                        evidence=artifact.evidence if artifact else (),
                    )
                )
    return events


async def build_terminal_event(
    graph: CompiledStateGraph,
    *,
    thread_id: UUID,
    run_id: UUID,
    scope: ResolvedKnowledgeBaseScope | None,
) -> AgentDoneEvent:
    """按持久化状态算出这次运行的终态 ``done`` 事件。

   正常跑完和协作式停止共用它，所以「点停止时看到的」与「刷新后看到的」是同一份口径：这里从
   checkpoint 消息里取答案、完成状态与引用，而刷新后回放读的是写入侧从**同一批消息**投影进
   业务表的行（投影用的是同一批纯函数：``replay.text_of``、``evidence.tool_evidence``，回放
   组装用 ``replay.turn_status`` 与 ``evidence.resolve_citations``）。两边读的源不同——终态
   事件发出时这一轮可能还没落表——所以共用的是判定规则，不是数据。

   为什么要从持久化结果现算而不是拿流式累计的文本：重试会留下临时输出（被弃用的那一次的文本已经
   发给用户了），预算耗尽与上游截断也都会让实际落库的内容与累计文本不同。以落库结果为准，回放与
   终态才不会分叉。

   Args:
       graph: 进程级共享的已编译 Agent 图，用 ``aget_state`` 读最新状态。
       thread_id: 本次运行所属会话。
       run_id: 本次运行的标识；据此在同一会话的多轮里找到这一轮。
       scope: 本次运行的实际范围；为 ``None`` 表示归属未知（不取轮次，退化成最后一轮）。

   Returns:
       可直接发给订阅者的 ``AgentDoneEvent``；找不到这一轮时答案是空串、状态为 ``incomplete``。

   Notes:
       执行会话历史读取（checkpointer 的读 I/O），不写任何东西。
   """

    snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
    turns, _, _ = build_replay_turns((snapshot.values or {}).get("messages") or [])
    turn = (
        next((item for item in turns if item.run_id == run_id), None)
        if scope is not None
        else (turns[-1] if turns else None)
    )
    return AgentDoneEvent(
        thread_id=thread_id,
        answer=turn.answer if turn else "",
        status=turn.status if turn else "incomplete",
        citations=turn.citations if turn else (),
        invalid_citations=turn.invalid_citations if turn else (),
    )


async def stream_agent_events(
    graph: CompiledStateGraph,
    *,
    message: str,
    thread_id: UUID,
    context: AgentContext,
    langsmith_settings: LangSmithSettings,
    control: RunControl | None = None,
    resume: bool = False,
) -> AsyncIterator[AgentChatEvent | PersistedModelMessage]:
    """跑一次 Agent，把过程翻译成事件流。

    正常结束时最后一个事件是 ``done``；已分类的失败以 ``error`` 事件结束，**不抛异常**。
    原因是响应头在第一个 token 发出时就已经送出，之后没法再改 HTTP 状态码，所以流一旦
    开始，失败只能作为事件送达。

    ``resume`` 为真时以「无新输入」的方式续跑同一个会话：提问与轮次身份已经在 checkpoint
    里，不再拼一条 HumanMessage，也不重发 ``run_started``（那次运行早就开始了）。这是部署时
    接手别的进程排空下来的运行所用的路径（见 ADR 0040）。

    Args:
        graph: 进程级共享的已编译 Agent 图。
        message: 用户这一轮的提问；``resume`` 为真时忽略。
        thread_id: 会话 id；checkpointer 按它读写历史。
        context: 本次运行的上下文。它同时是历史压缩那份比例的基准：当轮模型的上下文窗口在图
            跑起来之前从这里取出来，放进按协程隔离的上下文变量（见中间件的
            ``_get_profile_limits``）；上下文里没有模型时压缩回落到构造期的占位窗口。
        langsmith_settings: 追踪开关与凭据。
        control: 本次运行的排空控制器；进程收尾时请求它排空，图会在当前 superstep 落盘后
            停下并抛 ``GraphDrained``。
        resume: 是否以「无新输入」的方式接着跑 checkpoint 里尚未完成的节点。

    Yields:
        ``token`` / ``tool_call`` / ``tool_result`` 事件，最后是 ``done`` 或 ``error``；
        中间还可能夹着只给运行驱动者看的 ``PersistedModelMessage`` 落库信号。

    Raises:
        GraphDrained: 有人请求排空且图走到了可交接的边界。**刻意不翻成 error 事件**：
            它不是失败，调用方（运行驱动者）据此写下「等接手」标记。

    Notes:
        本函数执行模型 HTTP I/O、Qdrant 检索、PostgreSQL 读取和会话历史读写，但不写任何
        业务数据（见 ADR 0003）。异常只记类型名，不记 ``str(exc)``——上下文里有用户提问和
        新闻正文，异常文本可能把它们带进日志。
    """

    # 1、准备两样东西：config 里的 thread_id 是 checkpointer 定位历史的钥匙，
    #    client 为 None 表示这次不上报追踪。
    config = {"configurable": {"thread_id": str(thread_id)}}
    client = _build_tracing_client(langsmith_settings)
    actual_model_name: str | None = None
    has_answer = False
    # 本次运行的 token 合计。放在 try 外面，这样失败的那一轮也能在 finally 里报出已经
    # 烧掉的量——失控循环恰恰都是以失败收尾的，那时候的用量最值得看。
    usage_totals = {"input": 0, "output": 0, "total": 0}
    try:
        # 接手续跑时没有新的 run_started 可发：那次运行在旧进程里就开始了，前端靠回放拿到
        # 在途运行 id，不需要重新认领它。
        if context.scope is not None and not resume:
            yield AgentRunStartedEvent(thread_id=thread_id, run_id=context.run_id, scope=context.scope)
        # 2、开一个「只管本次运行」的追踪范围。tracing_context 不写 os.environ，
        #    所以并发请求之间不会互相污染，也不需要在进程启动时就决定好。
        with tracing_context(
            enabled=client is not None,
            project_name=langsmith_settings.project,
            client=client,
        ):
            # 3、跑图，同时订阅两种流。这里只传用户这一条新消息——历史由 checkpointer
            #    按 config 里的 thread_id 自己接在前面，不用我们拼。续跑时输入是 None：
            #    图从 checkpoint 里尚未完成的节点接着跑，不新开一轮。
            #
            #    当轮模型的窗口在跑图之前放进按协程隔离的上下文变量：历史压缩按它的占比
            #    触发与保留，而消费者是中间件，它只能从那里拿到窗口。上下文里没有模型时放
            #    ``None``，那表示这一轮没窗口可跟，中间件回落到构造期的占位值。
            #
            #    **不复位，也不靠它跨多次恢复传递**：消费这个生成器的是 ``runs.py`` 的运行
            #    驱动者，它把每一次 ``__anext__`` 包成一个 Task，而 Task 只在创建时复制当前
            #    上下文——上一次恢复里 set 的值下一次恢复看不到（复位会直接抛 ``ValueError``，
            #    Token 属于另一个上下文）。压缩只发生在本次图执行的第一个超步里，就在
            #    set 的这一段之内，所以够用；不会串到别处，也不会漏给下一次运行。
            bind_context_window(
                context.llm_model.context_window if context.llm_model is not None else None
            )
            #    运行标识**无条件**带上：会话历史按提问行上的 ``run_id`` 切分、排空接手按它
            #    找回这一轮（取不到就按「这一轮已经不在 checkpoint 里」放弃），缺了它整轮认不出
            #    归属。范围是另一样东西——只有本次运行真有范围时才写进去，回放读取容忍它缺失。
            #    当轮选定的模型同样只在真的有的时候写：它**连展示名与上下文窗口一起冻结**
            #    （快照必须自足：条目后来改名或停用不改写已经发生过的那几轮，而接手那一轮
            #    不许回查目录）。
            agent_run: dict[str, Any] = {"run_id": str(context.run_id)}
            if context.scope is not None:
                agent_run["scope"] = context.scope.model_dump(mode="json")
            if context.llm_model is not None:
                agent_run["llm_model"] = context.llm_model.model_dump(mode="json")
            graph_input = (
                None
                if resume
                else {"messages": [HumanMessage(content=message, additional_kwargs={"agent_run": agent_run})]}
            )
            async for stream_mode, chunk in graph.astream(
                graph_input,
                config=config,
                context=context,
                stream_mode=["updates", "messages"],
                control=control,
            ):
                # 4、messages 流 → 打字机效果。给的是 (消息增量, metadata) 二元组，
                #    只有模型节点产的文本才是用户要看的字，工具节点的要滤掉。
                if stream_mode == "messages":
                    part, metadata = chunk
                    if (
                        isinstance(part, AIMessage)
                        and metadata.get("langgraph_node") == MODEL_NODE
                    ):
                        # 记录实际模型名：只记一次，从第一个 AIMessage 的 response_metadata 取。
                        if actual_model_name is None and hasattr(part, "response_metadata"):
                            actual_model_name = part.response_metadata.get("model_name")
                        _add_usage(usage_totals, part)
                        token = _token_event(part)
                        if token is not None:
                            has_answer = True
                            yield token
                # 5、updates 流 → 工具轨迹。工具调用和工具结果只在这个流里出现，
                #    messages 流里没有。
                elif stream_mode == "updates":
                    for event in _tool_events(chunk, context):
                        yield event
                    # 节点结束时这条模型消息已经随 checkpoint 落库，所以它的文本从此刻起
                    # 不再属于「尚未落库」的部分。驱动者靠这个信号把累积边界推到这里之后。
                    for text in _persisted_model_texts(chunk):
                        yield PersistedModelMessage(text=text)
            # 以本次持久化结果校正流式重试的临时输出；回放与终态从同一份消息计算。
            terminal = await build_terminal_event(
                graph,
                thread_id=thread_id,
                run_id=context.run_id,
                scope=context.scope,
            )
    except GraphDrained:
        # 排空不是失败，是正常的收尾路径。往上抛给运行驱动者：它据此写下「等接手」标记、
        # 把这次运行交给别的进程（见 ADR 0040）。这里绝不能翻成 error 事件——那会让接手
        # 看到一个本可续跑的运行被当成了失败。
        raise
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
        # thread_id 也要带上：归属行在流开始之前就写好了，所以失败的这一轮同样有会话。
        # 不带的话前端不知道 id，用户点「重发」会开出第二个会话，列表里于是多一条
        # 「有提问、没答案」——重试几次就多几条。
        yield AgentErrorEvent(
            thread_id=thread_id,
            code=rule.code,
            detail=rule.detail,
            retryable=rule.retryable,
        )
        return
    finally:
        # 记录对话结束，标记是否有回答内容、实际使用的模型和本轮 token 用量。
        #
        # 用量和模型名记在同一行是有意的：单看「这轮花了 30000 token」判断不出是否异常，
        # 得知道是哪个模型、有没有出答案。三者凑在一起，「模型空转」（有用量、无答案）和
        # 「失控循环」（用量远超同类请求）都能从这一行看出来，不必去翻别的日志。
        #
        # 不记提问和回答原文：这条日志的用途是看成本和异常，不是审计对话内容。
        # 上游不报用量时三个数都是 0——那是「没拿到」，不是「没花」，不要据此断言免费。
        logger.info(
            "Agent 对话结束 thread_id=%s has_answer=%s model=%s "
            "tokens_input=%d tokens_output=%d tokens_total=%d",
            thread_id,
            has_answer,
            actual_model_name or "unknown",
            usage_totals["input"],
            usage_totals["output"],
            usage_totals["total"],
        )
    # 7、正常收尾。done 带上 thread_id，前端拿它接着发下一轮。
    yield terminal


__all__ = [
    "MODEL_NODE",
    "PersistedModelMessage",
    "build_terminal_event",
    "stream_agent_events",
]
