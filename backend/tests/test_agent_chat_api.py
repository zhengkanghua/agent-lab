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
from uuid import UUID, uuid4

import httpx
import openai
from fastapi import FastAPI
from langchain_core.messages import AIMessage

from agent_lab.agent.prompts import DEFAULT_SYSTEM_PROMPT
from agent_lab.agent.model_catalog import LlmModelNotListedError
from agent_lab.auth.dependencies import current_active_user, current_superuser
from agent_lab.schemas.agent_chat import AgentChatEventEnvelope
from tests.agent_helpers import (
    FailingChatModel,
    ScriptedChatModel,
    StreamingChatModel,
)
from tests.app_helpers import create_agent_app, seed_owned_thread, send
from tests.auth_helpers import SUPERUSER_ID


def run(coroutine: Any) -> Any:
    """执行异步 HTTP 测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


def app_for(
    model: Any,
    *,
    superuser: bool = True,
    anonymous: bool = False,
    agent_build_error: Exception | None = None,
    model_catalog_error: Exception | None = None,
) -> tuple[FastAPI, Any]:
    """本文件的简写:转调共享的 ``create_agent_app``。

    保留这层薄封装只为让下面几十个用例的调用点不用改动；真正的装配逻辑在
    ``tests/app_helpers.py``，与会话记录测试共用一份。
    """

    return create_agent_app(
        model,
        superuser=superuser,
        anonymous=anonymous,
        agent_build_error=agent_build_error,
        model_catalog_error=model_catalog_error,
    )


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


def test_client_supplied_thread_id_is_honoured_when_owned() -> None:
    """带上**自己的** thread_id 就接着聊，``done`` 事件回同一个 id。

    这条以前不需要预置归属：那时任何 UUID 都能直接用。现在必须先在会话表里有一行属于当前账号的
    记录，否则会被判成别人的会话——这正是本次要修的漏洞，所以测试形状跟着变是对的。
    """

    model = scripted("第一轮", "第二轮")
    app, _search = app_for(model)
    thread_id = uuid4()
    seed_owned_thread(app, thread_id)

    _response, frames = run(chat(app, message="问题", thread_id=str(thread_id)))

    done = [each for each in payloads(frames) if each["event"] == "done"]
    assert done[0]["thread_id"] == str(thread_id)


def test_thread_id_owned_by_another_account_is_rejected_before_the_model_runs() -> None:
    """别人的 thread_id 一律 404，而且模型一次都不该被调用。

    这是本次改动的核心断言，两半都重要：

    - 404 而不是 403：区分开会泄露「这个 id 是否存在」，等于给猜 id 的人一个预言机。
    - 模型没被调用：证明校验发生在流开始**之前**。这一点决定了失败能不能带 HTTP 状态码——
      流一旦开始，响应头就发出去了，之后只能塞一个 error 事件，前端要走完全不同的分支。
      如果哪天有人把校验挪到生成器里面，这条断言会失败。
    """

    model = scripted("不该被看到")
    app, _search = app_for(model)
    someone_elses = uuid4()
    seed_owned_thread(app, someone_elses, user_id=uuid4())

    response = run(
        send(
            app,
            "POST",
            "/agent/chat",
            json={"message": "问题", "thread_id": str(someone_elses)},
        )
    )

    assert response.status_code == 404
    assert response.json()["code"] == "agent_thread_not_found"
    # 响应体是 JSON 而不是 text/event-stream，这本身就证明流没开始过：校验要是挪进了生成器
    # 内部，这里拿到的会是 200 加一个 SSE 流。
    assert response.headers["content-type"].startswith("application/json")
    assert model.received_messages == []


def test_unknown_thread_id_is_rejected_the_same_way_as_someone_elses() -> None:
    """完全不存在的 thread_id 与「别人的」返回同一个 code 和状态码。

    两者刻意不可区分：能区分就能枚举。这条测试把这个性质钉住，防止将来有人「顺手」给不存在的
    情况换个更精确的 code。
    """

    model = scripted("不该被看到")
    app, _search = app_for(model)

    response = run(
        send(
            app,
            "POST",
            "/agent/chat",
            json={"message": "问题", "thread_id": str(uuid4())},
        )
    )

    assert response.status_code == 404
    assert response.json()["code"] == "agent_thread_not_found"
    assert model.received_messages == []


def test_new_conversation_records_ownership_for_the_current_account() -> None:
    """不带 thread_id 时服务端建会话，归属记到当前账号，标题取首条提问。"""

    model = scripted("答案")
    app, _search = app_for(model)

    _response, frames = run(chat(app, message="央行降息了吗"))

    done = [each for each in payloads(frames) if each["event"] == "done"]
    created = UUID(done[0]["thread_id"])
    record = app.state.offline_threads.threads[created]
    assert record.user_id == SUPERUSER_ID
    assert record.title == "央行降息了吗"


def test_account_preference_prompt_reaches_the_model_on_a_new_thread() -> None:
    """账号在偏好里配了提示词 → 新建会话时该会话存下这份，且本轮就生效。

    提示词不再从请求体来（那个字段已删除），所以这条改从偏好构造前提。它一次性穿过三处：
    偏好取值、会话快照的写入、运行上下文的组装——这正是只有 HTTP 层才能观察到的路径。
    """

    model = scripted("答案")
    app, _search = app_for(model)
    account_id = SUPERUSER_ID
    app.state.offline_threads.prompts[account_id] = "只用一句话回答。"

    run(chat(app, message="问题"))

    system = model.received_messages[0][0]
    # startswith 而非相等：末尾有一段运行时注入的当前日期（见 middleware.append_current_date），
    # 日期怎么追加由 test_agent_middleware.py 钉，这里只关心自定义那份有没有走通到模型。
    assert str(system.content).startswith("只用一句话回答。")


def test_account_without_a_preference_uses_the_default_prompt() -> None:
    """账号没配过提示词 → 会话存空值，本轮使用服务端内置默认提示词。

    「没配过」与「配了一份空提示词」不是一回事：前者回落到默认，后者会让模型失去角色约束。
    这里构造的是前者——偏好字典里没有这个账号。
    """

    model = scripted("答案")
    app, _search = app_for(model)

    run(chat(app, message="问题"))

    system = model.received_messages[0][0]
    assert str(system.content).startswith(DEFAULT_SYSTEM_PROMPT)


def test_changing_the_preference_does_not_affect_an_existing_thread() -> None:
    """会话建立后账号改了偏好 → 已开始的会话续聊时仍用会话里那份。

    这是「会话级快照」的核心保证：会话内的约束不中途变化，前后回答才可比。反过来说，
    如果续聊时回读偏好表，用户在设置页改一次提示词就会把正在进行的会话换掉。
    """

    model = scripted("第一轮回答", "第二轮回答")
    app, _search = app_for(model)
    app.state.offline_threads.prompts[SUPERUSER_ID] = "第一版提示词。"

    _, frames = run(chat(app, message="第一轮"))
    thread_id = _thread_id_from_frames(frames)

    # 会话开完再改偏好：只该影响新开的会话。
    app.state.offline_threads.prompts[SUPERUSER_ID] = "第二版提示词。"
    run(chat(app, message="第二轮", thread_id=thread_id))

    # 一次 run 一次模型调用，所以第二轮的系统消息在第 2 条记录里。
    # 它取自会话快照，仍是第一版。
    assert len(model.received_messages) == 2
    assert str(model.received_messages[1][0].content).startswith("第一版提示词。")


def test_a_new_thread_after_the_change_uses_the_new_preference() -> None:
    """改过偏好之后新开的会话用新值——与上一条合起来构成完整的「只影响新会话」语义。"""

    model = scripted("第一轮回答", "新会话回答")
    app, _search = app_for(model)
    app.state.offline_threads.prompts[SUPERUSER_ID] = "第一版提示词。"

    run(chat(app, message="第一轮"))
    app.state.offline_threads.prompts[SUPERUSER_ID] = "第二版提示词。"
    run(chat(app, message="新会话"))

    # 新会话从头开始，用的是改过之后的偏好。
    assert len(model.received_messages) == 2
    assert str(model.received_messages[1][0].content).startswith("第二版提示词。")


def _thread_id_from_frames(frames: list[str]) -> str:
    """从 SSE 帧里取 done 事件带回的会话 id。"""

    for frame in frames:
        payload = json.loads(frame.removeprefix("data: "))
        if payload.get("event") == "done":
            return str(payload["thread_id"])
    raise AssertionError("流里没有 done 事件，拿不到会话 id")


def test_default_prompt_endpoint_returns_the_same_constant() -> None:
    """端点返回的是不含日期的那份原始常量。

    这份是给前端编辑框当模板用的：用户看到的应该是可以改的回答规范，不该出现「当前日期：
    2026-03-17」这种他一改就会过期的运行时事实。日期在每次运行时由服务端追加。
    """

    model = scripted("答案")
    app, _search = app_for(model)

    response = run(send(app, "GET", "/agent/default-prompt"))

    assert response.status_code == 200
    assert response.json() == {"system_prompt": DEFAULT_SYSTEM_PROMPT}


def test_request_without_credentials_is_rejected_before_calling_the_model() -> None:
    """没有凭据就进不来，而且模型一次都不该被调用。

    缺凭据时由 fastapi-users 给出 401（不是 403——它连身份都没确认，谈不上权限不足）。
    关键断言是第二条：拒绝发生在调模型之前，不会白花一次 token。

    普通账号能不能进由下一条测试覆盖；这里只验「没登录进不来」这一半。
    """

    model = scripted("答案")
    # 用 anonymous 而不是 superuser=False：后者是「登录了但普通」，现在同样能进，
    # 拿它测 401 会变成一条恒假的断言。
    app, _search = app_for(model, anonymous=True)

    response = run(send(app, "POST", "/agent/chat", json={"message": "问题"}))

    assert response.status_code == 401
    assert model.received_messages == []


def test_agent_chat_is_open_to_a_regular_account() -> None:
    """普通登录账号能发起对话——这是本次改动要放开的行为。

    上面那条只证明「没凭据进不来」，一个永远 401 的实现也能通过它。这条补上另一个方向：
    普通账号（``superuser=False``）拿到的是 200 而不是 403。

    权限放开后靠什么挡跨账号读对话：会话归属（``AgentThreadService`` 按 ``user_id`` 过滤），
    它按账号判断、与角色无关，所以放开角色不影响隔离。那部分由会话归属自己的测试覆盖。
    """

    model = scripted("答案")
    app, _search = app_for(model, superuser=False)

    response = run(send(app, "POST", "/agent/chat", json={"message": "问题"}))

    assert response.status_code == 200
    # 真的走到了模型，而不是被某个中间层悄悄短路成空响应。
    assert model.received_messages != []


def test_agent_routes_are_guarded_by_active_user_not_superuser() -> None:
    """结构性断言：**每个** Agent 路由器挂的都是普通登录守卫，不是超级用户守卫。

    行为测试证明「普通账号能进」，但证明不了「没有哪个路由器被单独改成超级用户」——那会
    让对话能进、会话列表却 403，是一个只有点到那个入口才会发现的半开状态。这条直接查挂上去
    的是哪个依赖对象。

    断言的是「全部 ``/agent/`` 路由器都被守卫」而不是「恰好有一个路由器」：``/agent`` 前缀下
    现在有对话和会话记录两个路由器，以后可能更多，而这条测试要保住的性质是「没有一个漏掉守卫」。
    写成固定条数的话，新增一个路由器只会让它失败，而失败原因和它想防的风险无关。

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
    # 至少要找到对话和会话记录两个，否则说明上面那套反射没抓到东西，断言会变成空转。
    assert len(included) >= 2
    for router in included:
        guards = [dep.dependency for dep in router.include_context.dependencies]
        assert current_active_user in guards
        assert current_superuser not in guards


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


def test_a_model_name_not_in_the_catalog_yields_the_same_503() -> None:
    """启动时模型名校验失败，对外表现与 Agent 装配失败完全一致。

    与上一条成对：两者在 lifespan 里共用同一个 ``try``，因为对用户是同一件事——Agent 用不了，
    别的照用。钉住「同一个 code」是为了让前端只需要认一套文案；把校验挪出那个 try、让它
    冒到启动路径上，这条会红。
    """

    model = scripted("答案")
    app, search = app_for(
        model,
        model_catalog_error=LlmModelNotListedError("上游模型列表中没有以下模型：auto。"),
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
        "run_started",
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
