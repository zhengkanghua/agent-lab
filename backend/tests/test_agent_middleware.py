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
from typing import Any
from uuid import uuid4

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage

from agent_lab.agent.context import AgentContext
from agent_lab.agent.limits import (
    MODEL_CALL_RUN_LIMIT,
    MODEL_RETRY_MAX,
    TOOL_CALL_RUN_LIMIT,
    TOOL_RETRY_MAX,
)
from agent_lab.agent.middleware import (
    append_current_date,
    build_agent_middleware,
    select_system_prompt,
)
from agent_lab.agent.prompts import DEFAULT_SYSTEM_PROMPT
from agent_lab.agent.streaming import stream_agent_events
from agent_lab.schemas.agent_chat import AgentErrorEvent, AgentToolResultEvent
from tests.agent_helpers import (
    OFFLINE_LANGSMITH_SETTINGS,
    CountingTool,
    FailingChatModel,
    ScriptedChatModel,
    build_offline_graph,
    run,
    tool_call_message,
)


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


def test_middleware_order_is_the_documented_one() -> None:
    """流水线顺序必须与 ADR 0005 固定的顺序逐项一致。

    直接断言类名序列，是为了让任何人「顺手调一下顺序」时立刻失败，而不是等到某天发现
    重试没生效。顺序的理由在 ADR 里，这里只钉住结果。
    """

    model = ScriptedChatModel(responses=[AIMessage(content="ok")])
    names = [
        type(middleware).__name__
        for middleware in build_agent_middleware(
            fallback_model=model, summarization_model=model
        )
    ]
    assert names == [
        "resolve_system_prompt",
        "UnknownToolGuardMiddleware",
        "ModelFallbackMiddleware",
        "ModelRetryMiddleware",
        "SummarizationMiddleware",
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
        "ToolErrorMiddleware",
        "ToolRetryMiddleware",
    ]
    # 守卫必须排在兜底的外层，否则它抛的异常会被 ToolErrorMiddleware 翻成安全文案交回模型。
    assert names.index("UnknownToolGuardMiddleware") < names.index("ToolErrorMiddleware")


def test_failing_tool_is_retried_before_the_error_handler_sees_it() -> None:
    """工具失败时必须先重试满次数，再交给兜底翻译成安全文案。

    这是 ADR 0005 的核心断言：``invocations`` 为 ``1 + TOOL_RETRY_MAX`` 证明重试中间件
    确实在内层。若两个中间件顺序写反，这里会是 1——而下面的文案断言依然通过。
    """

    failing = CountingTool("search_news", error=RuntimeError(LEAKY_MESSAGE))
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_news", {"text": "央行"}),
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

    工具名取 ``Bash`` 不是随手编的：``LLM_MODEL=auto`` 那次事故里模型真的发出了
    ``Bash(command, description)``，见 ``UnknownToolGuardMiddleware`` 的说明。
    """

    registered = CountingTool("search_news")
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

    registered = CountingTool("search_news", result="检索结果")
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_news", {"text": "央行"}),
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

    payload = str([event.model_dump() for event in events])
    assert "secret" not in payload
    assert "postgresql://" not in payload
    assert "RuntimeError" not in payload


def test_run_continues_after_a_tool_fails() -> None:
    """工具失败不该终止整段运行——模型要有机会解释或换个思路。

    如果 ``sanitize_tool_error`` 返回 ``None``，异常会继续上抛、运行中断，用户只会看到
    一个错误事件。这里断言运行走到了 ``done``，且模型拿到失败结果后仍产出了回答。
    """

    failing = CountingTool("search_news", error=RuntimeError("boom"))
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_news", {"text": "央行"}),
            AIMessage(content="检索暂时不可用，我无法回答这个问题。"),
        ]
    )
    graph = build_offline_graph(model, [failing.build()])

    events = run(collect(graph))

    assert [type(event).__name__ for event in events][-1] == "AgentDoneEvent"
    assert model.call_count == 2


def test_model_failure_is_retried_then_falls_back() -> None:
    """主模型必须先重试满次数，再降级到备用模型。

    与工具侧对称的断言：主模型调用次数为 ``1 + MODEL_RETRY_MAX`` 证明重试在降级内层。
    若顺序写反，主模型只会被调 1 次就换模型——看起来也「能用」，但配置里的重试次数
    实际上被忽略了。
    """

    primary = FailingChatModel(
        error=openai.APIConnectionError(request=httpx.Request("POST", "http://llm"))
    )
    fallback = ScriptedChatModel(responses=[AIMessage(content="备用模型的回答")])
    graph = build_offline_graph(primary, [], fallback_model=fallback)

    events = run(collect(graph))

    assert primary.call_count == 1 + MODEL_RETRY_MAX
    assert fallback.call_count == 1
    assert [type(event).__name__ for event in events][-1] == "AgentDoneEvent"


def test_tool_call_limit_stops_further_tool_use() -> None:
    """工具调用次数到顶后不再执行工具，但模型仍可作答。

    这道上限防的是「模型陷入检索循环」：没有它，一次运行可能无限调工具，把 token 和
    Qdrant 查询都烧光。``exit_behavior="continue"`` 的选择意味着到顶不是报错，而是让
    模型用已有材料收尾。
    """

    counting = CountingTool("search_news", result="一条结果")
    # 脚本一直要求调工具，比上限多几次，确保上限而不是脚本决定了停止时机。
    model = ScriptedChatModel(
        responses=[
            *[tool_call_message("search_news", {"text": f"q{i}"})
              for i in range(TOOL_CALL_RUN_LIMIT + 3)],
            AIMessage(content="收尾回答"),
        ]
    )
    graph = build_offline_graph(model, [counting.build()])

    run(collect(graph))

    assert len(counting.invocations) <= TOOL_CALL_RUN_LIMIT


def test_model_call_limit_ends_the_run() -> None:
    """模型调用次数到顶后运行结束，不无限循环。"""

    counting = CountingTool("search_news", result="一条结果")
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_news", {"text": f"q{i}"})
            for i in range(MODEL_CALL_RUN_LIMIT + 5)
        ]
    )
    graph = build_offline_graph(model, [counting.build()])

    events = run(collect(graph))

    assert model.call_count <= MODEL_CALL_RUN_LIMIT
    assert [type(event).__name__ for event in events][-1] == "AgentDoneEvent"


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

    assert select_system_prompt(context, today=today) == append_current_date(
        expected, today=today
    )


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
