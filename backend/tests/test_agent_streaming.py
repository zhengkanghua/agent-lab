"""LangGraph 双模式流到 SSE 事件序列的翻译测试。

本文件守护三件事：

1. **事件序列的形状**：token 必须出现，工具轨迹必须成对出现，最后一个事件必须是 ``done``
   或 ``error``——前端靠最后这个事件决定「收笔」还是「显示错误」，缺了它输入框会一直转圈。
2. **流式和非流式两种 provider 都能出字**：支持流式的给 ``AIMessageChunk``，不支持的给
   一整条 ``AIMessage``。只认子类会让后一种 provider 一个字都发不出来，而这个 bug 在
   接真实模型前完全看不出来。
3. **追踪默认关闭**：所有测试共用 ``OFFLINE_LANGSMITH_SETTINGS``，任何测试都不会向
   LangSmith 上报。

默认测试全部离线：假模型、假工具、``InMemorySaver``。
"""

from typing import Any
from uuid import uuid4

import openai
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from agent_lab.agent.context import AgentContext
from agent_lab.agent.streaming import stream_agent_events
from agent_lab.config.llm import LangSmithSettings
from agent_lab.schemas.agent_chat import (
    AgentChatEventEnvelope,
    AgentDoneEvent,
    AgentErrorEvent,
    AgentTokenEvent,
    AgentToolCallEvent,
    AgentToolResultEvent,
)
from tests.agent_helpers import (
    OFFLINE_LANGSMITH_SETTINGS,
    CountingTool,
    FailingChatModel,
    ScriptedChatModel,
    StreamingChatModel,
    build_offline_graph,
    run,
    tool_call_message,
)


def collect(
    graph: Any,
    *,
    message: str = "央行降息了吗",
    context: AgentContext | None = None,
) -> list[Any]:
    """跑一次 Agent 并把事件收成列表。"""

    async def drain() -> list[Any]:
        return [
            event
            async for event in stream_agent_events(
                graph,
                message=message,
                thread_id=uuid4(),
                context=context or AgentContext(),
                langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
            )
        ]

    return run(drain())


def test_a_plain_answer_streams_tokens_then_done() -> None:
    """只回答不调工具时，事件序列是若干 token 加一个 done。"""

    model = ScriptedChatModel(responses=[AIMessage(content="央行确实降息了。")])
    events = collect(build_offline_graph(model))

    tokens = [each for each in events if isinstance(each, AgentTokenEvent)]
    assert "".join(each.text for each in tokens) == "央行确实降息了。"
    assert isinstance(events[-1], AgentDoneEvent)


def test_a_streaming_provider_emits_incremental_tokens() -> None:
    """支持流式的 provider 逐块产出，翻译层要逐块转成 token 事件。

    这条和上一条互补：两者的最终文本相同，区别在事件条数。真实 provider 走的是这条路径。
    """

    model = StreamingChatModel(messages=iter([AIMessage(content="降息 25 个基点。")]))
    events = collect(build_offline_graph(model))

    tokens = [each for each in events if isinstance(each, AgentTokenEvent)]
    assert len(tokens) > 1
    assert "".join(each.text for each in tokens) == "降息 25 个基点。"
    assert isinstance(events[-1], AgentDoneEvent)


def test_a_tool_call_produces_a_call_and_a_result_event() -> None:
    """调工具时必须同时给出调用事件和结果事件。

    两个都要：只有结果的话，前端无法显示「正在检索…」这个中间态；只有调用的话，用户不知道
    检索到底成没成。
    """

    counter = CountingTool("search_news", result="检索到 1 篇相关新闻。")
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_news", {"text": "央行降息"}),
            AIMessage(content="根据检索结果，确实降息了。"),
        ]
    )
    events = collect(build_offline_graph(model, [counter.build()]))

    calls = [each for each in events if isinstance(each, AgentToolCallEvent)]
    results = [each for each in events if isinstance(each, AgentToolResultEvent)]
    assert [each.tool for each in calls] == ["search_news"]
    assert calls[0].arguments == {"text": "央行降息"}
    assert [each.tool for each in results] == ["search_news"]
    assert results[0].failed is False
    assert isinstance(events[-1], AgentDoneEvent)


def test_the_tool_call_event_arrives_before_its_result() -> None:
    """调用事件必须早于结果事件。

    顺序反了前端就会先显示结果再显示「正在调用」，看起来像是在倒放。
    """

    counter = CountingTool("search_news")
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_news", {"text": "央行降息"}),
            AIMessage(content="好了。"),
        ]
    )
    events = collect(build_offline_graph(model, [counter.build()]))

    kinds = [type(each) for each in events]
    assert kinds.index(AgentToolCallEvent) < kinds.index(AgentToolResultEvent)


def test_a_failed_tool_is_marked_and_the_run_still_finishes() -> None:
    """工具失败要标记 ``failed``，但整轮运行仍以 done 收尾。

    工具失败不是会话失败：模型收到安全文案后完全可以换个思路继续。把它当成会话失败会让
    用户白白重问一遍。
    """

    counter = CountingTool("search_news", error=RuntimeError("Qdrant 挂了"))
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_news", {"text": "央行降息"}),
            AIMessage(content="检索暂时不可用，我先不下结论。"),
        ]
    )
    events = collect(build_offline_graph(model, [counter.build()]))

    results = [each for each in events if isinstance(each, AgentToolResultEvent)]
    assert results and results[0].failed is True
    assert isinstance(events[-1], AgentDoneEvent)


def _blocked_error() -> openai.PermissionDeniedError:
    """构造一个不带 HTTP 响应体的上游 403。"""

    error = openai.PermissionDeniedError.__new__(openai.PermissionDeniedError)
    error.args = ("Your request was blocked.",)
    return error


# 模型侧每一类故障对应的错误码。合成一条参数化用例是因为它们结构相同（喂一个永远失败的
# 模型、断言最后一个事件的 code 和 retryable），而每一行钉的东西各不相同：
#
# - ``llm_timeout``：最常见的可重试故障。
# - ``llm_request_blocked``：上游 403 必须和认证失败分开报。这两个码合并过一次，代价是真
#   事故里排查方向被带偏——中转站按 User-Agent 拦掉了 openai SDK 的默认标识，报出来却是
#   「认证失败」，于是先去查 Key，而 Key 一直是好的。两者都不可重试、都是 502，但要动的
#   东西一个是凭据、一个是客户端身份。
# - ``agent_internal_error``：没被分类的异常必须落到 Agent 自己的兜底码。错误契约表的通用
#   兜底是 ``pipeline_internal_error``，那是手动流水线的契约值；它漏到 Agent 接口上，前端
#   会按它去查文案，查不到就把原始枚举名显示给用户。
#
# 共同的前提：故障一律以 error **事件**送达，不向调用方抛异常。因为 HTTP 响应头在第一个
# token 发出时就已经送出、之后改不了状态码，抛异常的结果是连接被硬断，前端只看到「网络
# 错误」。所以每条都断言最后一个事件是 AgentErrorEvent，而不是断言抛了什么。
@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (openai.APITimeoutError(request=None), "llm_timeout", True),  # type: ignore[arg-type]
        (_blocked_error(), "llm_request_blocked", False),
        (ValueError("某个没预料到的问题"), "agent_internal_error", False),
    ],
)
def test_a_model_failure_becomes_a_classified_error_event(
    error: BaseException,
    code: str,
    retryable: bool,
) -> None:
    """模型故障以按类型分类的 error 事件结束，且它是流里唯一的事件。"""

    events = collect(build_offline_graph(FailingChatModel(error=error)))

    # 只有一个事件：故障发生在任何 token 之前，所以流里不该有别的东西。
    assert len(events) == 1
    assert isinstance(events[0], AgentErrorEvent)
    assert events[0].code == code
    assert events[0].retryable is retryable


def test_no_error_event_leaks_the_original_exception_text() -> None:
    """错误事件的文案必须来自错误契约表，不能带上游原文。

    上游异常文本可能含中转站地址、API Key 片段或模型返回的原始正文。这条断言钉住
    「只用契约里的固定文案」。单独留一条而不并进上面的参数化：它断言的不是「码对不对」，
    而是「序列化后的整个信封里不含某段敏感文本」，断言对象完全不同。
    """

    model = FailingChatModel(error=openai.AuthenticationError.__new__(openai.AuthenticationError))
    model.error.args = ("Incorrect API key provided: sk-secret123",)
    events = collect(build_offline_graph(model))

    payload = AgentChatEventEnvelope(root=events[-1]).model_dump_json()
    assert "sk-secret123" not in payload
    assert "Incorrect API key" not in payload


def test_every_event_serializes_through_the_discriminated_union() -> None:
    """所有事件都能被信封模型序列化。

    这是前后端契约的最后一道闸：信封是 OpenAPI 里的可辨识联合，前端的 TS 类型由它生成。
    某个事件类型漏出联合的话，生成的类型里就没有它，前端会静默丢掉这类事件。
    """

    counter = CountingTool("search_news")
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_news", {"text": "央行降息"}),
            AIMessage(content="结论如上。"),
        ]
    )
    events = collect(build_offline_graph(model, [counter.build()]))

    for event in events:
        payload = AgentChatEventEnvelope(root=event).model_dump(mode="json")
        assert payload["event"] == event.event
        assert AgentChatEventEnvelope.model_validate(payload).root == event


def test_the_done_event_carries_the_thread_id() -> None:
    """done 事件要带 thread_id，前端靠它续上下一轮对话。

    首轮的 thread_id 由后端生成，前端只有从 done 里拿到它才能把第二个问题发到同一个会话。
    """

    thread_id = uuid4()
    model = ScriptedChatModel(responses=[AIMessage(content="好。")])
    graph = build_offline_graph(model)

    async def drain() -> list[Any]:
        return [
            event
            async for event in stream_agent_events(
                graph,
                message="你好",
                thread_id=thread_id,
                context=AgentContext(),
                langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
            )
        ]

    events = run(drain())

    assert isinstance(events[-1], AgentDoneEvent)
    assert events[-1].thread_id == thread_id


def test_the_error_event_also_carries_the_thread_id() -> None:
    """error 事件同样要带 thread_id。

    **这条防的是列表里冒出重复会话。** 归属行在流开始之前就写好了，所以失败的这一轮也已经
    属于一个存在的会话。error 事件不带 id 的话前端无从知道它，用户点「重发这一轮」时请求里
    没有 thread_id，服务端只能当成新会话再建一行——于是列表里多一条「有提问、没答案」，
    重试几次就多几条，而它们指的都是同一次提问。

    上游限流是最常撞见的失败（真机第一次提问就撞上了），所以这条路径不是边角情况。
    """

    thread_id = uuid4()
    graph = build_offline_graph(
        FailingChatModel(error=openai.APITimeoutError(request=None)),  # type: ignore[arg-type]
    )

    async def drain() -> list[Any]:
        return [
            event
            async for event in stream_agent_events(
                graph,
                message="你好",
                thread_id=thread_id,
                context=AgentContext(),
                langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
            )
        ]

    events = run(drain())

    assert isinstance(events[-1], AgentErrorEvent)
    assert events[-1].thread_id == thread_id


def test_the_same_thread_id_continues_the_earlier_conversation() -> None:
    """同一个 thread_id 的第二轮必须能看到第一轮的消息。

    这条验证 checkpointer 真的被接上了。没接上的表现是：模型每轮都从零开始，用户问
    「那第二点呢」时它完全不知道在说什么。
    """

    thread_id = uuid4()
    model = ScriptedChatModel(
        responses=[AIMessage(content="第一轮回答。"), AIMessage(content="第二轮回答。")]
    )
    graph = build_offline_graph(model)

    async def two_rounds() -> None:
        for question in ("第一个问题", "第二个问题"):
            async for _ in stream_agent_events(
                graph,
                message=question,
                thread_id=thread_id,
                context=AgentContext(),
                langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
            ):
                pass

    run(two_rounds())

    second_round_input = model.received_messages[-1]
    contents = [str(each.content) for each in second_round_input]
    assert "第一个问题" in contents
    assert "第一轮回答。" in contents


def test_different_thread_ids_do_not_share_history() -> None:
    """不同 thread_id 之间必须隔离。

    串了就是会话内容泄露给另一个会话——同一个部署里不同用户的提问会互相看见。
    """

    model = ScriptedChatModel(
        responses=[AIMessage(content="第一轮回答。"), AIMessage(content="另一个会话的回答。")]
    )
    graph = build_offline_graph(model)

    collect(graph, message="第一个会话的问题")
    collect(graph, message="第二个会话的问题")

    second_round_input = model.received_messages[-1]
    contents = [str(each.content) for each in second_round_input]
    assert "第一个会话的问题" not in contents


def test_tracing_stays_off_without_an_api_key() -> None:
    """开了追踪开关但没配 Key 时不上报，也不影响业务。

    可观测性不该阻断业务：Key 忘了配就应该安静地不上报，而不是让所有对话失败。
    """

    settings = LangSmithSettings(tracing=True, api_key="", project="offline-test")
    model = ScriptedChatModel(responses=[AIMessage(content="照常回答。")])
    graph = build_offline_graph(model)

    async def drain() -> list[Any]:
        return [
            event
            async for event in stream_agent_events(
                graph,
                message="你好",
                thread_id=uuid4(),
                context=AgentContext(),
                langsmith_settings=settings,
            )
        ]

    events = run(drain())

    assert isinstance(events[-1], AgentDoneEvent)


@pytest.mark.parametrize(
    "chunk",
    [
        AIMessageChunk(content=""),
        AIMessage(content=[{"type": "tool_use", "id": "x", "name": "search_news", "input": {}}]),
    ],
)
def test_non_text_model_output_produces_no_token_event(chunk: AIMessage) -> None:
    """空内容和非文本块都不该变成 token 事件。

    工具调用阶段模型的 content 常是空串或 ``tool_use`` 块。原样发给前端，用户会看到半截
    JSON 混在回答里。
    """

    from agent_lab.agent.streaming import _token_event

    assert _token_event(chunk) is None


def test_text_blocks_in_a_list_content_become_one_token_event() -> None:
    """列表形态的 content 里的文本块要被抽出来拼成一个 token 事件。

    多模态 provider 会把回答放进 ``[{"type": "text", ...}]``。只处理字符串形态的话，这些
    provider 的回答会整段丢失。
    """

    from agent_lab.agent.streaming import _token_event

    chunk = AIMessage(
        content=[
            {"type": "text", "text": "降息"},
            {"type": "tool_use", "id": "x", "name": "t", "input": {}},
            {"type": "text", "text": " 25 个基点。"},
        ]
    )
    event = _token_event(chunk)

    assert event is not None
    assert event.text == "降息 25 个基点。"


def test_tool_messages_from_unknown_nodes_are_ignored() -> None:
    """非模型、非工具节点的更新不产出事件。

    中间件（比如摘要压缩）也会往状态里写消息。把它们当成模型输出发给前端，用户会看到
    一段内部摘要凭空出现在回答里。
    """

    from agent_lab.agent.streaming import _tool_events

    update = {
        "summarization": {
            "messages": [ToolMessage(content="内部摘要", tool_call_id="x", name="internal")]
        }
    }

    assert _tool_events(update) == []
