"""按当轮选定的模型解析客户端：解析位置、属性委托、账本模型名与压缩窗口。

本文件用一个不连数据库的解析替身（``StubResolver``）替掉目录读取，而包装链、图、中间件与
生产完全一致——被测的是「解析包装」本身，不是替身。另有一组用例走真实的
``CatalogModelResolver``，用一份「罐头会话」喂给它目录里的一行；缓存那组还把「当前时间」换成
一个可推进的时钟，于是不必真等 60 秒。

不访问网络、不连 PostgreSQL、不读 ``.env``，也不调用真实大模型。
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.checkpoint.memory import InMemorySaver
from cryptography.fernet import Fernet

from agent_lab.agent.context import AgentContext
from agent_lab.agent.limits import (
    MODEL_CLIENT_CACHE_MAX_ENTRIES,
    MODEL_CLIENT_CACHE_TTL_SECONDS,
)
from agent_lab.agent.model_resolution import (
    CatalogModelResolver,
    ResolvingChatModel,
    RunModelUnresolvedError,
)
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.agent.streaming import stream_agent_events
from agent_lab.config.llm import LlmProvider, LlmSettings
from agent_lab.schemas.llm_models import ResolvedLlmModel
from agent_lab.services.llm_credential_cipher import CredentialCipher
from agent_lab.services.llm_model_errors import LlmModelUnavailableError
from agent_lab.services.llm_model_selection_service import LlmModelSelectionService
from agent_lab.usage.contracts import UsageRecord

from tests.agent_helpers import (
    OFFLINE_LANGSMITH_SETTINGS,
    ScriptedChatModel,
    run,
)


class StubResolver:
    """按 id 给出预置客户端的解析替身，并记下被问过哪些 id。"""

    def __init__(self, clients: dict[UUID, Any]) -> None:
        self._clients = clients
        self.requested: list[UUID] = []

    async def resolve_client(self, model_id: UUID) -> Any:
        self.requested.append(model_id)
        return self._clients[model_id]


class NamedScriptedChatModel(ScriptedChatModel):
    """带上游模型名的假模型，并可以在每次调用开始时执行一个探针。

    账本取模型名走的是 ``_get_ls_params()['ls_model_name']``，所以假模型必须能报出名字，
    否则「委托了没有」这件事在测试里分辨不出来。``on_call`` 用来在**调用期间**读解析包装的
    可观测属性——那些属性只在一次调用期间有值（见 ``ResolvingChatModel``）。
    """

    upstream_name: str = ""
    model_name: str | None = None
    model: str | None = None
    on_call: Any = None

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        """报出这个上游模型名，与真实客户端同样给 ``ls_model_name``。"""

        return {"ls_model_name": self.upstream_name}

    def _generate(self, messages: list[Any], *args: Any, **kwargs: Any) -> Any:
        if self.on_call is not None:
            self.on_call()
        return super()._generate(messages, *args, **kwargs)


def named_fake(answer: str, upstream_name: str, *, more: tuple[str, ...] = ()) -> NamedScriptedChatModel:
    """造一个报得出上游模型名的假模型。"""

    return NamedScriptedChatModel(
        responses=[AIMessage(content=answer), *(AIMessage(content=each) for each in more)],
        upstream_name=upstream_name,
        model_name=upstream_name,
        model=upstream_name,
    )


class RecordingCollector:
    """把收到的用量记录攒在内存里。"""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def record(self, record: UsageRecord) -> None:
        self.records.append(record)


class FakeSearchService:
    """本文件里模型不调工具，检索不会被用到。"""

    async def search_documents(self, request: Any, *, resolved_scope: Any) -> list[Any]:
        return []


OFFLINE_LLM_SETTINGS = LlmSettings(temperature=0.0, request_timeout_seconds=60.0)


def build_runtime(
    *,
    resolver: Any = None,
    model: Any = None,
    collector: Any = None,
    usage_collector: Any = None,
) -> AgentRuntime:
    """按生产装配路径建一个 Runtime，只把模型解析来源、会话历史与采集器换掉。"""

    return AgentRuntime.build(
        llm_settings=OFFLINE_LLM_SETTINGS,
        search_service=FakeSearchService(),
        session_factory=None,  # type: ignore[arg-type]
        database_url="postgresql+psycopg://unused/unused",
        checkpointer=InMemorySaver(),
        model=model,
        model_resolver=resolver,
        usage_collector=usage_collector if usage_collector is not None else collector,
        retry_initial_delay=0.0,
    )


def snapshot(model_id: UUID, *, display_name: str = "展示名", window: int = 32768) -> ResolvedLlmModel:
    """造一份当轮模型快照（运行上下文与提问消息共用这个形状）。"""

    return ResolvedLlmModel(id=model_id, display_name=display_name, context_window=window)


def run_graph(runtime: AgentRuntime, *, context: AgentContext, message: str = "问一句") -> Any:
    """在给定运行上下文下跑一次图，返回最终状态。"""

    async def go() -> Any:
        return await runtime.graph.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": str(uuid4())}},
            context=context,
        )

    return run(go())


def collect_events(runtime: AgentRuntime, *, context: AgentContext, thread_id: UUID, message: str) -> Any:
    """走生产的流式入口跑一次对话。"""

    async def drain() -> list[Any]:
        return [
            event
            async for event in stream_agent_events(
                runtime.graph,
                message=message,
                thread_id=thread_id,
                context=context,
                langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
            )
        ]

    return run(drain())


# 1、解析：同一次提问用哪个客户端，由当轮那个模型条目的 id 决定。


def test_each_run_resolves_the_client_of_the_model_in_its_context() -> None:
    """两次运行选了不同模型，就解析到两个不同的客户端。

    断言的是**客户端构造结果**而不是网络请求：替身客户端分别记下自己的调用次数，
    选中的那个被调、另一个一次都没被调。
    """

    first_id, second_id = uuid4(), uuid4()
    first = named_fake("第一答", "alpha")
    second = named_fake("第二答", "beta")
    resolver = StubResolver({first_id: first, second_id: second})
    runtime = build_runtime(resolver=resolver)

    first_state = run_graph(runtime, context=AgentContext(llm_model=snapshot(first_id)))
    second_state = run_graph(runtime, context=AgentContext(llm_model=snapshot(second_id)))

    assert resolver.requested == [first_id, second_id]
    assert first_state["messages"][-1].content == "第一答"
    assert second_state["messages"][-1].content == "第二答"
    assert (first.call_count, second.call_count) == (1, 1)


def test_the_wrapper_delegates_observable_attributes_to_the_resolved_client() -> None:
    """解析包装的可观测属性在调用期间等于当轮客户端的那一份。

    必须在调用期间读：包装本身没有任何「当轮客户端」的实例状态（那会被并发会话覆盖），
    它只在一次调用期间从按协程隔离的上下文里取。``_get_ls_params`` 是账本取模型名走的
    那一项，漏委托会静默变成缺失。
    """

    model_id = uuid4()
    inner = named_fake("答", "alpha")
    wrapper = ResolvingChatModel(resolver=StubResolver({model_id: inner}))
    observed: dict[str, Any] = {}
    inner.on_call = lambda: observed.update(
        model_name=wrapper.model_name,
        model=wrapper.model,
        ls_params=wrapper._get_ls_params(),
    )
    runtime = build_runtime(model=wrapper)

    run_graph(runtime, context=AgentContext(llm_model=snapshot(model_id)))

    assert observed["model_name"] == inner.model_name == "alpha"
    assert observed["ls_params"] == inner._get_ls_params() == {"ls_model_name": "alpha"}


def test_ls_params_are_empty_outside_a_call() -> None:
    """一次调用还没解析过客户端时 ``_get_ls_params`` 返回空字典，而不是抛错。

    摘要中间件会在「这一轮还没开始调模型」时读它一次（上游用 ``ls_provider`` 比对上一轮自报的
    用量是否超线），那时没有客户端可委托。代价是那条路径在我们这里不成立，压缩一律按窗口
    占比触发——这条用例把这个约定钉住，免得日后改成抛错把压缩检查打挂。
    """

    wrapper = ResolvingChatModel(resolver=StubResolver({}))

    assert wrapper._get_ls_params() == {}


def test_a_run_without_a_chosen_model_fails_instead_of_answering() -> None:
    """运行上下文里没有模型时明确失败，绝不静默换一个模型回答。"""

    model_id = uuid4()
    inner = named_fake("不该出现", "alpha")
    resolver = StubResolver({model_id: inner})
    runtime = build_runtime(resolver=resolver)

    with pytest.raises(RunModelUnresolvedError):
        run_graph(runtime, context=AgentContext())

    assert resolver.requested == []
    assert inner.call_count == 0, "一次模型调用都不该发出"


def test_the_sync_entries_fail_loudly_instead_of_answering() -> None:
    """同步入口明确失败：当轮模型要按运行上下文异步解析，同步栈里等不了。

    本项目整张图本来就是异步的（有一个中间件只实现了异步钩子），所以这里不造一条同步路径，
    而是让它报出一个说得清的错误，而不是静默拿别的模型回答。
    """

    wrapper = ResolvingChatModel(resolver=StubResolver({}))

    with pytest.raises(RunModelUnresolvedError):
        wrapper.invoke([HumanMessage(content="问")])
    with pytest.raises(RunModelUnresolvedError):
        wrapper.stream([HumanMessage(content="问")])


def test_the_wrapper_applies_the_tools_it_was_asked_to_bind() -> None:
    """``bind_tools`` 记在副本上，调用时才交给当轮客户端——装配期还没有客户端。"""

    model_id = uuid4()
    inner = _ToolRecordingChatModel(responses=[AIMessage(content="答")], upstream_name="alpha")
    wrapper = ResolvingChatModel(resolver=StubResolver({model_id: inner}))
    runtime = build_runtime(model=wrapper)

    run_graph(runtime, context=AgentContext(llm_model=snapshot(model_id)))

    assert [tool.name for tool in inner.bound_tools] == [
        "search_documents",
        "read_document",
    ], "生产图绑定的两个工具必须真的到达当轮客户端"


class _ToolRecordingChatModel(NamedScriptedChatModel):
    """记下绑定到它头上的工具名。"""

    bound_tools: list[Any] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        self.bound_tools = list(tools)
        return self


# 2、账本：记的是「这次调用用的上游模型名」，而且每次调用恰好一条。


def test_the_ledger_records_the_upstream_name_of_the_chosen_model() -> None:
    """账本里的模型名是当轮那个条目的**上游模型名**，每个模型每次调用恰好一条记录。

    快照里的展示名刻意与上游模型名不同：记成展示名就说明它读错了地方。两次运行选不同模型，
    两条记录各自对上各自的客户端，不多不少。
    """

    first_id, second_id = uuid4(), uuid4()
    collector = RecordingCollector()
    resolver = StubResolver(
        {
            first_id: named_fake("一", "alpha"),
            second_id: named_fake("二", "beta"),
        }
    )
    runtime = build_runtime(resolver=resolver, usage_collector=collector)

    run_graph(
        runtime,
        context=AgentContext(llm_model=snapshot(first_id, display_name="展示名甲")),
    )
    run_graph(
        runtime,
        context=AgentContext(llm_model=snapshot(second_id, display_name="展示名乙")),
    )

    assert [record.model_name for record in collector.records] == ["alpha", "beta"]


# 3、真实解析来源：按目录里那一行渠道构造。


def catalog_session_factory(row: Any) -> Any:
    """造一个「不管问哪一行都回同一份罐头数据」的 session 工厂。

    真实的「按 id 取哪一行」由 ``LlmModelRepository`` 负责（它自己的查询有别的用例守着），
    这里要验的是拿到那一行之后构造出来的客户端。
    """

    class CannedResult:
        def __init__(self, row: Any) -> None:
            self._row = row

        def first(self) -> Any:
            return self._row

    class CannedSession:
        async def execute(self, _statement: Any) -> Any:
            return CannedResult(row)

        async def __aenter__(self) -> "CannedSession":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

    return lambda: CannedSession()


class CatalogRow:
    """目录里的一行：一个模型条目加它所属的那条渠道。

    字段名与 ``LlmModelRecord`` / ``LlmProviderRecord`` 里真有的那些对齐。解析本身只读
    ``upstream_model_name`` 与渠道那三项；模型行的 ``id``、``display_name``、``context_window``
    是「开始运行之前解析」那道门取快照时要读的，所以一并给全。
    """

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        upstream_model_name: str,
        model_id: UUID | None = None,
        display_name: str | None = None,
        context_window: int = 32768,
        enabled: bool = True,
    ):
        self.model = _Row(
            id=model_id or uuid4(),
            upstream_model_name=upstream_model_name,
            display_name=display_name,
            context_window=context_window,
            enabled=enabled,
        )
        self.provider = _Row(
            provider=provider,
            base_url=base_url,
            credential_ciphertext=None,
            enabled=enabled,
        )

    def __iter__(self):
        return iter((self.model, self.provider))


class _Row:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


def test_the_resolver_builds_each_client_from_its_own_channel_row() -> None:
    """两个渠道解析出两个只差渠道内容的客户端：接入类型、地址、上游模型名各自独立。"""

    cipher = CredentialCipher(Fernet.generate_key().decode())
    compatible = _resolver_for(
        cipher,
        CatalogRow(
            provider=LlmProvider.OPENAI_COMPATIBLE.value,
            base_url="https://first.example.com/v1",
            upstream_model_name="alpha",
        ),
        credential="sk-first",
    )
    local = _resolver_for(
        cipher,
        CatalogRow(
            provider=LlmProvider.OLLAMA.value,
            base_url="http://second.example.com:11434",
            upstream_model_name="beta",
        ),
        credential="",
    )

    first = run(compatible.resolve_client(uuid4()))
    second = run(local.resolve_client(uuid4()))

    assert type(first).__name__ == "ChatOpenAI"
    assert type(second).__name__ == "ChatOllama"
    assert (first.model_name, first.openai_api_base) == ("alpha", "https://first.example.com/v1")
    assert (second.model, second.base_url) == ("beta", "http://second.example.com:11434")
    assert first.openai_api_key.get_secret_value() == "sk-first", "凭据从那一行的密文解出来交给客户端"


def _resolver_for(cipher: CredentialCipher, row: Any, *, credential: str) -> CatalogModelResolver:
    """造一个解析来源：罐头会话给这一行，凭据按给定明文加密后落进那一行。"""

    if credential:
        row.provider.credential_ciphertext = cipher.encrypt(credential)
    return CatalogModelResolver(
        settings=OFFLINE_LLM_SETTINGS,
        session_factory=catalog_session_factory(row),
        cipher=cipher,
    )


def test_the_resolver_does_not_look_at_the_enabled_flags() -> None:
    """解析不重判可用性：停用的渠道与模型照样解析得到客户端。

    接手一条在途运行时用的就是这条路径——那一轮的模型可能在排空期间被停用，而接手不过
    「开始运行之前解析」那道 HTTP 门；中途换一个模型比让上游自己失败更糟。
    """

    cipher = CredentialCipher(Fernet.generate_key().decode())
    row = CatalogRow(
        provider=LlmProvider.OPENAI_COMPATIBLE.value,
        base_url="https://stopped.example.com/v1",
        upstream_model_name="alpha",
        enabled=False,
    )
    resolver = _resolver_for(cipher, row, credential="sk-stopped")

    client = run(resolver.resolve_client(uuid4()))

    assert client.model_name == "alpha"


def test_the_resolver_refuses_a_model_that_is_gone_from_the_catalog() -> None:
    """目录里查不到那一条时明确失败（本表只有停用、没有删除，所以这是不变量被破坏）。"""

    cipher = CredentialCipher(Fernet.generate_key().decode())
    resolver = CatalogModelResolver(
        settings=OFFLINE_LLM_SETTINGS,
        session_factory=catalog_session_factory(None),
        cipher=cipher,
    )

    with pytest.raises(RunModelUnresolvedError):
        run(resolver.resolve_client(uuid4()))


def test_the_assembly_builds_the_resolving_wrapper_without_touching_the_database() -> None:
    """不注入模型也不注入解析来源时，装配出来的就是生产那条链，而且不连库。

    模型目录为空时进程照样起得来：装配期既不读环境变量、也不查库，失败推到真的有人提问那一刻。
    本用例不传 ``session_factory``，所以一旦装配期去建连就会报错。
    """

    runtime = build_runtime()

    assert runtime.graph is not None


# 4、压缩触发点跟着选中的模型窗口走。


def test_a_smaller_model_window_moves_the_compression_trigger_earlier() -> None:
    """同一段历史，换一个窗口小得多的模型，摘要那次调用就出现了。

    观察量是「摘要模型被调了几次」：窗口取大的一侧只花一次回答调用，窗口取小的一侧多出一次
    摘要调用。窗口来自**当轮选中的那条模型快照**（经运行上下文进中间件），两个假模型自己的
    ``profile`` 完全相同，所以差别只可能来自选中的模型。

    顺带钉住「每次调用恰好一条记录」：摘要那次调用也走同一条包装链，它记一条——重复包一层
    用量采集的话，这一轮的记录数会变成调用数的两倍。
    """

    assert _measured_run(factor=1) == (2, 2), "窗口不够大时必须先摘要再回答，两次调用两条记录"
    assert _measured_run(factor=10) == (1, 1), "窗口足够大时只有一次回答调用"


def _measured_run(*, factor: int) -> tuple[int, int]:
    """在一条新会话上跑同样的三轮，返回第三轮的 ``(模型调用次数, 用量记录条数)``。

    前两轮用窗口足够大的模型产出历史（第一答足够长，历史才会越过小窗口的触发线），第三轮换成
    被测窗口。
    """

    model_id = uuid4()
    meter = named_fake("长" * 2000, "alpha", more=("短答", "第三答"))
    collector = RecordingCollector()
    runtime = build_runtime(resolver=StubResolver({model_id: meter}), usage_collector=collector)
    thread_id = uuid4()

    async def go() -> tuple[int, int]:
        big = AgentContext(llm_model=snapshot(model_id, window=10_000_000))
        await _drain(runtime, context=big, thread_id=thread_id, message="第一问")
        await _drain(runtime, context=big, thread_id=thread_id, message="第二问")
        seeded = await runtime.graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
        tokens = count_tokens_approximately(
            [*seeded.values["messages"], HumanMessage(content="第三问")],
            use_usage_metadata_scaling=True,
        )
        calls_before, records_before = meter.call_count, len(collector.records)
        await _drain(
            runtime,
            context=AgentContext(llm_model=snapshot(model_id, window=tokens * factor)),
            thread_id=thread_id,
            message="第三问",
        )
        return meter.call_count - calls_before, len(collector.records) - records_before

    return run(go())


async def _drain(runtime: AgentRuntime, *, context: AgentContext, thread_id: UUID, message: str) -> None:
    """把一次对话的事件流读干。"""

    async for _ in stream_agent_events(
        runtime.graph,
        message=message,
        thread_id=thread_id,
        context=context,
        langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
    ):
        pass


# 5、客户端缓存：同一个模型只构造一次，那一行内容变了才重建。


class FakeClock:
    """可推进的「当前时间」：缓存的信任窗口用不着真等 60 秒。"""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        """把时间往后拨；缓存只比「拨了多少秒」与窗口，所以不关心它是从哪一刻算起的。"""

        self.seconds += seconds


class MutableCatalog:
    """一份可以当场改的目录行，并记下目录表被读了几次。

    「读了几次」是「客户端被复用了没有」的等价观察量：一次解析要么走缓存直接给，要么真的
    去目录表取一行。
    """

    def __init__(self, row: Any) -> None:
        self.row = row
        self.reads = 0

    def session_factory(self) -> Any:
        """造一个每次读都返回当时这一行的 session 工厂。"""

        catalog = self

        class CannedResult:
            def first(self) -> Any:
                return catalog.row

        class CannedSession:
            async def execute(self, _statement: Any) -> Any:
                catalog.reads += 1
                return CannedResult()

            async def __aenter__(self) -> "CannedSession":
                return self

            async def __aexit__(self, *exc: Any) -> bool:
                return False

        return CannedSession


def full_row(**overrides: Any) -> CatalogRow:
    """一条完整的渠道行（openai_compatible，带着凭据），供缓存用例按需改。"""

    return CatalogRow(
        provider=LlmProvider.OPENAI_COMPATIBLE.value,
        base_url="https://origin.example.com/v1",
        upstream_model_name="alpha",
        **overrides,
    )


def cached_resolver(
    catalog: MutableCatalog, cipher: CredentialCipher, clock: FakeClock
) -> CatalogModelResolver:
    """造一个待测解析来源：目录用整份可改的罐头会话，时钟可推进。

    凭据在这里按明文加密后落进那一行：openai_compatible 要求凭据非空，而不带着凭据就只剩
    ollama 那个分支，缓存用例会失去代表性。
    """

    catalog.row.provider.credential_ciphertext = cipher.encrypt("sk-origin")
    return CatalogModelResolver(
        settings=OFFLINE_LLM_SETTINGS,
        session_factory=catalog.session_factory(),
        cipher=cipher,
        clock=clock,
    )


def test_a_second_resolve_of_the_same_model_reuses_the_same_client() -> None:
    """第二次解析同一个模型：拿到的是**同一个客户端对象**，目录表也只被读了一次。"""

    cipher = CredentialCipher(Fernet.generate_key().decode())
    catalog = MutableCatalog(full_row())
    resolver = cached_resolver(catalog, cipher, FakeClock())
    model_id = uuid4()

    first = run(resolver.resolve_client(model_id))
    second = run(resolver.resolve_client(model_id))

    assert second is first, "同一个模型第二次解析必须拿到同一个客户端，不能重新建连接"
    assert catalog.reads == 1, "命中缓存不再读目录表"


# 「一行内容」里改了就必须重建的那五项（对应 ``_catalog_fingerprint``）。
CHANGED_FIELDS = (
    "base_url",
    "provider",
    "credential_ciphertext",
    "upstream_model_name",
    "context_window",
)


def _change(row: CatalogRow, field: str, cipher: CredentialCipher) -> None:
    """按字段名改那一行的一项，模拟管理员在后台改了配置。"""

    if field == "base_url":
        row.provider.base_url = "https://changed.example.com/v1"
    elif field == "provider":
        row.provider.provider = LlmProvider.OLLAMA.value
    elif field == "credential_ciphertext":
        row.provider.credential_ciphertext = cipher.encrypt("sk-changed")
    elif field == "upstream_model_name":
        row.model.upstream_model_name = "beta"
    elif field == "context_window":
        row.model.context_window = 8192
    else:
        raise AssertionError(f"用例里没定义怎么改 {field}")


@pytest.mark.parametrize("field", CHANGED_FIELDS)
def test_a_changed_field_of_the_row_rebuilds_the_client(field: str) -> None:
    """地址、接入类型、凭据、上游模型名、上下文窗口：改了任一项都要换上新的客户端。

    先越过信任窗口再改：「改了最多 60 秒之后生效」正是缓存承诺的语义，窗口内本来就不查库。
    断言的是两个对象不相同（以及目录表真的被重读了），不是去看缓存字典。
    """

    cipher = CredentialCipher(Fernet.generate_key().decode())
    clock = FakeClock()
    catalog = MutableCatalog(full_row())
    resolver = cached_resolver(catalog, cipher, clock)
    model_id = uuid4()

    before = run(resolver.resolve_client(model_id))
    clock.advance(MODEL_CLIENT_CACHE_TTL_SECONDS + 1)
    _change(catalog.row, field, cipher)
    after = run(resolver.resolve_client(model_id))

    assert after is not before, f"{field} 改了之后必须重建客户端"
    assert catalog.reads == 2


def test_a_row_whose_content_is_unchanged_keeps_its_client() -> None:
    """信任窗口过期只重新校验那一行：内容没变就继续用同一个客户端。

    ``reads == 2`` 说明这一次确实重读了目录表——所以这不是「过期了也不查」，而是「查了没变
    就不换」。无条件重建的话一个被频繁使用的模型每分钟丢一次连接池与 TLS 连接。
    """

    cipher = CredentialCipher(Fernet.generate_key().decode())
    clock = FakeClock()
    catalog = MutableCatalog(full_row())
    resolver = cached_resolver(catalog, cipher, clock)
    model_id = uuid4()

    first = run(resolver.resolve_client(model_id))
    clock.advance(MODEL_CLIENT_CACHE_TTL_SECONDS + 1)
    second = run(resolver.resolve_client(model_id))

    assert second is first, "内容没变就必须复用同一个客户端"
    assert catalog.reads == 2, "过期后确实重新校验了那一行"


def test_a_change_inside_the_trust_window_is_not_picked_up_yet() -> None:
    """信任窗口内不查库：配置改了也要等窗口过期才生效（且不等超过 60 秒）。"""

    cipher = CredentialCipher(Fernet.generate_key().decode())
    catalog = MutableCatalog(full_row())
    resolver = cached_resolver(catalog, cipher, FakeClock())
    model_id = uuid4()

    first = run(resolver.resolve_client(model_id))
    _change(catalog.row, "base_url", cipher)
    second = run(resolver.resolve_client(model_id))

    assert second is first
    assert catalog.reads == 1, "窗口内一次目录表都不读"


def test_a_removed_row_evicts_the_cached_client() -> None:
    """那一行没了 → 淘汰：这回解析明确失败，把行放回来也不该拿到被淘汰的那个客户端。"""

    cipher = CredentialCipher(Fernet.generate_key().decode())
    clock = FakeClock()
    row = full_row()
    catalog = MutableCatalog(row)
    resolver = cached_resolver(catalog, cipher, clock)
    model_id = uuid4()

    first = run(resolver.resolve_client(model_id))
    clock.advance(MODEL_CLIENT_CACHE_TTL_SECONDS + 1)
    catalog.row = None
    with pytest.raises(RunModelUnresolvedError):
        run(resolver.resolve_client(model_id))

    catalog.row = row
    again = run(resolver.resolve_client(model_id))

    assert again is not first, "那一行曾经消失过，缓存里那一条要已经淘汰而不是被留着"


def test_disabling_the_row_keeps_the_cached_client() -> None:
    """只是被停用不算「内容变了」：接手一条在途运行还要用同一个客户端跑完。

    过期后重新校验那一行时，模型自己的与所属渠道的启用位都不参与比较：停用之后不会再被新的
    选择解析到（那道 HTTP 门先拦住），而已经选过它的在途运行不能被换掉客户端。
    """

    cipher = CredentialCipher(Fernet.generate_key().decode())
    clock = FakeClock()
    catalog = MutableCatalog(full_row())
    resolver = cached_resolver(catalog, cipher, clock)
    model_id = uuid4()

    first = run(resolver.resolve_client(model_id))
    clock.advance(MODEL_CLIENT_CACHE_TTL_SECONDS + 1)
    catalog.row.model.enabled = False
    catalog.row.provider.enabled = False
    second = run(resolver.resolve_client(model_id))

    assert second is first, "停用不改内容，不能重建客户端"
    assert catalog.reads == 2


def test_the_cache_stays_within_its_bound_and_drops_the_least_recently_used() -> None:
    """缓存有上界：用过的模型数超过上界以后，最久没被用到的那一个被淘汰。

    断言只看「重新解析拿到的是不是原来那个对象」：被淘汰的那一个会被重新构造，还在缓存里的
    那一个原样拿到。上界从 ``agent.limits`` 读，所以调数值不改这个用例。
    """

    cipher = CredentialCipher(Fernet.generate_key().decode())
    catalog = MutableCatalog(full_row())
    resolver = cached_resolver(catalog, cipher, FakeClock())
    model_ids = [uuid4() for _ in range(MODEL_CLIENT_CACHE_MAX_ENTRIES + 1)]

    async def resolve_each(ids: list[UUID]) -> list[Any]:
        return [await resolver.resolve_client(model_id) for model_id in ids]

    used = run(resolve_each(model_ids))
    oldest, newest = run(resolve_each([model_ids[0], model_ids[-1]]))

    assert newest is used[-1], "最近用过的那一个还在缓存里"
    assert oldest is not used[0], "最久没被用到的那一个已经被淘汰，重新构造了客户端"


def test_the_availability_gate_rejects_a_just_disabled_model_though_it_is_cached() -> None:
    """可用性判定不走这个缓存：缓存里还留着这一条的客户端，那道门照样立刻拒。

    两个方向一起看才说明问题：同一时刻、同一份目录行，开始运行之前那道门（真实
    ``LlmModelSelectionService``）已经拒了，而解析包装仍能把缓存里的客户端交给接手的那一轮。
    """

    cipher = CredentialCipher(Fernet.generate_key().decode())
    model_id = uuid4()
    clock = FakeClock()
    catalog = MutableCatalog(full_row(model_id=model_id, display_name="停用前"))
    resolver = cached_resolver(catalog, cipher, clock)

    cached = run(resolver.resolve_client(model_id))
    catalog.row.model.enabled = False

    gate = LlmModelSelectionService(catalog.session_factory())
    with pytest.raises(LlmModelUnavailableError):
        run(gate.resolve_for_run(model_id))

    assert run(resolver.resolve_client(model_id)) is cached, "在途运行仍用缓存里那个客户端跑完"
