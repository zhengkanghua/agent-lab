"""``POST /agent/chat`` 与 ``GET /agent/default-prompt`` 的完全离线测试。

替身只做在两个边界上：``AgentRuntime`` 用真实实现（注入 fake 模型和 ``InMemorySaver``），
认证依赖用 ``auth_helpers`` 覆盖。也就是说「图怎么编译、事件怎么排序、错误怎么分类」仍由
真实代码决定，本文件验证的是 HTTP 层那几件事：SSE 帧格式、权限、会话 id 归属、
OpenAPI 契约，以及流开始前后两条不同的失败路径。

不连接 PostgreSQL、Qdrant，也不访问任何大模型。
"""

import asyncio
import json
from typing import Any

import httpx
import openai
from fastapi import FastAPI
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent_lab.agent.prompts import DEFAULT_SYSTEM_PROMPT
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.auth.dependencies import current_active_user, current_superuser
from agent_lab.config.llm import LlmProvider, LlmSettings
from agent_lab.schemas.agent_chat import AgentChatEventEnvelope
from tests.agent_helpers import (
    FailingChatModel,
    ScriptedChatModel,
    StreamingChatModel,
)
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_reader, allow_superuser


def run(coroutine: Any) -> Any:
    """执行异步 HTTP 测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


OFFLINE_LLM_SETTINGS = LlmSettings(
    provider=LlmProvider.OLLAMA,
    base_url="http://127.0.0.1:11434",
    model="offline-test-model",
    fallback_model="offline-test-fallback",
)


class FakeSearchService:
    """记录检索请求并返回空结果，不执行任何网络 I/O。

    本文件的用例都不触发工具调用，所以它主要有两个用处：证明 Agent 拿到的是同一个
    Service 实例，以及在 Agent 装配失败时充当「只读链路还活着」的探针。
    """

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def search(self, request: Any) -> list[Any]:
        """记录请求并返回空结果。"""

        self.calls.append(request)
        return []


class FakeSearchRuntime:
    """只暴露 API 与 Agent 装配所需的 ``service`` 字段。"""

    def __init__(self) -> None:
        self.service = FakeSearchService()
        self.closed = False

    async def close(self) -> None:
        """记录关闭，不访问外部资源。"""

        self.closed = True


def app_for(
    model: Any,
    *,
    superuser: bool = True,
    agent_build_error: Exception | None = None,
) -> tuple[FastAPI, FakeSearchRuntime]:
    """创建注入 fake 模型的应用。

    ``agent_build_error`` 非空时模拟「Agent 装配失败」，用来验证它不会连带把只读系统
    一起拖下线。
    """

    search_runtime = FakeSearchRuntime()

    def agent_factory(service: Any) -> AgentRuntime:
        if agent_build_error is not None:
            raise agent_build_error
        assert service is search_runtime.service
        return AgentRuntime.build(
            llm_settings=OFFLINE_LLM_SETTINGS,
            search_service=service,
            session_factory=None,  # type: ignore[arg-type]
            database_url="postgresql+psycopg://unused/unused",
            checkpointer=InMemorySaver(),
            model=model,
            # 退避是真 sleep，HTTP 层测的是 SSE 帧格式和状态码，不需要等。
            retry_initial_delay=0.0,
        )

    grant = allow_superuser if superuser else allow_reader
    app = grant(
        create_offline_app(
            runtime_factory=lambda: search_runtime,
            agent_runtime_factory=agent_factory,
        )
    )
    return app, search_runtime


async def send(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    """在显式 lifespan 内发送一个非流式 ASGI 请求。"""

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


async def chat(app: FastAPI, **payload: Any) -> tuple[httpx.Response, list[str]]:
    """发起一次 SSE 对话并把响应体按帧切开。

    用 ``client.stream`` 而不是普通请求：普通请求会等整个响应体收完，那样测不出「响应头
    先到、事件后到」，也测不出中途断开。
    """

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            async with client.stream(
                "POST", "/agent/chat", json=payload
            ) as response:
                body = "".join([chunk async for chunk in response.aiter_text()])
            return response, [
                frame for frame in body.split("\n\n") if frame.strip()
            ]


def payloads(frames: list[str]) -> list[dict[str, Any]]:
    """把 ``data:`` 帧解析成 JSON 对象，跳过心跳注释行。"""

    return [
        json.loads(frame.removeprefix("data: "))
        for frame in frames
        if frame.startswith("data: ")
    ]


def scripted(*answers: str) -> ScriptedChatModel:
    """构造一个按顺序给出这些答案的假模型。"""

    return ScriptedChatModel(
        responses=[AIMessage(content=answer) for answer in answers]
    )


def test_chat_streams_sse_frames_and_ends_with_done() -> None:
    model = scripted("降息 25 个基点。")
    app, _search = app_for(model)

    response, frames = run(chat(app, message="央行降息了吗"))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = payloads(frames)
    assert [each["event"] for each in events][-1] == "done"
    tokens = "".join(
        each["text"] for each in events if each["event"] == "token"
    )
    assert tokens == "降息 25 个基点。"


def test_every_frame_is_a_valid_envelope_member() -> None:
    """帧里的 JSON 必须能被 OpenAPI 里那个可判别联合解析。

    这条断言保证「文档里写的 schema」和「实际发出去的字节」是同一套东西——否则前端按
    生成的 TS 类型写代码会在运行时对不上。
    """

    model = StreamingChatModel(messages=iter([AIMessage(content="央行 降息 了")]))
    app, _search = app_for(model)

    _response, frames = run(chat(app, message="问题"))

    for raw in payloads(frames):
        AgentChatEventEnvelope.model_validate(raw)


def test_streaming_response_disables_proxy_buffering() -> None:
    model = scripted("答案")
    app, _search = app_for(model)

    response, _frames = run(chat(app, message="问题"))

    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"


def test_server_generates_thread_id_when_omitted() -> None:
    """不传 thread_id 时由服务端生成，并通过 done 事件告知。"""

    model = scripted("答案")
    app, _search = app_for(model)

    _response, frames = run(chat(app, message="问题"))

    done = [each for each in payloads(frames) if each["event"] == "done"]
    assert len(done) == 1
    assert done[0]["thread_id"]


def test_client_supplied_thread_id_is_honoured() -> None:
    model = scripted("第一轮", "第二轮")
    app, _search = app_for(model)
    thread_id = "8f14e45f-ceea-467a-9a3f-a1f1b8a1b1c1"

    _response, frames = run(chat(app, message="问题", thread_id=thread_id))

    done = [each for each in payloads(frames) if each["event"] == "done"]
    assert done[0]["thread_id"] == thread_id


def test_custom_system_prompt_reaches_the_model() -> None:
    model = scripted("答案")
    app, _search = app_for(model)

    run(chat(app, message="问题", system_prompt="只用一句话回答。"))

    system = model.received_messages[0][0]
    assert system.content == "只用一句话回答。"


def test_default_prompt_applies_when_not_supplied() -> None:
    model = scripted("答案")
    app, _search = app_for(model)

    run(chat(app, message="问题"))

    system = model.received_messages[0][0]
    assert system.content == DEFAULT_SYSTEM_PROMPT


def test_default_prompt_endpoint_returns_the_same_constant() -> None:
    model = scripted("答案")
    app, _search = app_for(model)

    response = run(send(app, "GET", "/agent/default-prompt"))

    assert response.status_code == 200
    assert response.json() == {"system_prompt": DEFAULT_SYSTEM_PROMPT}


def test_request_without_superuser_credentials_is_rejected() -> None:
    """没有超级用户凭据就进不来，而且模型一次都不该被调用。

    这里覆盖的是普通用户依赖、保留真实的超级用户检查，所以缺凭据时由 fastapi-users 给出
    401（不是 403——它连身份都没确认，谈不上权限不足）。关键断言是第二条：拒绝发生在
    调模型之前，不会白花一次 token。
    """

    model = scripted("答案")
    app, _search = app_for(model, superuser=False)

    response = run(send(app, "POST", "/agent/chat", json={"message": "问题"}))

    assert response.status_code == 401
    assert model.received_messages == []


def test_agent_routes_are_guarded_by_superuser_not_active_user() -> None:
    """结构性断言：Agent 路由挂的是超级用户守卫。

    上一条测试只能证明「没凭据进不来」，那连挂 ``current_active_user`` 也能通过。这条
    直接查挂上去的是哪个依赖对象，能挡住「有人把守卫降级成普通用户」这种改动。

    为什么要翻 ``include_context`` 而不是路由自己的 ``dependant``：这个版本的 FastAPI 把
    ``include_router(dependencies=...)`` 存在「被包含的路由器」上，匹配时才合进去，所以
    路由本身的依赖列表里看不到守卫。两个守卫又都由 fastapi-users 工厂生成、``__name__``
    一样，所以只能比对象身份，不能比名字。
    """

    app, _search = app_for(scripted("答案"))

    included = [
        router
        for router in app.routes
        if any(
            getattr(route, "path", "").startswith("/agent/")
            for route in getattr(
                getattr(router, "original_router", None), "routes", []
            )
        )
    ]
    assert len(included) == 1
    guards = [dep.dependency for dep in included[0].include_context.dependencies]
    assert current_superuser in guards
    assert current_active_user not in guards


def test_agent_build_failure_yields_503_without_breaking_search() -> None:
    """Agent 装配失败只影响 /agent/*，只读检索照常。

    这是失败半径的断言：一个缺失的模型凭据不该让整个只读系统下线。用 ``/vector-search``
    当探针而不是 ``/health``——后者真的会连 PostgreSQL，离线环境下它本来就是 503，
    证明不了任何事。
    """

    model = scripted("答案")
    app, search = app_for(
        model, agent_build_error=RuntimeError("缺少模型凭据")
    )

    chat_response = run(send(app, "POST", "/agent/chat", json={"message": "问题"}))
    search_response = run(
        send(app, "POST", "/vector-search", json={"query": "央行利率"})
    )

    assert chat_response.status_code == 503
    assert chat_response.json()["code"] == "agent_runtime_unavailable"
    assert search_response.status_code == 200
    assert len(search.service.calls) == 1


def test_pre_stream_failure_detail_carries_no_exception_text() -> None:
    model = scripted("答案")
    app, _search = app_for(
        model,
        agent_build_error=RuntimeError("password=hunter2 host=10.0.0.1"),
    )

    response = run(send(app, "POST", "/agent/chat", json={"message": "问题"}))

    payload = response.text
    assert "hunter2" not in payload
    assert "10.0.0.1" not in payload


def test_upstream_failure_arrives_as_error_event_with_status_200() -> None:
    """流已经开始后，失败只能是事件——状态码早就发出去了，改不了。"""

    model = FailingChatModel(error=openai.APITimeoutError(request=None))  # type: ignore[arg-type]
    app, _search = app_for(model)

    response, frames = run(chat(app, message="问题"))

    assert response.status_code == 200
    events = payloads(frames)
    assert events[-1]["event"] == "error"
    assert events[-1]["code"] == "llm_timeout"
    assert events[-1]["retryable"] is True


def test_empty_message_is_rejected_before_any_model_call() -> None:
    model = scripted("答案")
    app, _search = app_for(model)

    response = run(send(app, "POST", "/agent/chat", json={"message": "  "}))

    assert response.status_code == 422
    assert model.received_messages == []


def test_openapi_declares_the_discriminated_event_union() -> None:
    """SSE 的事件 schema 必须挂在 text/event-stream 上并带 discriminator。

    挂到 application/json 上是错的：这个接口从不返回 JSON 响应体，而前端代码生成器会
    按 media type 找 schema。
    """

    model = scripted("答案")
    app, _search = app_for(model)

    spec = app.openapi()
    content = spec["paths"]["/agent/chat"]["post"]["responses"]["200"]["content"]
    assert list(content) == ["text/event-stream"]
    envelope = spec["components"]["schemas"]["AgentChatEventEnvelope"]
    assert envelope["discriminator"]["propertyName"] == "event"
    assert set(envelope["discriminator"]["mapping"]) == {
        "token",
        "tool_call",
        "tool_result",
        "done",
        "error",
    }


def test_search_runtime_is_closed_even_though_agent_holds_its_service() -> None:
    """Agent 复用检索 Service，关闭顺序必须让它先撤、再关 Service 的持有者。"""

    model = scripted("答案")
    app, search = app_for(model)

    run(chat(app, message="问题"))

    assert search.closed is True
    assert app.state.agent_runtime is None
