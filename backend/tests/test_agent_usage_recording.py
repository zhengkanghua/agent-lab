"""模型调用经过用量采集点的行为测试。

本文件只验一件事：**每一次模型调用都经过采集点，且包装的存在不改变任何其它可观察行为**。
它不碰数据库、不读用量库配置、不访问网络——采集器是假的，模型是假的，会话历史在
``InMemorySaver`` 里。

覆盖的接缝从高到低：走 ``AgentRuntime.build`` 的真实装配（含工具调用与工具重试）、
生产的流式入口、单层包装的属性委托与绑定行为。本工单只记**成功**的调用；「失败的、被取消的
调用也留一条记录」由后续工单补上。
"""

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

import httpx2 as sdk_httpx  # openai 3.x 的传输实现；仓库直接依赖的 httpx 不是同一条链路
import openai
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableBinding
from langgraph.checkpoint.memory import InMemorySaver

from agent_lab.agent.chat_model import build_chat_model
from agent_lab.agent.context import AgentContext
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.agent.streaming import stream_agent_events
from agent_lab.agent.usage_recording import wrap_with_usage_recording
from agent_lab.config.llm import LlmProvider, LlmSettings
from agent_lab.schemas.agent_chat import AgentErrorEvent, AgentTokenEvent
from agent_lab.schemas.llm_models import ResolvedLlmModel
from agent_lab.usage.contracts import UsageRecord, UsageSource, UsageStatus

from tests.agent_helpers import (
    OFFLINE_LANGSMITH_SETTINGS,
    CountingTool,
    FailingChatModel,
    ScriptedChatModel,
    StreamingChatModel,
    run,
    tool_call_message,
)
from tests.agent_scope_helpers import NEWS_SCOPE


def offline_llm_settings(**overrides: Any) -> LlmSettings:
    """造一份离线的进程级 LLM 配置（本文件的模型都是注入的假模型）。"""

    return LlmSettings(**overrides)


def openai_channel_settings() -> LlmSettings:
    """造一份 OpenAI 兼容渠道上线时用的进程级配置。"""

    return LlmSettings(temperature=0.0, request_timeout_seconds=60.0, user_agent="agent-lab")


class RecordingCollector:
    """把收到的记录攒在内存里的假采集器。

    ``error`` 非空时每次受理都抛异常，用来证明采集失败不会传到对话。
    """

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.records: list[UsageRecord] = []
        self.error = error

    def record(self, record: UsageRecord) -> None:
        if self.error is not None:
            raise self.error
        self.records.append(record)


class SingleModelResolver:
    """把同一个现成客户端交给所有 id。

    本文件要验的是「真实客户端被构造出来之后发了什么请求」，所以解析来源直接返回现成客户端，
    不经数据库。
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def resolve_client(self, _model_id: Any) -> Any:
        return self._client


class FakeSearchService:
    """只实现 ``search_documents`` 的假检索服务，供真实工具调用。

    ``fail_first`` 为真时第一次调用抛异常：``ToolRetryMiddleware`` 会重试，这正是「含工具
    调用与重试的一次运行」需要的形状，而重试的是工具，模型调用本身每次都成功。
    """

    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0

    async def search_documents(self, request: Any, *, resolved_scope: Any) -> list[Any]:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("检索上游暂时不可用")
        return []


class _BindingChatModel(ScriptedChatModel):
    """``bind_tools`` 返回一个新绑定对象的假模型。

    ``ScriptedChatModel`` 的 ``bind_tools`` 直接返回自己（模拟「底层不接受绑定」），
    走不到「重新挂回包装」那条路，所以这里单独造一个能返回绑定对象的假模型。
    """

    bound_tools: list[Any] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        self.bound_tools = list(tools)
        return RunnableBinding(bound=self, kwargs={"tools": list(tools)})


def build_runtime(
    *,
    model: Any,
    collector: RecordingCollector | None = None,
    search_service: Any = None,
) -> AgentRuntime:
    """按生产装配路径建一个 Runtime，只把模型、会话历史与采集器换掉。"""

    return AgentRuntime.build(
        llm_settings=offline_llm_settings(),
        search_service=search_service or FakeSearchService(),
        session_factory=None,  # type: ignore[arg-type]
        database_url="postgresql+psycopg://unused/unused",
        checkpointer=InMemorySaver(),
        model=model,
        usage_collector=collector,
        retry_initial_delay=0.0,
    )


def run_graph(runtime: AgentRuntime, *, context: AgentContext, message: str = "央行降息了吗") -> Any:
    """跑一次图，返回最终状态。"""

    async def go() -> Any:
        return await runtime.graph.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": str(uuid4())}},
            context=context,
        )

    return run(go())


def collect_events(runtime: AgentRuntime, *, context: AgentContext) -> list[Any]:
    """走生产的流式入口跑一次对话，把 SSE 事件收成列表。"""

    async def drain() -> list[Any]:
        return [
            event
            async for event in stream_agent_events(
                runtime.graph,
                message="央行降息了吗",
                thread_id=uuid4(),
                context=context,
                langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
            )
        ]

    return run(drain())


# 1、装配接缝：每一次模型调用都进采集器，条数等于模型客户端被真实调用的次数。


def test_every_model_call_reaches_the_collector() -> None:
    """含工具调用与一次工具重试的运行里，登记数等于模型客户端真实调用次数。

    这条用例里模型客户端被调两次：第一次决定调用检索工具，工具首次失败被中间件重试后成功，
    模型拿到工具结果再答一次。两条登记正好对上两次调用——重试的是工具，模型调用本身没失败，
    所以本工单「只记成功调用」的口径下也应该有两条。
    """

    collector = RecordingCollector()
    service = FakeSearchService(fail_first=True)
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_documents", {"query": "央行降息"}),
            AIMessage(content="央行确实降息了。"),
        ]
    )
    runtime = build_runtime(model=model, collector=collector, search_service=service)

    state = run_graph(runtime, context=AgentContext(scope=NEWS_SCOPE))

    assert model.call_count == 2
    assert service.calls == 2, "工具失败后必须被重试，而不是直接交给错误兜底"
    assert len(collector.records) == model.call_count
    assert all(record.status is UsageStatus.COMPLETED for record in collector.records)

    # 工具真的被执行并返回了结果，而不是被包装吞掉。
    tool_messages = [each for each in state["messages"] if isinstance(each, ToolMessage)]
    assert len(tool_messages) == 1
    assert "本次检索范围" in tool_messages[0].content


def test_tools_reach_the_inner_model_and_calls_are_still_recorded() -> None:
    """底层接受工具绑定时，工具必须到达底层，而绑定之后的调用仍然记账。

    两个都不能少：把工具丢在包装里，模型不再调用检索工具而且不报错；把包装换成底层绑定
    结果，从这一轮起这次运行剩下的调用一条都不会进账本。
    """

    collector = RecordingCollector()
    inner = _BindingChatModel(responses=[AIMessage(content="答")])
    wrapper = wrap_with_usage_recording(inner, collector)

    bound = wrapper.bind_tools([CountingTool("search_documents").build()])

    assert bound is not wrapper, "底层接受了绑定，包装应当换成绑定结果作为调用目标"
    assert len(inner.bound_tools) == 1, "工具必须真的下达到底层模型"
    assert bound.target is inner, "属性仍要来自底层模型"

    result = run(bound.ainvoke([HumanMessage(content="问题")]))

    assert result.content == "答"
    assert len(collector.records) == 1, "绑定之后的调用仍必须经过采集点"


def test_a_fake_model_that_refuses_binding_falls_back_to_sharing_itself() -> None:
    """底层不接受绑定时退回「不绑工具、仍由包装记账」，而不是把自己换成底层对象。"""

    collector = RecordingCollector()
    wrapper = wrap_with_usage_recording(ScriptedChatModel(responses=[]), collector)

    assert wrapper.bind_tools([]) is wrapper
    assert wrapper.bind() is wrapper


def test_the_assembly_runs_without_a_collector() -> None:
    """不注入采集器时装配照常工作，且不建任何连接。

    离线测试把真实 psycopg 连接打成失败（见 ``tests/conftest.py``），所以这条用例通过本身
    就证明装配没有偷偷去连数据库；它也不要求任何用量库环境变量。
    """

    model = ScriptedChatModel(responses=[AIMessage(content="答")])
    runtime = build_runtime(model=model, collector=None)

    state = run_graph(runtime, context=AgentContext(scope=NEWS_SCOPE))

    assert state["messages"][-1].content == "答"
    assert model.call_count == 1


# 2、生产入口：流式行为不被包装改变。


def test_the_production_streaming_entry_still_streams_tokens() -> None:
    """走生产的流式入口时，客户端仍然逐块收到分片，而不是等生成完一次性收到。

    判据是 token 事件多于一条：一次性返回的回答只会产生一条 token 事件。
    """

    collector = RecordingCollector()
    model = StreamingChatModel(messages=iter([AIMessage(content="降息 25 个基点。")]))
    runtime = build_runtime(model=model, collector=collector)

    events = collect_events(runtime, context=AgentContext(scope=NEWS_SCOPE))

    tokens = [each for each in events if isinstance(each, AgentTokenEvent)]
    assert len(tokens) > 1, "流式入口被包装挡住时会退化成一次性返回整段回答"
    assert "".join(each.text for each in tokens) == "降息 25 个基点。"
    assert len(collector.records) == 1, "流式入口跑的仍是 ainvoke，必须留下记录"


# 一份最小的 OpenAI 兼容流式响应：先一个字，再一个收尾块（带上用量）。
# 用量块按官方契约把 choices 留空——带 choices 的块不会把 usage_metadata 带上消息。
_OPENAI_STREAM_BODY = (
    'data: {"id":"chatcmpl-test","object":"chat.completion.chunk","created":0,'
    '"model":"offline-test-model","choices":[{"index":0,'
    '"delta":{"role":"assistant","content":"央行确实降息了。"},"finish_reason":null}]}\n\n'
    'data: {"id":"chatcmpl-test","object":"chat.completion.chunk","created":0,'
    '"model":"offline-test-model","choices":[],'
    '"usage":{"prompt_tokens":11,"completion_tokens":5,"total_tokens":16}}\n\n'
    "data: [DONE]\n\n"
)


def test_the_production_streaming_entry_asks_the_upstream_for_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """走生产入口时，请求体里必须带「流式响应里回传用量」的开关，而且用量真的进了账本。

    这条**不能**用注入假模型来做：那会直接替掉模型构造入口，被测的那行开关根本没跑到，用例
    会绿得毫无意义。所以这里在 HTTP 传输层造假上游，让真实客户端被构造出来并真的发出请求，
    再断言请求体与落下来的用量。
    """

    requests: list[Any] = []

    async def fake_transport(self: Any, request: Any) -> Any:
        requests.append(request)
        return sdk_httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_OPENAI_STREAM_BODY,
            request=request,
        )

    settings = openai_channel_settings()
    # 探针客户端只为拿到 SDK 真正使用的传输类。openai 3.x 已改用 httpx2，而仓库直接依赖的
    # httpx（也是 tests/conftest.py 离线阻断补的那个）不是同一条链路，所以这里按实际类型补，
    # 而不是把类名写死。
    probe = build_chat_model(
        settings,
        provider=LlmProvider.OPENAI_COMPATIBLE,
        base_url="https://gateway.example.com/v1",
        credential="sk-test",
        model="offline-test-model",
    )
    transport_class = type(probe.async_client._client._client._transport)
    monkeypatch.setattr(transport_class, "handle_async_request", fake_transport)

    collector = RecordingCollector()
    model_id = uuid4()
    runtime = AgentRuntime.build(
        llm_settings=settings,
        search_service=FakeSearchService(),
        session_factory=None,  # type: ignore[arg-type]
        database_url="postgresql+psycopg://unused/unused",
        checkpointer=InMemorySaver(),
        model_resolver=SingleModelResolver(probe),
        usage_collector=collector,
        retry_initial_delay=0.0,
    )

    events = collect_events(
        runtime,
        context=AgentContext(
            scope=NEWS_SCOPE,
            llm_model=ResolvedLlmModel(
                id=model_id, display_name="offline-test-model", context_window=32768
            ),
        ),
    )

    assert events, "生产入口应当跑完并产出事件"
    assert requests, "真实客户端必须真的发出了请求"
    body = json.loads(requests[0].content)
    assert body["stream"] is True, "生产入口本来就是流式调用"
    assert body["stream_options"] == {"include_usage": True}, (
        "自建 base_url 下 ChatOpenAI 默认不请求用量，必须显式打开，"
        "否则流式响应里没有 usage_metadata，用量会被静默记成 0"
    )
    assert len(collector.records) == 1
    assert collector.records[0].input_tokens == 11, "上游回传的用量必须真的被读出来"


# 3、可观测属性：逐项委托，漏一项就会在这里AttributeError。


@pytest.mark.parametrize(
    ("provider", "base_url", "attributes"),
    [
        (
            LlmProvider.OPENAI_COMPATIBLE,
            "https://gateway.example.com/v1",
            (
                "model_name",
                "default_headers",
                "request_timeout",
                "temperature",
                "max_retries",
                "model_kwargs",
                "use_responses_api",
            ),
        ),
        (
            LlmProvider.OLLAMA,
            "http://127.0.0.1:11434",
            # Ollama 分支把超时与请求头放在 client_kwargs 里，没有 model_name /
            # default_headers / request_timeout / max_retries 这些字段，所以这里的清单不同。
            ("model", "client_kwargs", "temperature"),
        ),
    ],
)
def test_the_wrapper_does_not_change_observable_attributes(
    provider: LlmProvider,
    base_url: str,
    attributes: tuple[str, ...],
) -> None:
    """把真实客户端包起来后，可观测属性与未包装时一致。

    这是唯一能发现「漏委托一项」的地方：漏掉带默认值的属性不会报错，只会静默给出空值，
    现有构造测试又只调 ``build_chat_model``、根本不经过包装。
    """

    settings = LlmSettings(
        temperature=0.3,
        request_timeout_seconds=45.0,
        user_agent="agent-lab",
    )
    inner = build_chat_model(
        settings,
        provider=provider,
        base_url=base_url,
        credential="sk-test" if provider is LlmProvider.OPENAI_COMPATIBLE else "",
        model="test-model",
    )
    wrapper = wrap_with_usage_recording(inner, RecordingCollector())

    for name in attributes:
        assert getattr(wrapper, name) == getattr(inner, name), name

    # 中间件会读的三项单独列出来，它们不是普通字段。
    assert wrapper.profile == inner.profile
    assert wrapper._llm_type == inner._llm_type
    assert wrapper._get_ls_params() == inner._get_ls_params()


# 4、用量取数：上游报的值原样记，缺失与 0 分得开，缓存按后缀取且缺了不补 0。


def test_upstream_usage_is_recorded_verbatim() -> None:
    """输入、输出、合计原样记录；上游没报合计时用输入加输出补齐。"""

    record = _record_for(AIMessage(content="答", usage_metadata={
        "input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
    }))

    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (10, 4, 14)
    assert record.source is UsageSource.UPSTREAM


def test_upstream_reported_zero_is_not_a_missing_usage() -> None:
    """上游报了 0 也是「自报」，不能记成「缺失」——那正是这份账要分开的两件事。"""

    record = _record_for(AIMessage(content="答", usage_metadata={
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
    }))

    assert record.source is UsageSource.UPSTREAM
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (0, 0, 0)


def test_a_call_without_a_usage_object_is_marked_missing() -> None:
    """上游什么都没报时来源记缺失、token 记 0，与「报了 0」区分开。"""

    record = _record_for(AIMessage(content="答"))

    assert record.source is UsageSource.MISSING
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (0, 0, 0)


def test_a_missing_total_is_filled_from_input_and_output() -> None:
    """上游没报合计时用输入加输出补齐。

    ``usage_metadata`` 的 Pydantic 校验要求三项齐全，真实 provider 也不会有这种形状，
    所以这里绕过校验直接构造消息，专门守住补齐这条分支。
    """

    message = AIMessage.model_construct(content="答", usage_metadata={
        "input_tokens": 3, "output_tokens": 5,
    })

    record = _record_for(message)

    assert record.total_tokens == 8
    assert record.source is UsageSource.UPSTREAM


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ({"cache_read": 6}, 6),
        # 不同服务档位会给这个键加前缀，只能按后缀匹配。
        ({"prompt_cache_read": 9}, 9),
        ({"audio": 2}, None),
        (None, None),
    ],
)
def test_cache_read_is_taken_by_suffix_and_missing_stays_missing(
    details: dict[str, int] | None,
    expected: int | None,
) -> None:
    """缓存命中数按 ``cache_read`` 后缀取；上游没报这一项时记缺失，不记 0。"""

    usage: dict[str, Any] = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    if details is not None:
        usage["input_token_details"] = details

    record = _record_for(AIMessage(content="答", usage_metadata=usage))

    assert record.cached_tokens == expected


# 5、运行身份：同一次运行的记录共享同一个运行标识。


def test_records_of_one_run_share_the_run_identity() -> None:
    """同一次运行里的多次调用带上同一个运行标识，以及账号与会话引用。"""

    collector = RecordingCollector()
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_documents", {"query": "央行降息"}),
            AIMessage(content="央行确实降息了。"),
        ]
    )
    runtime = build_runtime(model=model, collector=collector)
    user_id = uuid4()
    thread_id = uuid4()
    context = AgentContext(scope=NEWS_SCOPE, user_id=user_id, thread_id=thread_id)

    run_graph(runtime, context=context)

    assert len(collector.records) == 2
    assert {record.run_id for record in collector.records} == {context.run_id}
    assert {record.user_id for record in collector.records} == {user_id}
    assert {record.thread_id for record in collector.records} == {thread_id}


def test_missing_attribution_leaves_a_log(caplog: pytest.LogCaptureFixture) -> None:
    """归属缺失时留一条日志：账号为空的记录谁都查不到，不记就没人知道账本在少算。"""

    collector = RecordingCollector()
    wrapper = wrap_with_usage_recording(
        ScriptedChatModel(responses=[AIMessage(content="答")]),
        collector,
    )

    with caplog.at_level(logging.WARNING):
        result = run(wrapper.ainvoke([HumanMessage(content="问题")]))

    assert result.content == "答"
    assert collector.records[0].user_id is None
    assert "缺少账号归属" in caplog.text


def test_a_failing_collector_does_not_break_the_call() -> None:
    """采集失败绝不影响对话：采集器抛异常时回答照常返回。"""

    wrapper = wrap_with_usage_recording(
        ScriptedChatModel(responses=[AIMessage(content="答")]),
        RecordingCollector(error=RuntimeError("用量库坏了")),
    )

    result = run(wrapper.ainvoke([HumanMessage(content="问题")]))

    assert result.content == "答"


# 6、失败与被取消的调用也各留一条记录。


def test_a_failing_model_call_leaves_one_failed_record_per_attempt() -> None:
    """模型报错时，每次尝试留一条失败记录，且不会在成功路径上再补一条。

    ``ModelRetryMiddleware`` 会把一次提问变成多次真实调用；
    账本必须**逐次**记下来（重试风暴这种成本异常不能被平均掉），并且每条都带上账号、会话与
    运行——失败的那次调用同样属于某次提问，丢了归属就再也追不回来。
    """

    collector = RecordingCollector()
    model = FailingChatModel(
        error=openai.APIConnectionError(request=sdk_httpx.Request("POST", "https://gateway.example.invalid"))
    )
    runtime = build_runtime(model=model, collector=collector)
    user_id, thread_id = uuid4(), uuid4()
    context = AgentContext(scope=NEWS_SCOPE, user_id=user_id, thread_id=thread_id)

    events = collect_events(runtime, context=context)

    assert isinstance(events[-1], AgentErrorEvent), "模型彻底失败时运行以 error 事件收尾"
    assert model.call_count >= 1
    assert len(collector.records) == model.call_count, "每次尝试恰好一条，不多不少"
    for record in collector.records:
        assert record.status is UsageStatus.FAILED
        assert (record.input_tokens, record.output_tokens, record.total_tokens) == (0, 0, 0)
        assert record.cached_tokens is None
        assert record.source is UsageSource.MISSING
        assert (record.user_id, record.thread_id, record.run_id) == (user_id, thread_id, context.run_id)


def test_a_retried_call_leaves_one_failed_and_one_completed_record() -> None:
    """第一次失败、第二次成功的调用留两条记录：失败那条不会被成功路径重复记一遍。"""

    collector = RecordingCollector()
    model = _FlakyChatModel(
        responses=[AIMessage(content="央行确实降息了。")],
        error=openai.APIConnectionError(request=sdk_httpx.Request("POST", "https://gateway.example.invalid")),
    )
    runtime = build_runtime(model=model, collector=collector)

    state = run_graph(runtime, context=AgentContext(scope=NEWS_SCOPE))

    assert state["messages"][-1].content == "央行确实降息了。"
    assert model.call_count == 2
    assert [record.status for record in collector.records] == [
        UsageStatus.FAILED,
        UsageStatus.COMPLETED,
    ]


def test_a_cancelled_call_leaves_a_failed_record_and_still_propagates() -> None:
    """模型调用进行中取消运行时：账本上留一条耗时算到取消那一刻的失败记录，取消原样向上传播。

    两件事必须同时成立。只记账不传播会把「已停止」变成「继续跑」；只传播不记账会让「停止」
    在账本上看不见。耗时那项用「远小于底层模型的睡眠时长」来断言：耗时要是算到调用真正结束
    才取，这里就永远等不到记录。
    """

    collector = RecordingCollector()
    model = _HangingChatModel()
    runtime = build_runtime(model=model, collector=collector)

    async def scenario() -> bool:
        task = asyncio.create_task(
            runtime.graph.ainvoke(
                {"messages": [HumanMessage(content="央行降息了吗")]},
                config={"configurable": {"thread_id": str(uuid4())}},
                context=AgentContext(scope=NEWS_SCOPE, user_id=uuid4(), thread_id=uuid4()),
            )
        )
        # 等模型真的在途了再取消，否则测的是「还没开始就取消」。
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    cancelled = run(scenario())

    assert cancelled is True, "取消必须原样向上传播，不能被包装吞掉"
    assert len(collector.records) == 1, "取消的那次调用只留一条记录"
    record = collector.records[0]
    assert record.status is UsageStatus.FAILED
    assert record.source is UsageSource.MISSING
    assert record.duration_ms < 5_000, "耗时应当算到取消那一刻，而不是等底层模型自己结束"


class _FlakyChatModel(FakeMessagesListChatModel):
    """第一次调用抛错、之后按脚本回答的假模型。

    ``ScriptedChatModel`` 与 ``FailingChatModel`` 都只能表达单一的成败，而「失败一次再成功」
    正是验证「一次调用只产生一条记录」需要的形状。
    """

    error: BaseException
    call_count: int = 0

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        if self.call_count == 1:
            raise self.error
        return super()._generate(messages, *args, **kwargs)


class _HangingChatModel(BaseChatModel):
    """永远挂住的假模型：用来在模型调用进行中取消一次运行。

    直接覆写 ``ainvoke`` 而不是让 ``_generate`` 睡眠：默认的 ``_agenerate`` 会把 ``_generate``
    放到线程里跑，而线程里的 ``sleep`` 不会被任务取消打断，那样测不出取消路径。
    """

    @property
    def _llm_type(self) -> str:
        return "hanging-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def ainvoke(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        await asyncio.sleep(30)
        raise AssertionError("取消之后不该再往下跑")


# 辅助：直接调包装一次，取回那条记录。


def _record_for(message: BaseMessage) -> UsageRecord:
    collector = RecordingCollector()
    wrapper = wrap_with_usage_recording(ScriptedChatModel(responses=[message]), collector)

    run(wrapper.ainvoke([HumanMessage(content="问题")]))

    assert len(collector.records) == 1
    return collector.records[0]
