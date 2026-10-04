"""Agent 中间件流水线的顺序语义、错误脱敏和运行上限测试。

本文件守护的是 ``docs/adr/0005-middleware-order-semantics.md`` 记录的那条容易写反的规则：
中间件列表里越靠后的越内层、越先执行。

**为什么这些断言必须查调用次数，而不只是查最终消息**：顺序写反时最终消息完全相同——两种
顺序下用户都会看到「工具调用失败：...」这句安全文案。区别只在「工具被真正执行了几次」：
正确顺序 3 次（1 次 + 2 次重试），写反 1 次（兜底在内层先把异常吞成 ToolMessage，重试
永远收不到异常，成了死代码）。所以只断言消息的测试在两种顺序下都会通过，等于没测。

默认测试全部离线：假模型、假工具、``InMemorySaver``，不访问 Ollama、PostgreSQL、Qdrant
或 LangSmith。
"""

from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from agent_lab.agent.context import AgentContext
from agent_lab.agent.evidence import DocumentEvidence, ToolEvidence
from agent_lab.agent.limits import (
    MODEL_CALL_RUN_LIMIT,
    MODEL_RETRY_MAX,
    PRUNED_TOOL_RESULT_HEAD_CHARS,
    PRUNED_TOOL_RESULT_PLACEHOLDER,
    PRUNED_TOOL_RESULT_TAIL_CHARS,
    SUMMARIZATION_KEEP_FRACTION,
    SUMMARIZATION_TRIGGER_FRACTION,
    TOOL_CALL_RUN_LIMIT,
    TOOL_RETRY_MAX,
)
from agent_lab.agent.replay import build_replay_turns
from agent_lab.agent.middleware import (
    RunSafeSummarizationMiddleware,
    append_current_date,
    bind_context_window,
    build_agent_middleware,
    select_system_prompt,
    unbind_context_window,
)
from agent_lab.agent.prompts import DEFAULT_SYSTEM_PROMPT, SUMMARY_PROMPT
from agent_lab.schemas.llm_models import ResolvedLlmModel
from agent_lab.agent.streaming import PersistedModelMessage, stream_agent_events
from agent_lab.schemas.agent_chat import AgentErrorEvent, AgentToolResultEvent
from tests.agent_helpers import (
    OFFLINE_LANGSMITH_SETTINGS,
    CountingTool,
    FailingChatModel,
    ScriptedChatModel,
    build_offline_graph,
    parallel_tool_call_message,
    run,
    tool_call_message,
    window_that_triggers,
)
from tests.agent_scope_helpers import NEWS_SCOPE


LEAKY_MESSAGE = "postgresql://admin:secret@10.0.0.1:5432/news"


async def collect(
    graph: Any,
    message: str = "问题",
    context: AgentContext | None = None,
) -> list[Any]:
    """跑一次运行并收集全部事件，供断言使用。"""

    events: list[Any] = []
    async for event in stream_agent_events(
        graph,
        message=message,
        thread_id=uuid4(),
        context=context or AgentContext(),
        langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
    ):
        events.append(event)
    return events


def test_failing_tool_is_retried_before_the_error_handler_sees_it() -> None:
    """工具失败时必须先重试满次数，再交给兜底翻译成安全文案。

    这是 ADR 0005 的核心断言：``invocations`` 为 ``1 + TOOL_RETRY_MAX`` 证明重试中间件
    确实在内层。若两个中间件顺序写反，这里会是 1——而下面的文案断言依然通过。
    """

    failing = CountingTool("search_documents", error=RuntimeError(LEAKY_MESSAGE))
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_documents", {"text": "央行"}),
            AIMessage(content="抱歉，暂时查不到。"),
        ]
    )
    graph = build_offline_graph(model, [failing.build()])

    events = run(collect(graph))

    assert len(failing.invocations) == 1 + TOOL_RETRY_MAX
    results = [event for event in events if isinstance(event, AgentToolResultEvent)]
    assert len(results) == 1
    assert results[0].failed is True


def test_an_unregistered_tool_call_ends_the_run_with_a_classified_error() -> None:
    """模型请求没注册的工具时，必须以 ``llm_response_invalid`` 结束，而不是让模型自己纠正。

    这条钉住的是 ``UnknownToolGuardMiddleware`` 存在的全部理由。**关键在于断言事件类型**：
    守卫若被排到 ``ToolErrorMiddleware`` 的内层，异常会被兜底翻成安全文案变成一条
    ``tool_result``，运行照常走到 ``done``——用户拿到的是「查了资料但不回答」，也就是它要
    替换掉的那个症状。所以这里要求最后一个事件是 ``AgentErrorEvent``，且流里没有任何
    ``tool_result``。

    工具名取 ``Bash`` 不是随手编的：``LLM_MODEL=auto``（那项环境变量今天已退休）那次事故里
    模型真的发出了
    ``Bash(command, description)``，见 ``UnknownToolGuardMiddleware`` 的说明。
    """

    registered = CountingTool("search_documents")
    model = ScriptedChatModel(
        responses=[
            tool_call_message("Bash", {"command": "ls", "description": "列目录"}),
            AIMessage(content="不该走到这一步。"),
        ]
    )
    graph = build_offline_graph(model, [registered.build()])

    events = run(collect(graph))

    assert isinstance(events[-1], AgentErrorEvent)
    assert events[-1].code == "llm_response_invalid"
    assert events[-1].retryable is False
    # 未注册的工具不存在，自然没得执行；已注册的那个也不该被顺手调用。
    assert registered.invocations == []
    assert [event for event in events if isinstance(event, AgentToolResultEvent)] == []


def test_a_registered_tool_call_passes_the_guard_untouched() -> None:
    """已注册的工具照常执行——守卫只拦不认识的名字，不给正常路径加条件。

    与上一条成对：只有上一条时，一个「什么都拦」的坏实现同样能通过。
    """

    registered = CountingTool("search_documents", result="检索结果")
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_documents", {"text": "央行"}),
            AIMessage(content="根据检索结果……"),
        ]
    )
    graph = build_offline_graph(model, [registered.build()])

    events = run(collect(graph))

    assert len(registered.invocations) == 1
    results = [event for event in events if isinstance(event, AgentToolResultEvent)]
    assert len(results) == 1
    assert results[0].failed is False
    assert not [event for event in events if isinstance(event, AgentErrorEvent)]


def test_tool_failure_never_leaks_exception_text() -> None:
    """工具异常文本不得出现在任何事件里。

    用一个长得像数据库连接串的异常消息，因为那是最坏情况：真实的 ``SQLAlchemyError``
    有时会把 DSN 带在消息里，一旦顺着 ToolMessage 进了模型上下文，模型完全可能在回答里
    复述它。
    """

    failing = CountingTool("read_document", error=RuntimeError(LEAKY_MESSAGE))
    model = ScriptedChatModel(
        responses=[
            tool_call_message("read_document", {"text": "x"}),
            AIMessage(content="查不到。"),
        ]
    )
    graph = build_offline_graph(model, [failing.build()])

    events = run(collect(graph))

    # 滤掉只给运行驱动者看的落库信号：它不是对外事件，没有 model_dump，也不该被当成
    # 「发给前端的内容」来断言。它的存在由 tests/test_agent_streaming.py 单独守护。
    payload = str(
        [event.model_dump() for event in events if not isinstance(event, PersistedModelMessage)]
    )
    assert "secret" not in payload
    assert "postgresql://" not in payload
    assert "RuntimeError" not in payload


def test_run_continues_after_a_tool_fails() -> None:
    """工具失败不该终止整段运行——模型要有机会解释或换个思路。

    如果 ``sanitize_tool_error`` 返回 ``None``，异常会继续上抛、运行中断，用户只会看到
    一个错误事件。这里断言运行走到了 ``done``，且模型拿到失败结果后仍产出了回答。
    """

    failing = CountingTool("search_documents", error=RuntimeError("boom"))
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_documents", {"text": "央行"}),
            AIMessage(content="检索暂时不可用，我无法回答这个问题。"),
        ]
    )
    graph = build_offline_graph(model, [failing.build()])

    events = run(collect(graph))

    assert [type(event).__name__ for event in events][-1] == "AgentDoneEvent"
    assert model.call_count == 2


def test_model_failure_is_retried_then_ends_with_a_classified_error() -> None:
    """主模型重试满次数后以错误收尾，整个过程里没有第二个上游被调用。

    失败换上游那条路已经删掉（见 ``docs/adr/0046-model-catalog-and-user-model-choice.md``），
    所以这里不再有第二个客户端可以数：一次提问打到那条上游的次数就是 ``1 + MODEL_RETRY_MAX``
    ——全部落在唯一的那个客户端上，证明重试确实试满了，而不是第一次失败就放弃。

    结尾必须是分类后的错误事件，不能是 ``done``：外面已经没有兜底再拦一道，
    ``ModelRetryMiddleware`` 的 ``on_failure`` 一旦写回默认的 ``"continue"``，它会自己造
    一条消息返回、运行以 ``done`` 收尾，这条断言就会红。若有人把失败换上游那条路加回来
    （哪怕只是为了「以后可能需要」），那次调用会产出一个回答，结尾同样不是错误事件。
    """

    primary = FailingChatModel(
        error=openai.APIConnectionError(request=httpx.Request("POST", "http://llm"))
    )
    graph = build_offline_graph(primary, [])

    events = run(collect(graph))

    assert primary.call_count == 1 + MODEL_RETRY_MAX
    assert isinstance(events[-1], AgentErrorEvent)
    assert events[-1].code == "llm_unavailable"
    assert events[-1].retryable is True


def test_tool_call_limit_stops_further_tool_use() -> None:
    """工具调用次数到顶后不再执行工具，但模型仍可作答。

    这道上限防的是「模型陷入检索循环」：没有它，一次运行可能无限调工具，把 token 和
    Qdrant 查询都烧光。``exit_behavior="continue"`` 的选择意味着到顶不是报错，而是让
    模型用已有材料收尾。
    """

    counting = CountingTool("search_documents", result="一条结果")
    # 脚本一直要求调工具，比上限多几次，确保上限而不是脚本决定了停止时机。
    model = ScriptedChatModel(
        responses=[
            *[tool_call_message("search_documents", {"text": f"q{i}"})
              for i in range(TOOL_CALL_RUN_LIMIT + 3)],
            AIMessage(content="收尾回答"),
        ]
    )
    graph = build_offline_graph(model, [counting.build()])

    run(collect(graph))

    assert len(counting.invocations) <= TOOL_CALL_RUN_LIMIT


def test_model_call_limit_ends_the_run() -> None:
    """模型调用次数到顶后运行结束，不无限循环。"""

    counting = CountingTool("search_documents", result="一条结果")
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_documents", {"text": f"q{i}"})
            for i in range(MODEL_CALL_RUN_LIMIT + 5)
        ]
    )
    graph = build_offline_graph(model, [counting.build()])

    events = run(collect(graph))

    assert model.call_count <= MODEL_CALL_RUN_LIMIT
    assert [type(event).__name__ for event in events][-1] == "AgentDoneEvent"


def test_every_run_stamps_its_identity_even_without_a_scope() -> None:
    """没有知识库范围时，提问行与助手行上同样要带运行标识。

    会话历史按提问行上的 ``run_id`` 切分，排空接手也按它找回那一轮（``runs.py`` 的
    ``_read_frozen_run_meta``：取不到 id 就当成「这一轮已经不在 checkpoint 里」放弃）。所以
    「本次运行没有范围」不能成为不盖标记的理由：按范围短路会让这类运行整轮认不出归属。
    范围是另一样东西，只有真有范围时才写进 ``agent_run``。
    """

    context = AgentContext(run_id=uuid4())
    thread_id = uuid4()
    model = ScriptedChatModel(responses=[AIMessage(content="回答")])
    graph = build_offline_graph(model)

    async def verify() -> list[Any]:
        async for _ in stream_agent_events(
            graph,
            message="问题",
            thread_id=thread_id,
            context=context,
            langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
        ):
            pass
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
        return snapshot.values["messages"]

    messages = run(verify())
    question = next(message for message in messages if isinstance(message, HumanMessage))
    answer = next(message for message in messages if isinstance(message, AIMessage))

    assert question.additional_kwargs["agent_run"] == {"run_id": str(context.run_id)}
    assert answer.additional_kwargs["agent_run"] == {
        "run_id": str(context.run_id),
        "completed": True,
    }


def test_the_frozen_run_metadata_carries_the_model_with_its_name_and_window() -> None:
    """当轮选定的模型连展示名与上下文窗口一起冻结进提问消息的运行元数据。

    快照必须**自足**：条目后来改名或停用不改写已经发生过的那几轮，而接手续跑那一轮也不许
    回查目录。所以这里钉的是完整的键名与键值——只有展示名或只有窗口都是不够的。
    """

    model = ResolvedLlmModel(
        id=UUID("40000000-0000-4000-8000-0000000000a1"),
        display_name="演示模型",
        context_window=32768,
    )
    context = AgentContext(run_id=uuid4(), llm_model=model)
    thread_id = uuid4()
    graph = build_offline_graph(ScriptedChatModel(responses=[AIMessage(content="回答")]))

    async def verify() -> HumanMessage:
        async for _ in stream_agent_events(
            graph,
            message="问题",
            thread_id=thread_id,
            context=context,
            langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
        ):
            pass
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
        return next(
            message
            for message in snapshot.values["messages"]
            if isinstance(message, HumanMessage)
        )

    question = run(verify())

    assert question.additional_kwargs["agent_run"] == {
        "run_id": str(context.run_id),
        "llm_model": {
            "id": str(model.id),
            "display_name": "演示模型",
            "context_window": 32768,
        },
    }


# 历史那一轮与本次运行各自用的标识，取值互不干扰，便于按原文反查是哪一个漏了出去。
HISTORICAL_CITATION = "E111111111111"
CURRENT_CITATION = "E222222222222"


def run_second_turn_after_a_citing_history(model: ScriptedChatModel, tool: CountingTool) -> list[Any]:
    """在一轮带标识的历史之后跑第二轮，返回收尾后的 checkpoint 消息。

    历史直接铺在 checkpoint 上（与 ``test_agent_evidence_scope.py`` 里那条换范围用例同一
    手法）：要断言的就是「上一轮留下的东西」在这一次运行里被怎么送出去，所以历史必须真的
    在 state 里，而不是拼在请求里。历史头部放一条摘要伪提问，顺带证明它走的是同一条路——
    它也在本次提问之前，照常按历史处理，没有任何特例分支。第二轮自己调一次工具，结果里带
    着本轮的标识——它是模型唯一能照抄的东西，用来钉「本次的一个字不动」。

    Args:
        model: 主模型（假的），脚本是「先调工具、再问答」。
        tool: 本次运行要调的那个假工具，结果里带本轮标识。

    Returns:
        checkpoint 里的消息，供断言存档原文没被动过。模型收到的消息从
        ``model.received_messages`` 读。

    Notes:
        不联网：假模型、假工具、``InMemorySaver``。
    """

    graph = build_offline_graph(model, [tool.build()])
    thread_id = uuid4()

    async def verify() -> list[Any]:
        await graph.aupdate_state(
            {"configurable": {"thread_id": str(thread_id)}},
            {"messages": [
                # 摘要那条伪提问也在本次提问之前，照常当历史处理，不为它开特例。
                HumanMessage(content="更早那段聊过的旧摘要", additional_kwargs={"lc_source": "summarization"}),
                HumanMessage(content="第一问"),
                parallel_tool_call_message([("call-history", "search_documents", {"text": "旧查询"})]),
                ToolMessage(content=f"旧资料 [[{HISTORICAL_CITATION}]]", tool_call_id="call-history", name="search_documents"),
                AIMessage(content=f"旧回答 [[{HISTORICAL_CITATION}]]"),
            ]},
        )
        async for _ in stream_agent_events(
            graph,
            message="第二问",
            thread_id=thread_id,
            context=AgentContext(scope=NEWS_SCOPE),
            langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
        ):
            pass
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
        return snapshot.values["messages"]

    return run(verify())


def test_history_citations_are_stripped_but_this_runs_are_not() -> None:
    """送给模型的历史里旧标识换成失效说明，本次运行自己的标识一字不动。

    模型会照抄它在上下文里看到的标识，抄进新回答就是一批解析不出来的非法引用（见
    ``docs/adr/0045-model-sees-whole-session-history.md``）。界与压缩守卫一致：本次提问
    之前的是历史（历史回答、历史工具结果都要剥），本次运行自己产出的工具结果一个字不动
    ——它是模型唯一能拄的东西。断言落在模型实际收到的消息上，不去看中间件的内部状态。
    """

    model = ScriptedChatModel(responses=[
        tool_call_message("search_documents", {"text": "本次查询"}),
        AIMessage(content=f"本次回答 [[{CURRENT_CITATION}]]"),
    ])
    tool = CountingTool("search_documents", result=f"本次资料 [[{CURRENT_CITATION}]]")

    run_second_turn_after_a_citing_history(model, tool)

    sent = "\n".join(message.text for message in model.received_messages[-1])
    assert "旧资料 [出处已失效]" in sent
    assert "旧回答 [出处已失效]" in sent
    # 旧标识一个都不该剩下，否则模型照样能把它抄进回答。
    assert HISTORICAL_CITATION not in sent
    assert f"本次资料 [[{CURRENT_CITATION}]]" in sent

def test_stripping_the_sent_copy_leaves_the_checkpoint_text_unchanged() -> None:
    """剥离只发生在发送副本上，checkpoint 里的原文一字不动。

    ``request.messages`` 里的消息对象与 graph state 里的是同一批，就地改
    ``message.content`` 是最容易写错的那条路——改完连用户回看那一轮时都把当年的引用点不开
    了（回放是拿那一轮的答案文本对它自己的证据去解析的）。所以这里从 checkpoint 读回原文，
    并反向断言剥离后的说明没有渗进去。
    """

    model = ScriptedChatModel(responses=[
        tool_call_message("search_documents", {"text": "本次查询"}),
        AIMessage(content=f"本次回答 [[{CURRENT_CITATION}]]"),
    ])
    tool = CountingTool("search_documents", result=f"本次资料 [[{CURRENT_CITATION}]]")

    stored = run_second_turn_after_a_citing_history(model, tool)

    texts = [message.text for message in stored]
    assert f"旧资料 [[{HISTORICAL_CITATION}]]" in texts
    assert f"旧回答 [[{HISTORICAL_CITATION}]]" in texts
    assert f"本次资料 [[{CURRENT_CITATION}]]" in texts
    assert not any("[出处已失效]" in text for text in texts)


def test_summarization_waits_for_the_next_question_even_without_a_scope() -> None:
    """无知识库范围的会话，运行中途同样不压缩——守卫不再按范围短路。

    它防的是「这一轮刚取得的 Tool 内容被压成摘要」，而那与本次运行有没有范围无关。用摘要
    模型被调了几次来观察：同一份越过触发线的历史，收尾是本次运行的工具结果时不调（不压缩），
    换成新提问才调（那正是压缩该发生的时刻）。只断言前一半不足以成钉——摘要模型一次不调
    也可能是阈值根本没触发的空转，所以后一半必须同时在。
    """

    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])
    middleware = summarization_middleware(summarizer)
    history = old_history()
    question = HumanMessage(content="本次提问")
    call = tool_call_message("search_documents", {"text": "查"})
    result = ToolMessage(content="本次资料", tool_call_id="call-search_documents", name="search_documents")
    window = window_that_triggers([*history, question])

    async def verify() -> tuple[Any, Any, int]:
        # 守卫已不读 ``runtime.context``；给的仍是一个无范围的上下文，钉住「没有范围也一样挡」。
        runtime = SimpleNamespace(context=AgentContext())
        # 窗口显式放进上下文变量：这里直接调中间件、不经过流入口，没有当轮窗口可跟
        # （回落那一支见 ``_get_profile_limits`` 的说明）。
        token = bind_context_window(window)
        try:
            mid_run = await middleware.abefore_model({"messages": [*history, question, call, result]}, runtime)
            at_question = await middleware.abefore_model({"messages": [*history, question]}, runtime)
        finally:
            unbind_context_window(token)
        return mid_run, at_question, summarizer.call_count

    mid_run, at_question, calls = run(verify())

    assert mid_run is None
    assert at_question is not None
    assert calls == 1


# 直接调中间件的用例共用的历史规模：20 轮一问一答，每条正文约 100 字符（≈30 个近似 token），
# 合计约 1200 token。下面各条断言的关系都按这个量级选窗口，留出一倍以上余量。
_OLD_TURNS = 20
_OLD_TEXT = "内容" * 48


def old_history() -> list[Any]:
    """造一段旧历史：20 轮一问一答，每条长度固定。"""

    return [
        message
        for index in range(_OLD_TURNS)
        for message in (
            HumanMessage(content=f"旧问{index}：{_OLD_TEXT}"),
            AIMessage(content=f"旧答{index}：{_OLD_TEXT}"),
        )
    ]


def summarization_middleware(summarizer: ScriptedChatModel) -> RunSafeSummarizationMiddleware:
    """按生产口径（当轮窗口占比）造一个摘要中间件，摘要模型用给的假模型。"""

    return RunSafeSummarizationMiddleware(
        model=summarizer,
        trigger=("fraction", SUMMARIZATION_TRIGGER_FRACTION),
        keep=("fraction", SUMMARIZATION_KEEP_FRACTION),
        summary_prompt=SUMMARY_PROMPT,
    )


def summarize_with_window(middleware: Any, messages: list[Any], window: int | None) -> Any:
    """把窗口放进上下文变量后调一次 ``abefore_model``，返回它的状态改写。

    调用前把窗口 ``set`` 进去、调用后复位：不复位的话同一条用例里后面的调用会接着用旧值。
    ``window`` 为 ``None`` 的写法就是「这一轮没有窗口可跟」，用来钉回落那一支。
    """

    async def verify() -> Any:
        token = bind_context_window(window)
        try:
            # 守卫不读 ``runtime.context``，给一个空的（与生产一致的那条路另有用例在走）。
            return await middleware.abefore_model(
                {"messages": messages}, SimpleNamespace(context=AgentContext())
            )
        finally:
            unbind_context_window(token)

    return run(verify())


def test_compression_triggers_at_eighty_percent_of_the_model_window() -> None:
    """触发线是当轮模型窗口的 80%：刚好装得下就压缩，窗口再大一点就不压缩。

    第二半是同一条断言的一半：只断言「触发」的话，一个「永远压缩」的实现也会通过；只断言
    「不触发」同样漏掉另一边。窗口值的取法靠 ``window_that_triggers``（比它大 5 个 token
    就不该触发）——而那正好钉住了 80% 这个系数：换成别的系数，翻转点就不在这里。
    """

    history = [*old_history(), HumanMessage(content="本次提问")]
    window = window_that_triggers(history)
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])

    at_threshold = summarize_with_window(summarization_middleware(summarizer), history, window)

    assert at_threshold is not None
    assert summarizer.call_count == 1, "窗口的 80% 刚好落在历史长度上，必须触发"

    wider_summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])
    wider = summarize_with_window(summarization_middleware(wider_summarizer), history, window + 5)

    assert wider is None
    assert wider_summarizer.call_count == 0, "窗口再大一点就不该触发"


def test_compression_keeps_thirty_percent_of_the_model_window() -> None:
    """压缩后保留的那一段按窗口的 30% 定，不是按条数。

    两侧都断言：保留段自己装得进 30%（切点还会回退到一轮提问处，所以允许多一条），而再多留
    一条就超了——只断言前者的话，一个「几乎不折」的实现也会通过。
    """

    history = [*old_history(), HumanMessage(content="本次提问")]
    window = window_that_triggers(history)
    target = int(window * SUMMARIZATION_KEEP_FRACTION)
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])

    result = summarize_with_window(summarization_middleware(summarizer), history, window)

    assert summarizer.call_count == 1
    # 上游的重写形状是「清空整个列表 + 摘要 + 保留段」，摘要之后的就是保留段。
    preserved = result["messages"][2:]
    assert preserved == history[-len(preserved):]
    one_message = max(count_tokens_approximately([message]) for message in history)
    assert count_tokens_approximately(preserved) <= target + one_message
    assert count_tokens_approximately(history[-len(preserved) - 1:]) > target


def test_the_trigger_follows_the_runtime_window_not_the_client_profile() -> None:
    """构造期那个占位值换成别的数不影响触发点，只有运行期那一轮的窗口才决定它。

    三个取值要同时成立，用例才有证伪力：同一个运行期窗口下，客户端上的值换成 1000 或
    32768（差了一个数量级）都应触发；而只把运行期窗口调大就不触发。反过来，一个「按客户端
    上那个值算」的实现在这两侧都过不去——1000 的阈值（800）低于历史长度、32768 的阈值
    （26214）高于它，所以它会在第一侧不触发、在第三侧反而触发。
    """

    history = [*old_history(), HumanMessage(content="本次提问")]
    tokens = count_tokens_approximately(history)
    # 历史长度必须夹在两个「按客户端值算」的阈值之间，上面那段证伪力才成立。
    assert 800 < tokens < 26214

    def calls(*, runtime_window: int, client_window: int) -> int:
        summarizer = ScriptedChatModel(
            responses=[AIMessage(content="摘要")],
            profile={"max_input_tokens": client_window},
        )
        result = summarize_with_window(
            summarization_middleware(summarizer), history, runtime_window
        )
        assert (result is not None) == (summarizer.call_count == 1)
        return summarizer.call_count

    assert calls(runtime_window=200, client_window=1000) == 1
    assert calls(runtime_window=200, client_window=32768) == 1
    assert calls(runtime_window=100000, client_window=1000) == 0


def test_a_missing_runtime_window_falls_back_to_the_client_profile() -> None:
    """取不到当轮窗口时回落到构造期那个值，而不是「比例条件不满足」。

    不经过流入口的调用点（直接调中间件、``AgentContext`` 里没有模型的那种）本来就没有当轮
    窗口可跟，回落让它们的口径回到客户端上那个窗口。返回 ``None`` 则会被上游判成「比例条件
    不满足」，于是压缩从此静默失效（不报错、也不压缩）——那比回落坏得多，所以这一支要单独钉。
    """

    history = [*old_history(), HumanMessage(content="本次提问")]
    summarizer = ScriptedChatModel(
        responses=[AIMessage(content="摘要")],
        profile={"max_input_tokens": 200},
    )

    result = summarize_with_window(summarization_middleware(summarizer), history, None)

    assert result is not None
    assert summarizer.call_count == 1, "没有当轮窗口时应当回落到客户端上的值，而不是彻底不压缩"


# ---- 摘要那一次调用的输入与提示词 ----
#
# 摘要是模型对被折掉那一段的唯一记忆，所以「送去生成摘要的输入有多长」跟「压缩后保留多少」
# 一样是行为，不是实现细节。上游默认只把被折掉那一段的**末尾** 4000 token 送去摘要
# （``trim_tokens_to_summarize``），超过它时最早的那几条消息永远进不了摘要。

# 放在最前面的独特标记：旧行为下它落在 4000 token 截断线之外，必定被丢掉。
_SUMMARY_HEAD_MARK = "被折掉那一段最前面的标记 OLD-HEAD-9527"
_SUMMARY_FILLER = "内容" * 250


def summarization_middleware_from_production(
    summarizer: ScriptedChatModel,
) -> RunSafeSummarizationMiddleware:
    """从 ``build_agent_middleware`` 里取出那个摘要中间件。

    刻意不复用上面那个直调构造的 ``summarization_middleware``（它要把调用次数数在传进去的那
    个假模型上，只能直造）：构造参数一旦分开写两处，生产漏传 ``trim_tokens_to_summarize=None``
    时测试里那份照样按老参数跑，用例就不会红——而这里要测的正是**生产构造时传了什么**。

    代价是模型被 ``_with_placeholder_window`` 拷了一份，摘要那次调用落在副本上，读提示词要
    从 ``middleware.model`` 上读，不能读传进来的那一个。
    """

    return next(
        middleware
        for middleware in build_agent_middleware(summarization_model=summarizer)
        if isinstance(middleware, RunSafeSummarizationMiddleware)
    )


def long_history_for_summary() -> list[Any]:
    """一段够长的旧历史：最前面一条带独特标记，后面全是等长填充。"""

    return [
        HumanMessage(content=_SUMMARY_HEAD_MARK),
        *[
            message
            for index in range(40)
            for message in (
                HumanMessage(content=f"旧问{index}：{_SUMMARY_FILLER}"),
                AIMessage(content=f"旧答{index}：{_SUMMARY_FILLER}"),
            )
        ],
    ]


def test_the_summary_call_sees_the_beginning_of_the_dropped_history() -> None:
    """摘要那次调用看到的是被折掉那一段的完整内容，不只是它的末尾一截。

    标记放在被折掉那一段的**最前面**，是这条用例的证伪力所在：上游默认按「末尾 4000 token」
    裁剪（``trim_tokens_to_summarize`` 的默认值），被折掉的那段比它长时，标记必定落在裁剪
    之外——把 ``trim_tokens_to_summarize=None`` 从构造处去掉，这条立刻变红。

    两半必须同时断言：只看「提示词里有标记」的话，一段没触发压缩的历史也能让标记出现在
    别处；只看「触发过」的话，截断与否根本看不出来。
    """

    history = [*long_history_for_summary(), HumanMessage(content="本次提问")]
    # 前提：被折掉的那一段必须长过上游那条 4000 token 截断线，否则裁剪根本不会发生、这条
    # 用例会退化成空转。按「触发 80%、保留 30%」推算：窗口 ≈ 历史 ÷ 0.8，保留段 ≈ 窗口 × 0.3
    # ≈ 历史的 37.5%，于是被折掉的是历史的约 62.5%——历史超过 6400 token 就够，这里留余量。
    assert count_tokens_approximately(history) > 8000
    window = window_that_triggers(history)
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])
    middleware = summarization_middleware_from_production(summarizer)

    result = summarize_with_window(middleware, history, window)

    # 1、压缩真的发生了。
    assert result["messages"][1].additional_kwargs.get("lc_source") == "summarization"
    # 2、标记确实落在被折掉那一段里（保留段里没有它），所以它出现在提示词里只可能是
    #    「被折掉的那段被整段送去了」。
    preserved = "\n".join(message.text for message in result["messages"][2:])
    assert _SUMMARY_HEAD_MARK not in preserved
    # 3、摘要模型收到的提示词里有它（从副本上读，理由见上面那个构造助手）。
    prompt = middleware.model.received_messages[0][0].content
    assert _SUMMARY_HEAD_MARK in prompt
    # 4、而且是被折掉的那一段被**整段**送去了：那一段里每一条的正文都在提示词里——只送
    #    队首不送队尾（或反过来）是另一种错，这一条把它一起挡住。保留段是历史的后缀
    #    （形状见 ``test_compression_keeps_thirty_percent_of_the_model_window``），据此
    #    能算出被折掉的是前面哪几条。
    dropped = history[: len(history) - (len(result["messages"]) - 2)]
    assert [message.text for message in dropped if message.text not in prompt] == []


def test_the_summary_prompt_asks_for_cleared_tool_results_to_be_left_out() -> None:
    """摘要提示词补了「已清理的工具结果不必提及」，而 ``<messages>`` 块一个字没动。

    ``<messages>`` 标记与 ``{messages}`` 占位符是上游写明的**公开契约**（
    ``langchain/agents/middleware/summarization.py`` 里那句 “part of this constant's
    public contract, not just cosmetic formatting”）：下游靠 ``str.replace`` 在
    ``<messages>`` 之前插自己的指令块，改了标记就整条链断掉。所以新句子只能加在它**之前**
    的那段文字里，这条用例同时钉住「加了」和「契约没动」。

    正文只剩占位文字的工具结果写进摘要不携带任何信息，还会挤掉真正的历史——摘要模型的输入
    不再截断之后，这类条目反而变多了，所以这句要跟着进来。
    """

    statement = "正文已清理、只剩占位文字的工具结果不必在摘要里提及。"

    assert statement in SUMMARY_PROMPT
    assert SUMMARY_PROMPT.index(statement) < SUMMARY_PROMPT.index("<messages>")
    assert SUMMARY_PROMPT.endswith("<messages>\n{messages}\n</messages>")


# ---- 先清后压：压缩触发后先清旧的工具正文，清够了就不调摘要模型 ----
#
# 被清的是**模型那一侧**看到的旧工具正文：保留消息、保留工具调用与结果的配对，只把正文换成
# 占位文字。用户看到的那一份在会话历史业务表里、永不清理，所以下面这些用例断言的都是「模型
# 收到了什么」，不去读中间件的内部状态。
#
# 几条用例共用同一套夹具：一条长度必定超过「头 + 尾」的工具正文，头尾用同一个标记铺满、中段用
# 另一个字符铺满——「头 512 / 尾 256 / 中段没了」三件事因此各自都能认出来。
_PRUNE_MARK = "甲"
_KEEP_MARK = "乙"


def tool_body(mark: str) -> str:
    """一条长约 2800 字符的工具正文：首尾是标记，中间是将来会被清掉的那一段。"""

    return mark * PRUNED_TOOL_RESULT_HEAD_CHARS + "中" * 2000 + mark * PRUNED_TOOL_RESULT_TAIL_CHARS


def pruned_body(mark: str) -> str:
    """``tool_body`` 被清过之后的形状：头 512 字符、一句占位文字、尾 256 字符。

    这三个值刻意写死在用例里、**不引用那三个常量**：占位文字会被用户看到（被清掉的轮次在界面
    上只剩它），写死才能在常量被顺手改掉时变红；跟着常量算出来的期望值等于没断言。
    """

    return mark * 512 + "[... tool result middle pruned ...]" + mark * 256


def old_turn_with_tools(bodies: list[str]) -> list[Any]:
    """旧的一轮：旧提问、一次发起多个工具调用、逐个结果、旧回答。

    多个调用放在同一条助手消息里，每个结果各带自己的 ``tool_call_id``——配对没被清理拆散时
    看得出来，被拆散了也看得出来。
    """

    return [
        HumanMessage(content="旧提问"),
        parallel_tool_call_message([
            (f"old-{index}", "search_documents", {"query": f"旧查询{index}"})
            for index in range(len(bodies))
        ]),
        *[
            ToolMessage(content=body, tool_call_id=f"old-{index}", name="search_documents")
            for index, body in enumerate(bodies)
        ],
        AIMessage(content="旧回答"),
    ]


def tool_bodies(messages: list[Any]) -> list[Any]:
    """一串消息里所有工具结果的正文，按出现顺序。"""

    return [message.content for message in messages if isinstance(message, ToolMessage)]


def test_clearing_old_tool_bodies_is_enough_to_skip_the_summarization_call() -> None:
    """清够（计量降到触发线以下）时不调摘要模型，而写回状态的旧工具正文已是占位文字。

    两条断言必须同时成立：只断「摘要模型没被调」的话，阈值压根没触发的一次空转也会通过；
    只断「正文被清了」的话，一个清完照样调摘要模型的实现也会通过。
    """

    history = [
        *old_history(),
        *old_turn_with_tools([tool_body(_PRUNE_MARK)]),
        HumanMessage(content="本次提问"),
    ]
    window = window_that_triggers(history)
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])
    # 前提：清之前确实越过了触发线。少了这一步，这段历史短到不触发时用例会变成空转。
    assert count_tokens_approximately(history) >= int(window * SUMMARIZATION_TRIGGER_FRACTION)

    result = summarize_with_window(summarization_middleware(summarizer), history, window)

    assert summarizer.call_count == 0, "清够之后不该再花那次摘要调用"
    assert result is not None, "清理过的消息必须写回状态，不能当成没发生"
    messages = result["messages"]
    # 重建形状是「清空整个列表 + 写回」，所以去掉那一句之后消息一条不少、顺序不变。
    assert [type(message) for message in messages[1:]] == [type(message) for message in history]
    assert tool_bodies(messages) == [pruned_body(_PRUNE_MARK)]
    assert count_tokens_approximately(messages[1:]) < int(window * SUMMARIZATION_TRIGGER_FRACTION)


def test_clearing_goes_from_the_oldest_and_stops_once_it_is_enough() -> None:
    """从最旧的开始清、清到够用就停：更新的那条工具正文一个字不动。

    两条正文都足够长（各自都会被清），所以「新的没被动过」只能是顺序造成的——反过来从最新
    往旧清的实现会把第二条清掉、留着第一条，这条会红。
    """

    history = [
        *old_history(),
        *old_turn_with_tools([tool_body(_PRUNE_MARK), tool_body(_KEEP_MARK)]),
        HumanMessage(content="本次提问"),
    ]
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])

    result = summarize_with_window(
        summarization_middleware(summarizer), history, window_that_triggers(history)
    )

    assert summarizer.call_count == 0
    assert result is not None
    assert tool_bodies(result["messages"]) == [pruned_body(_PRUNE_MARK), tool_body(_KEEP_MARK)]


def test_clearing_not_enough_still_summarizes_from_the_cleared_measurement() -> None:
    """清完仍然超线才走摘要，而摘要的输入已经是清理之后的正文。

    这是「先清 → 再计量 → 最后才决定要不要摘要」整条链的用例：窗口取成触发线的一半（比这份
    历史小得多），清掉一条长正文远不够，于是走父类压缩；而摘要模型收到的那份提示词里，那条旧
    工具正文已经是占位文字。

    「清理写回 state 而不是只改发送副本」就必须在这条上体现：父类在 ``before_model`` 里按
    state 里的消息取摘要输入，只有清理真的写回了 state，摘要的输入才会是占位文字。
    """

    history = [
        *old_turn_with_tools([tool_body(_PRUNE_MARK)]),
        *old_history(),
        HumanMessage(content="本次提问"),
    ]
    window = window_that_triggers(history) // 2
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])

    result = summarize_with_window(summarization_middleware(summarizer), history, window)

    assert summarizer.call_count == 1, "清完仍然超线，必须走摘要"
    # 上游的重写形状：索引 0 是清空整个列表那条，索引 1 是摘要伪提问。
    assert result["messages"][1].additional_kwargs.get("lc_source") == "summarization"
    prompt = summarizer.received_messages[0][0].content
    assert PRUNED_TOOL_RESULT_PLACEHOLDER in prompt
    assert "中" * 2000 not in prompt, "摘要的输入必须是清理之后的正文"


def test_short_tool_bodies_are_left_exactly_as_they_were() -> None:
    """正文长度不超过「头 + 尾」之和时原样保留，不清理。

    工具失败时返回的几十字安全文案属于这一类：给它拼上占位文字反而把短的拼长了，而那几句正是
    模型唯一能看到失败原因的地方。
    """

    failure_notice = "工具调用失败：暂时不支持这种检索。"
    history = [
        *old_history(),
        *old_turn_with_tools([failure_notice, tool_body(_PRUNE_MARK)]),
        HumanMessage(content="本次提问"),
    ]
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])

    result = summarize_with_window(
        summarization_middleware(summarizer), history, window_that_triggers(history)
    )

    assert result is not None
    assert tool_bodies(result["messages"]) == [failure_notice, pruned_body(_PRUNE_MARK)]


def test_the_current_run_tool_results_are_never_cleared() -> None:
    """本轮自己取到的工具结果一字不动：运行中的守卫排在清理之前。

    守卫要求「本次提问就是最后一条消息」，所以命中守卫时不仅不压缩，连清理也不做——能被清的
    只能是历史里的正文。这里要单独断言「正文没被就地改过」：只断 ``result is None`` 的话，
    一个先就地清一轮再返回 ``None`` 的实现照样通过，而那次改动会留在 checkpoint 里。
    """

    history = [*old_history(), *old_turn_with_tools([tool_body(_PRUNE_MARK)])]
    current = [
        HumanMessage(content="本次提问"),
        parallel_tool_call_message([("now-0", "search_documents", {"query": "本次查询"})]),
        ToolMessage(content=tool_body(_KEEP_MARK), tool_call_id="now-0", name="search_documents"),
    ]
    messages = [*history, *current]
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])

    result = summarize_with_window(
        summarization_middleware(summarizer), messages, window_that_triggers(messages)
    )

    assert result is None
    assert summarizer.call_count == 0
    assert current[-1].content == tool_body(_KEEP_MARK), "本轮取到的证据不能被清"
    assert history[-2].content == tool_body(_PRUNE_MARK), "守卫命中时历史那段也一动不动"


def test_the_model_receives_the_cleared_body_and_the_citation_still_resolves() -> None:
    """端到端：模型收到的旧工具正文已是占位文字，而引用证据与配对照旧。

    「清理写回 checkpoint」这件事只有真图才看得出来：中间件返回的状态改写要由图的消息归约器
    接住、再由下一次模型调用读出来。而「占位文字真的送到了模型」同时证明阈值确实触发了——少了
    这一半，一个从不清理也从不压缩的实现也能通过下面那条「没有摘要」。引用核验读的是工具结果上
    的 ``artifact``，清理只动正文，所以回放那一步仍然能解析出当时那条引用。
    """

    item = DocumentEvidence(
        document_id=uuid4(),
        knowledge_base_id=NEWS_SCOPE.knowledge_base_ids[0],
        knowledge_base_name="新闻",
        title="运行手册",
        content_hash="a" * 64,
        kind="match",
    )
    context = AgentContext(scope=NEWS_SCOPE)
    artifact = ToolEvidence(
        run_id=context.run_id, scope=NEWS_SCOPE, evidence=(item,)
    ).model_dump(mode="json")
    history = [
        HumanMessage(
            content="上一问",
            additional_kwargs={
                "agent_run": {
                    "run_id": str(context.run_id),
                    "scope": context.scope.model_dump(mode="json"),
                }
            },
        ),
        parallel_tool_call_message([("old-0", "search_documents", {"query": "旧查询"})]),
        ToolMessage(
            content=tool_body(_PRUNE_MARK),
            tool_call_id="old-0",
            name="search_documents",
            artifact=artifact,
        ),
        AIMessage(
            content=f"上一答 [[{item.citation_id}]]",
            additional_kwargs={"agent_run": {"run_id": str(context.run_id), "completed": True}},
        ),
        *old_history(),
    ]
    model = ScriptedChatModel(responses=[AIMessage(content="当前答案")])
    graph = build_offline_graph(model)
    config = {"configurable": {"thread_id": str(uuid4())}}
    window = window_that_triggers(history)

    async def drive() -> list[Any]:
        token = bind_context_window(window)
        try:
            await graph.aupdate_state(config, {"messages": history})
            await graph.ainvoke({"messages": [HumanMessage(content="本次提问")]}, config=config)
        finally:
            unbind_context_window(token)
        snapshot = await graph.aget_state(config)
        return list(snapshot.values["messages"])

    messages = run(drive())

    # 1、模型这一侧收到的就是清过的那条正文——阈值真的触发了，占位文字也真的送到了。
    assert tool_bodies(list(model.received_messages[-1])) == [pruned_body(_PRUNE_MARK)]
    # 2、而这一轮没有走摘要那条路：状态里没有摘要伪提问。摘要模型**一次都没被调**那个确切
    #    计数只有直调中间件那条用例给得出——经图跑时中间件为了补占位窗口会 ``model_copy``
    #    一份客户端，摘要那次调用落在副本上、数不到。
    assert not [
        message
        for message in messages
        if getattr(message, "additional_kwargs", {}).get("lc_source") == "summarization"
    ], "清够了就不该留下摘要"
    # 3、工具调用与结果仍然成对（请求没被上游拒），引用证据与正文之外的字段一字未动。
    call_message = next(
        message for message in messages if isinstance(message, AIMessage) and message.tool_calls
    )
    stored = next(message for message in messages if isinstance(message, ToolMessage))
    assert call_message.tool_calls[0]["id"] == stored.tool_call_id == "old-0"
    assert messages.index(call_message) < messages.index(stored)
    assert stored.artifact == artifact
    assert messages[-1].content == "当前答案"
    # 4、回放那一步的引用核验照旧：证据仍然挂在工具结果上，标识仍然解析得出来。
    turns, _, _ = build_replay_turns(messages)
    assert next(turn for turn in turns if turn.question == "上一问").citations == (item,)


# ---- 摘要消息记下的两样：分界标记与产生它的运行标识 ----
#
# 两个字段都只能在压缩发生的那一刻记下，事后推不出来：``memory_boundary_run_id``（被折掉那
# 一段里最后一次提问的运行标识）是界面上那条分界线的唯一来源，``produced_by_run_id``（产生
# 这条摘要的那次运行）让会话历史表认得出「这一行是不是本次刚产生的」。所以它们的正确性只能
# 靠断言发现。


def question_with_run(text: str, run_id: UUID) -> HumanMessage:
    """一条带上运行标识的提问——分界标记就是从这个字段取的。"""

    return HumanMessage(content=text, additional_kwargs={"agent_run": {"run_id": str(run_id)}})


def is_question(message: Any) -> bool:
    """一条消息是不是用户提问（摘要那条伪提问也是 ``HumanMessage``，要排掉）。"""

    return isinstance(message, HumanMessage) and message.additional_kwargs.get("lc_source") != "summarization"


def history_with_runs(question_run_ids: list[UUID]) -> list[Any]:
    """一问一答的旧历史，每条提问都带着自己的运行标识。"""

    return [
        message
        for index, run_id in enumerate(question_run_ids)
        for message in (
            question_with_run(f"旧问{index}：{_OLD_TEXT}", run_id),
            AIMessage(content=f"旧答{index}：{_OLD_TEXT}"),
        )
    ]


def summary_of(result: Any) -> Any:
    """从 ``abefore_model`` 的改写里取出摘要那条消息（上游的形状是「清空 + 摘要 + 保留段」）。"""

    return result["messages"][1]


def dropped_segment(messages: list[Any], result: Any) -> list[Any]:
    """被折掉的那一段：交给中间件的消息去掉保留段（保留段是输入的后缀）。"""

    preserved = len(result["messages"]) - 2
    return messages[: len(messages) - preserved]


def test_the_summary_records_the_last_question_of_the_dropped_segment() -> None:
    """分界标记取的是被折掉那一段里**最后一次**提问的运行标识，不是第一次、也不是本次。

    三种取法（取第一次、取最后一次、取本次运行）事后看都是一串像 run_id 的字符，分不开；
    所以段里必须埋上多个提问，而且本次提问要另有一个标识——否则这条用例两边都会通过。
    """

    question_run_ids = [uuid4() for _ in range(8)]
    history = history_with_runs(question_run_ids)
    current_run_id = uuid4()
    messages = [*history, question_with_run("本次提问", current_run_id)]
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])

    result = summarize_with_window(
        summarization_middleware(summarizer), messages, window_that_triggers(messages)
    )

    assert summarizer.call_count == 1, "这份历史必须真的触发压缩，否则没有摘要消息可断言"
    dropped = dropped_segment(messages, result)
    dropped_questions = [message for message in dropped if is_question(message)]
    assert len(dropped_questions) >= 2, "段里要有多个提问，「取最后一个」与「取第一个」才分得开"

    boundary = summary_of(result).additional_kwargs["memory_boundary_run_id"]

    assert boundary == dropped_questions[-1].additional_kwargs["agent_run"]["run_id"]
    assert boundary != dropped_questions[0].additional_kwargs["agent_run"]["run_id"], "不能取段里第一个提问"
    assert boundary != str(current_run_id), "不能取本次运行的标识"


def test_a_second_compression_without_a_question_inherits_the_previous_boundary() -> None:
    """第二次压缩时被折掉那一段里一条提问都没有：沿用上一条摘要记的分界，不记空。

    保留段刚好从「摘要之后的第一条提问」起时就是这个形状：段里只剩上一条摘要、它的回答和
    取过的工具结果。这条痕迹事后推不出来，所以三样要同时断言：等于上一条摘要的值、不为空、
    也不是本次运行的标识。窗口刻意取得远低于触发线，让保留段连上一条消息都装不下。
    """

    previous_boundary = uuid4()
    previous_summary = HumanMessage(
        content="Here is a summary of the conversation to date: 更早那段聊过的旧背景",
        additional_kwargs={
            "lc_source": "summarization",
            "memory_boundary_run_id": str(previous_boundary),
            "produced_by_run_id": str(uuid4()),
        },
    )
    messages = [
        previous_summary,
        parallel_tool_call_message([("call-old", "search_documents", {"text": "旧查询"})]),
        ToolMessage(content="上一轮取到的资料", tool_call_id="call-old", name="search_documents"),
        AIMessage(content=f"上一答：{_OLD_TEXT}"),
        question_with_run("本次提问", uuid4()),
    ]
    summarizer = ScriptedChatModel(responses=[AIMessage(content="摘要")])

    result = summarize_with_window(
        summarization_middleware(summarizer), messages, window_that_triggers(messages) // 8
    )

    assert summarizer.call_count == 1, "这份历史必须真的触发压缩，否则没有摘要消息可断言"
    dropped = dropped_segment(messages, result)
    assert previous_summary in dropped, "前提：上一条摘要在被折掉的那一段里"
    assert not [message for message in dropped if is_question(message)], "前提：这一段里一条提问都没有"

    kwargs = summary_of(result).additional_kwargs

    assert kwargs["memory_boundary_run_id"] == str(previous_boundary), "没有提问时必须沿用上一条摘要的分界，不能记空"
    assert kwargs["memory_boundary_run_id"] != kwargs["produced_by_run_id"], "不能把本次运行的标识当成沿用下来的分界"


def test_the_summary_records_the_run_that_produced_it() -> None:
    """``produced_by_run_id`` 是发起这次运行的那一次运行，经真实流水线一路取到运行上下文。

    走一遍「建图 → 图节点执行」而不是直接调中间件：这个字段只能从 ``runtime.context`` 来，
    接线断掉时直接调的写法照样能过。同时钉住它与分界标记是两个不同的值——把两者写成同一个
    来源（本次运行）在两边的断言里都会红。
    """

    question_run_ids = [uuid4() for _ in range(10)]
    history = history_with_runs(question_run_ids)
    question_text = "本次提问"
    window = window_that_triggers([*history, HumanMessage(content=question_text)]) // 2
    context = AgentContext(
        llm_model=ResolvedLlmModel(
            id=UUID("40000000-0000-4000-8000-0000000000a3"),
            display_name="演示模型",
            context_window=window,
        )
    )
    model = ScriptedChatModel(responses=[AIMessage(content=f"第 {index} 条") for index in range(1, 6)])
    graph = build_offline_graph(model)

    async def verify() -> list[Any]:
        thread_id = uuid4()
        config = {"configurable": {"thread_id": str(thread_id)}}
        await graph.aupdate_state(config, {"messages": history})
        async for _ in stream_agent_events(
            graph,
            message=question_text,
            thread_id=thread_id,
            context=context,
            langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
        ):
            pass
        snapshot = await graph.aget_state(config)
        return list(snapshot.values["messages"])

    messages = run(verify())

    # 1、压缩真的发生了，摘要就在状态头部（它一次压缩之后就一直留在那里）。
    assert messages[0].additional_kwargs.get("lc_source") == "summarization"
    # 2、产生它的就是本次运行。
    assert messages[0].additional_kwargs.get("produced_by_run_id") == str(context.run_id)
    # 3、分界取的是历史里那次提问的运行标识，与本次运行是两个不同的值。
    current_index = next(
        index for index, message in enumerate(messages) if getattr(message, "text", None) == question_text
    )
    preserved = messages[1:current_index]
    assert [message.text for message in preserved] == [
        message.text for message in history[len(history) - len(preserved):]
    ], "保留段必须是历史的后缀，否则下面算不出被折掉的是哪一段"
    dropped_questions = [
        message
        for message in history[: len(history) - len(preserved)]
        if is_question(message)
    ]
    assert len(dropped_questions) >= 2, "段里要有多个提问，「取最后一个」与「取第一个」才分得开"
    assert messages[0].additional_kwargs.get("memory_boundary_run_id") == (
        dropped_questions[-1].additional_kwargs["agent_run"]["run_id"]
    )
    assert messages[0].additional_kwargs.get("memory_boundary_run_id") != str(context.run_id)


# 提示词选择的全部输入形状。合成一条是因为它们都是「喂一个 context、断言选出哪份提示词」
# 的纯函数调用，逐个写成独立函数只是重复同一行断言。
#
# 各条的意义：
# - ``AgentContext()``：没给自定义值时回落到默认提示词。
# - 给了自定义值：按它走。这条是「进程级共享一个图」能成立的前提——提示词是每次运行读的，
#   不是编译期定死的，否则换提示词就得重新编译。
# - 空白值（空串、空格、换行制表符）：等同于「没给」。真用一份空提示词会让模型失去角色
#   约束和「必须引用 document_id」的要求，比回落到默认值糟得多。请求层已经把空白规整成
#   ``None``，这里是第二道防线。
# - ``None``：完全没有 context 也不能抛异常。``create_agent`` 允许不传 context 调用，那时
#   ``runtime.context`` 就是 ``None``。这条路径 HTTP 层走不到，但图是公共对象，CLI 或测试
#   可能直接调它。
#
# ``expected`` 是选出来的**基底**提示词，日期段由断言统一补上：日期对每条都一样，写进
# 参数表只是把同一段话抄六遍。日期本身怎么追加由下面几条单独测。
@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (AgentContext(), DEFAULT_SYSTEM_PROMPT),
        (AgentContext(system_prompt="你只说是或不是。"), "你只说是或不是。"),
        (AgentContext(system_prompt=""), DEFAULT_SYSTEM_PROMPT),
        (AgentContext(system_prompt="   "), DEFAULT_SYSTEM_PROMPT),
        (AgentContext(system_prompt="\n\t"), DEFAULT_SYSTEM_PROMPT),
        (None, DEFAULT_SYSTEM_PROMPT),
    ],
)
def test_prompt_selection_covers_every_context_shape(
    context: AgentContext | None,
    expected: str,
) -> None:
    """按上下文选出正确的那份系统提示词。"""

    today = date(2026, 3, 17)

    # 用 startswith 而不是相等：末尾还跟着一段无条件注入的证据规则（哪种上下文形状都拿到
    # 同一段，由下面那条单独钉），日期段则必须紧跟基底提示词。
    assert select_system_prompt(context, today=today).startswith(
        append_current_date(expected, today=today)
    )


def test_history_is_not_evidence_rule_is_injected_with_and_without_a_scope() -> None:
    """「历史内容不是本次证据」那一段与有没有知识库范围无关，两边都要有。

    撤销历史裁剪之后模型手里确实有历史回答、摘要和旧引用，这段约束是它们不能当证据用的唯一
    凭据（见 ADR 0045）；它原来嵌在「有范围」那一段里，无范围的运行拿不到。反向一并钉住：
    资料边界那一段仍然只在有范围时出现——它讲的是本次运行范围（工具的参数），无范围时
    没有可说的内容。
    """

    scoped = select_system_prompt(AgentContext(scope=NEWS_SCOPE))
    unscoped = select_system_prompt(AgentContext())

    for prompt in (scoped, unscoped):
        assert "能看到这个会话的完整往来" in prompt
        assert "但历史里的回答、摘要和引用都不是本次事实的依据" in prompt
        assert "本次的事实性结论必须来自本次工具返回的内容" in prompt
        assert "原样引用本次 Tool 给出的 [[E...]]" in prompt
        assert "[出处已失效]" in prompt
        # 旧描述与撤销裁剪后的事实矛盾（模型手里就是有这些内容），必须已经换掉。
        assert "历史问题只帮助理解意图" not in prompt

    assert "应用规定的资料边界" in scoped
    assert "应用规定的资料边界" not in unscoped
    assert NEWS_SCOPE.knowledge_bases[0].name in scoped


# 日期注入的三条契约。分开写是因为它们各自会被不同的改动破坏：追加位置、格式、以及
# 「自定义提示词那条路会不会漏掉这一步」。
def test_current_date_is_appended_not_replacing_the_prompt() -> None:
    """日期段追加在原提示词之后，原文一字不改。"""

    result = append_current_date(DEFAULT_SYSTEM_PROMPT, today=date(2026, 3, 17))

    assert result.startswith(DEFAULT_SYSTEM_PROMPT)
    assert result != DEFAULT_SYSTEM_PROMPT


def test_current_date_uses_iso_format_and_says_utc() -> None:
    """日期写成 ISO 格式并标明 UTC。

    格式钉死是有意义的：``2026-03-17`` 无歧义，而 ``03/17/2026`` 和 ``17/03/2026`` 长得
    一样却是两个日期。标明 UTC 是为了和 ``published_at`` 的存储时区对齐——模型据此换算
    「最近三天」时，边界和检索侧用的是同一套基准。
    """

    result = append_current_date("提示词", today=date(2026, 3, 17))

    assert "2026-03-17" in result
    assert "UTC" in result


def test_custom_prompt_still_gets_the_current_date() -> None:
    """用户换掉回答规范，不该顺带把「今天几号」也换掉。

    这条是日期做成独立追加步骤、而不是写进 ``DEFAULT_SYSTEM_PROMPT`` 的理由：写进默认值
    里，自定义提示词一上来日期就没了，模型又会把训练截止那会儿当成「现在」。
    """

    today = date(2026, 3, 17)

    result = select_system_prompt(AgentContext(system_prompt="你只说是或不是。"), today=today)

    assert result.startswith("你只说是或不是。")
    assert "2026-03-17" in result


def test_custom_prompt_actually_reaches_the_model() -> None:
    """自定义提示词必须真的进到模型收到的消息里。

    上面几条测的是「选对了哪份」，这条测的是「选出来的那份真的用上了」——中间件装错位置
    或图没传 context_schema 时，选择逻辑再对也不会生效。
    """

    model = ScriptedChatModel(responses=[AIMessage(content="是")])
    graph = build_offline_graph(model, [])

    run(collect(graph, context=AgentContext(system_prompt="你只说是或不是。")))

    system_messages = [
        message
        for call in model.received_messages
        for message in call
        if message.type == "system"
    ]
    assert system_messages
    # 用 startswith 而不是相等：末尾还有一段运行时注入的当前日期，写死日期会让这条测试
    # 隔天就红。日期段本身由上面几条单独钉。
    assert str(system_messages[0].content).startswith("你只说是或不是。")


def test_the_running_window_reaches_the_middleware_through_the_stream_entry() -> None:
    """当轮窗口要真的送到压缩判定那一步，而不只是「中间件在孤立调用时读得到」。

    上面那几条窗口用例都是**直接把窗口放进上下文变量再单调中间件**。生产里窗口来自
    ``AgentContext``——它得先穿过「建图 → 图节点执行」这段路才到得了中间件。这段路断掉的话，
    压缩会拿构造期那个占位值（32768）当窗口：不报错、不报警，只是在窗口小的模型上**永远不触发
    压缩**（反过来在窗口大的模型上会过早压缩）。两侧都看不出来，所以必须有这条端到端用例。

    观察方式刻意不数「假模型被调了几次」：中间件为了补占位窗口会 ``model_copy`` 一份客户端，
    摘要那次调用落在副本上、数不到。这里看的是**压缩发生过之后留下的痕迹**——状态里第一条变成
    摘要伪提问、消息条数减少、而两份脚本的取用顺序也跟着变（压缩先取走「摘要」，主模型才拿到
    「回答」）。
    """

    model = ScriptedChatModel(
        responses=[AIMessage(content="摘要"), AIMessage(content="回答")]
    )
    graph = build_offline_graph(model)
    history = old_history()
    question_text = "本次提问"
    # 刻意取**触发线的一半**：这样两种情况能一刀切开——当轮窗口真送到了，阈值是这段历史的一半
    # 左右、必定触发；没送到而回落到构造期那个占位值（32768），阈值是 26214、远大于这段历史，
    # 必定不触发。卡在临界点上就分不出「接线断了」还是「阈值算偏了几个 token」。
    window = window_that_triggers([*history, HumanMessage(content=question_text)]) // 2
    context = AgentContext(
        llm_model=ResolvedLlmModel(
            id=UUID("40000000-0000-4000-8000-0000000000a2"),
            display_name="演示模型",
            context_window=window,
        )
    )

    async def verify() -> tuple[list[Any], list[Any]]:
        thread_id = uuid4()
        await graph.aupdate_state(
            {"configurable": {"thread_id": str(thread_id)}}, {"messages": history}
        )
        events = [
            event
            async for event in stream_agent_events(
                graph,
                message=question_text,
                thread_id=thread_id,
                context=context,
                langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
            )
        ]
        snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
        return events, list(snapshot.values["messages"])

    events, messages = run(verify())

    # 1、压缩发生过：状态被重建过，头一条是摘要伪提问，整份历史比原来的 41 条短。
    assert messages[0].additional_kwargs.get("lc_source") == "summarization"
    assert len(messages) < len(history) + 1
    # 2、而且那条摘要就是摘要模型真的产出的内容——不是别的什么被塞进了头部。
    #    刻意不去数假模型被调了几次：中间件为了补占位窗口会 ``model_copy`` 一份客户端，
    #    摘要那次调用落在副本上，数不到。
    assert "摘要" in str(messages[0].content)
    assert events[-1].event == "done"
    assert events[-1].answer
