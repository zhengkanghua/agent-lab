"""``/agent/threads`` 三条路由的完全离线测试：列表、历史回放、删除。

替身只做在两个边界上：模型换成假的、checkpointer 换成 ``InMemorySaver``、会话归属换成内存替身。
路由逻辑、归属判断的调用顺序、回放翻译都是真实代码。

**本文件最在意的三件事**，按重要性排序：

1. 别人的会话一律 404，且**什么都没做**——不回放内容，更不删历史。只断言状态码不够：
   一个「先删了历史、再发现不是你的」的实现同样返回 404。
2. 不存在的会话与别人的会话不可区分。能区分就能枚举。
3. 删除的两步顺序（先清历史、后删归属记录）。顺序反了会留下查不到也删不掉的孤儿历史。

不连 PostgreSQL、Qdrant，不访问网络，也不调真实大模型。
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from langchain_core.messages import AIMessage

from tests.agent_helpers import ScriptedChatModel
from tests.app_helpers import create_agent_app, seed_owned_thread, send


def run(coroutine: Any) -> Any:
    """执行异步 HTTP 测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


def scripted(*answers: str) -> ScriptedChatModel:
    """构造一个按顺序给出这些答案的假模型。"""

    return ScriptedChatModel(
        responses=[AIMessage(content=answer) for answer in answers]
    )


async def within_lifespan(
    app: FastAPI,
    work: Callable[[httpx.AsyncClient], Awaitable[Any]],
) -> Any:
    """在**同一个** lifespan 内跑完 ``work`` 里的全部请求。

    Args:
        app: 待测应用。
        work: 收到 HTTP 客户端后执行若干请求的协程函数。

    Returns:
        ``work`` 的返回值。

    Notes:
        为什么必须有这个helper：``agent_runtime_factory`` 在**每次**进入 lifespan 时都会被调用，
        于是每次都装一个全新的 ``InMemorySaver``。用 ``send()`` 发两个请求等于开了两次 lifespan，
        第一个请求写进历史的东西在第二个请求里已经不存在了——「先聊一轮、再回放」这类用例必须
        待在一个 lifespan 里才成立。

        同理，要检查 checkpointer 内容也得在这里面做：lifespan 退出后
        ``app.state.agent_runtime`` 会被置成 ``None``。
    """

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await work(client)


async def start_conversation(client: httpx.AsyncClient, message: str) -> UUID:
    """发起一轮真实对话，返回服务端建出来的会话 id。

    Args:
        client: 处于 lifespan 内的客户端。
        message: 提问内容，也会成为会话标题。

    Returns:
        ``done`` 事件里带回的会话 id。

    Notes:
        走真实的 ``POST /agent/chat``，所以 checkpointer 里会留下真实历史结构——回放用例断言的
        正是那个结构。自己往 saver 里塞手写消息也能让回放测试通过，但那证明的是「实现和我编的
        假数据一致」。
    """

    response = await client.post("/agent/chat", json={"message": message})
    frames = [frame for frame in response.text.split("\n\n") if frame.strip()]
    events = [
        json.loads(frame.removeprefix("data: "))
        for frame in frames
        if frame.startswith("data: ")
    ]
    done = [event for event in events if event["event"] == "done"]
    assert len(done) == 1, f"没拿到 done 事件，实际事件：{events}"
    return UUID(done[0]["thread_id"])


def test_list_returns_only_the_current_accounts_threads() -> None:
    """列表里不出现别人的会话，``total`` 也不把别人的算进去。

    ``total`` 那半同样重要：漏掉归属条件的 count 会让界面显示「共 9 个」却只列出 3 个，
    而这个差值本身就泄露了别的账号有多少会话。
    """

    app, _search = create_agent_app(scripted("答案"))
    mine = [uuid4(), uuid4()]
    for thread_id in mine:
        seed_owned_thread(app, thread_id)
    for _ in range(3):
        seed_owned_thread(app, uuid4(), user_id=uuid4())

    response = run(send(app, "GET", "/agent/threads"))

    assert response.status_code == 200
    body = response.json()
    assert {item["thread_id"] for item in body["items"]} == {
        str(thread_id) for thread_id in mine
    }
    assert body["total"] == 2


def test_list_is_ordered_by_most_recent_activity_first() -> None:
    """最近聊过的排在最前。"""

    app, _search = create_agent_app(scripted("答案"))
    base = datetime.now(UTC)
    oldest, middle, newest = uuid4(), uuid4(), uuid4()
    seed_owned_thread(app, oldest, title="最早", last_active_at=base - timedelta(hours=2))
    seed_owned_thread(app, newest, title="最新", last_active_at=base)
    seed_owned_thread(app, middle, title="中间", last_active_at=base - timedelta(hours=1))

    response = run(send(app, "GET", "/agent/threads"))

    assert [item["title"] for item in response.json()["items"]] == [
        "最新",
        "中间",
        "最早",
    ]


def test_total_is_independent_of_the_page_window() -> None:
    """``total`` 是账号的会话总数，不随 limit/offset 变。

    界面靠它显示「共 N 个」和算总页数，等于当前页条数的话翻到最后一页就会显示错的总量。
    """

    app, _search = create_agent_app(scripted("答案"))
    for index in range(7):
        seed_owned_thread(
            app,
            uuid4(),
            title=f"会话 {index}",
            last_active_at=datetime.now(UTC) - timedelta(minutes=index),
        )

    response = run(send(app, "GET", "/agent/threads?limit=2&offset=4"))

    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 7


def test_offset_past_the_end_returns_an_empty_page_with_the_real_total() -> None:
    """越界的 offset 给空列表加真实总数，不是 404。

    前端删掉最后一页的最后一条后仍停在那一页，这时需要 ``total`` 才能算出该退回哪页。
    """

    app, _search = create_agent_app(scripted("答案"))
    seed_owned_thread(app, uuid4())

    response = run(send(app, "GET", "/agent/threads?offset=500"))

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 1}


def test_page_size_beyond_the_maximum_is_rejected() -> None:
    """limit 超过上限是 422，不是「悄悄按上限截断」。

    悄悄截断会让前端以为自己拿到了 500 条、其实只有 100 条，翻页逻辑随之算错。
    """

    app, _search = create_agent_app(scripted("答案"))

    assert run(send(app, "GET", "/agent/threads?limit=101")).status_code == 422
    assert run(send(app, "GET", "/agent/threads?limit=0")).status_code == 422
    assert run(send(app, "GET", "/agent/threads?offset=-1")).status_code == 422


def test_replay_returns_the_turns_that_were_actually_stored() -> None:
    """真聊两轮，再回放，拿回同样的两轮问答。

    端到端走的是真实链路：真图、真中间件、真 checkpointer 读写、真回放翻译。只有模型和存储介质
    是替身。这条用例是「点进历史会话能看到之前聊了什么」这个需求的直接验证。
    """

    app, _search = create_agent_app(scripted("降了 25 个基点。", "上周四。"))

    async def work(client: httpx.AsyncClient) -> dict[str, Any]:
        thread_id = await start_conversation(client, "央行降息了吗")
        await client.post(
            "/agent/chat",
            json={"message": "什么时候", "thread_id": str(thread_id)},
        )
        response = await client.get(f"/agent/threads/{thread_id}/messages")
        assert response.status_code == 200
        return response.json()

    body = run(within_lifespan(app, work))

    assert [(turn["question"], turn["answer"]) for turn in body["turns"]] == [
        ("央行降息了吗", "降了 25 个基点。"),
        ("什么时候", "上周四。"),
    ]
    assert body["summarized"] is False
    assert body["summary"] is None


def test_replay_of_a_thread_with_no_stored_history_is_an_empty_turn_list() -> None:
    """归属记录有、历史没有时回放空列表，而不是 404 或 500。

    这种状态是真实存在的：会话行在流开始**前**就写好了，之后模型调用失败就不会留下任何消息。
    此时会话确实属于当前账号，404 说不通；用户点进去看到空的、可以接着聊，才是对的。
    """

    app, _search = create_agent_app(scripted("答案"))
    thread_id = uuid4()
    seed_owned_thread(app, thread_id)

    response = run(send(app, "GET", f"/agent/threads/{thread_id}/messages"))

    assert response.status_code == 200
    body = response.json()
    assert body["turns"] == []
    assert body["thread_id"] == str(thread_id)
    assert body["summarized"] is False


def test_replaying_someone_elses_thread_is_404_and_leaks_no_content() -> None:
    """别人的会话回放 404，而且响应体里没有那段历史的任何字样。

    第二半是真正的安全断言。只查状态码的话，一个「先读出内容、再发现不是你的、然后把内容塞进
    错误详情里」的实现照样通过。
    """

    app, _search = create_agent_app(scripted("这是别人的秘密答案"))

    async def work(client: httpx.AsyncClient) -> httpx.Response:
        # 用当前账号真聊一轮以攒下历史，然后把归属改成别人的，模拟「这个 id 的历史存在，但不属于我」。
        thread_id = await start_conversation(client, "这是别人的秘密提问")
        app.state.offline_threads.threads[thread_id].user_id = uuid4()
        return await client.get(f"/agent/threads/{thread_id}/messages")

    response = run(within_lifespan(app, work))

    assert response.status_code == 404
    assert response.json()["code"] == "agent_thread_not_found"
    assert "秘密提问" not in response.text
    assert "秘密答案" not in response.text


def test_replaying_an_unknown_thread_looks_exactly_like_someone_elses() -> None:
    """不存在的会话与别人的会话返回同一个状态码和 code。

    两者刻意不可区分：能区分就能拿这个接口当预言机去枚举有效 id。
    """

    app, _search = create_agent_app(scripted("答案"))
    someone_elses = uuid4()
    seed_owned_thread(app, someone_elses, user_id=uuid4())

    unknown_response = run(
        send(app, "GET", f"/agent/threads/{uuid4()}/messages")
    )
    foreign_response = run(
        send(app, "GET", f"/agent/threads/{someone_elses}/messages")
    )

    assert unknown_response.status_code == foreign_response.status_code == 404
    assert unknown_response.json() == foreign_response.json()


def test_malformed_thread_id_in_the_path_is_422() -> None:
    """路径里不是 UUID 就 422，不会走到归属校验。"""

    app, _search = create_agent_app(scripted("答案"))

    assert run(send(app, "GET", "/agent/threads/not-a-uuid/messages")).status_code == 422


def test_deleting_a_thread_clears_both_the_history_and_the_ownership_row() -> None:
    """删除同时清掉 checkpointer 历史和归属记录，两边都要空。

    只断言归属记录没了是不够的：那样留下的历史查不到也删不掉，只能等 ``prune-orphan-threads``
    来收，而在此之前它一直占着库。
    """

    app, _search = create_agent_app(scripted("答案"))

    async def work(client: httpx.AsyncClient) -> dict[str, Any]:
        thread_id = await start_conversation(client, "待删除的会话")
        checkpointer = app.state.agent_runtime.checkpointer
        config = {"configurable": {"thread_id": str(thread_id)}}
        assert await checkpointer.aget_tuple(config) is not None, "历史没写进去，用例失去意义"

        response = await client.delete(f"/agent/threads/{thread_id}")

        return {
            "status": response.status_code,
            "body": response.json(),
            "thread_id": thread_id,
            "history_left": await checkpointer.aget_tuple(config),
        }

    result = run(within_lifespan(app, work))

    assert result["status"] == 200
    assert result["body"] == {"thread_id": str(result["thread_id"])}
    assert result["history_left"] is None
    assert result["thread_id"] not in app.state.offline_threads.threads
    assert app.state.offline_threads.deleted == [result["thread_id"]]


def test_deleting_someone_elses_thread_leaves_their_history_intact() -> None:
    """别人的会话删不掉：404，而且**他的历史一条都没少**。

    这是本文件最重要的一条。只断言 404 挡不住「先调 ``adelete_thread`` 清历史、再校验归属」
    这种写法——那样返回的也是 404，但受害者的历史已经没了，而且不可恢复。归属校验必须在
    任何删除动作**之前**。
    """

    app, _search = create_agent_app(scripted("答案"))

    async def work(client: httpx.AsyncClient) -> dict[str, Any]:
        thread_id = await start_conversation(client, "别人的会话")
        app.state.offline_threads.threads[thread_id].user_id = uuid4()

        response = await client.delete(f"/agent/threads/{thread_id}")

        checkpointer = app.state.agent_runtime.checkpointer
        stored = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": str(thread_id)}}
        )
        return {
            "status": response.status_code,
            "code": response.json()["code"],
            "history_left": stored,
            "thread_id": thread_id,
        }

    result = run(within_lifespan(app, work))

    assert result["status"] == 404
    assert result["code"] == "agent_thread_not_found"
    assert result["history_left"] is not None
    # 归属记录也还在——它是别人的，我们无权删。
    assert result["thread_id"] in app.state.offline_threads.threads
    assert app.state.offline_threads.deleted == []


def test_deleting_the_same_thread_twice_is_404_the_second_time() -> None:
    """重复删除第二次是 404：归属记录已经不在了。

    前端在列表里连点两下删除会走到这条路径，所以它得是个明确的 404 而不是 500。
    """

    app, _search = create_agent_app(scripted("答案"))
    thread_id = uuid4()
    seed_owned_thread(app, thread_id)

    async def work(client: httpx.AsyncClient) -> tuple[int, int]:
        first = await client.delete(f"/agent/threads/{thread_id}")
        second = await client.delete(f"/agent/threads/{thread_id}")
        return first.status_code, second.status_code

    assert run(within_lifespan(app, work)) == (200, 404)


def test_deleting_an_unknown_thread_looks_exactly_like_someone_elses() -> None:
    """不存在的会话删除失败的形状与别人的一致。"""

    app, _search = create_agent_app(scripted("答案"))
    someone_elses = uuid4()
    seed_owned_thread(app, someone_elses, user_id=uuid4())

    unknown = run(send(app, "DELETE", f"/agent/threads/{uuid4()}"))
    foreign = run(send(app, "DELETE", f"/agent/threads/{someone_elses}"))

    assert unknown.status_code == foreign.status_code == 404
    assert unknown.json() == foreign.json()


def test_a_deleted_thread_cannot_be_continued() -> None:
    """删掉之后同一个 id 不能再续聊——``POST /agent/chat`` 认不出它了。

    这条把删除和归属两条路径接起来：删除只清了两处存储，但用户感知到的「不能再用了」是由
    ``ensure_thread`` 的归属校验保证的。缺了它，删除就只是「列表里看不见了」。
    """

    app, _search = create_agent_app(scripted("答案", "不该被看到"))
    thread_id = uuid4()
    seed_owned_thread(app, thread_id)

    async def work(client: httpx.AsyncClient) -> int:
        await client.delete(f"/agent/threads/{thread_id}")
        response = await client.post(
            "/agent/chat",
            json={"message": "接着聊", "thread_id": str(thread_id)},
        )
        return response.status_code

    assert run(within_lifespan(app, work)) == 404


def test_all_three_routes_require_credentials() -> None:
    """三条路由都要凭据。逐条验证，不假定「挂在同一个路由器上就都被守住了」。

    路由器级依赖确实是一次性生效的，但 ``@router.get`` 上也各自写了一个
    ``Depends(current_superuser)``，将来有人清理「重复」的守卫时，这条能告出哪条路由被清漏了。
    """

    app, _search = create_agent_app(scripted("答案"), superuser=False)
    thread_id = uuid4()
    seed_owned_thread(app, thread_id)

    assert run(send(app, "GET", "/agent/threads")).status_code == 401
    assert run(
        send(app, "GET", f"/agent/threads/{thread_id}/messages")
    ).status_code == 401
    assert run(send(app, "DELETE", f"/agent/threads/{thread_id}")).status_code == 401
    # 拒绝发生在动手之前：归属记录必须还在。
    assert thread_id in app.state.offline_threads.threads


def test_openapi_declares_the_error_contract_for_every_thread_route() -> None:
    """三条路由在 OpenAPI 里都声明了失败分支，前端才能生成对应的类型。

    列表只会因数据库故障失败（没有 404——「没有会话」是空列表，不是错），另外两条都带 404。
    """

    app, _search = create_agent_app(scripted("答案"))

    paths = app.openapi()["paths"]
    assert set(paths["/agent/threads"]["get"]["responses"]) == {"200", "422", "503"}
    for method, path in (
        ("get", "/agent/threads/{thread_id}/messages"),
        ("delete", "/agent/threads/{thread_id}"),
    ):
        assert set(paths[path][method]["responses"]) == {"200", "404", "422", "503"}


def test_thread_summary_does_not_expose_message_content() -> None:
    """列表项只有导航需要的四个字段，不含消息内容。

    多带一个「最后一条回答」会让列表变成 checkpointer 内容的副本，而历史压缩是破坏性的：
    副本迟早和模型真实看到的上下文分叉，界面显示的和模型记得的对不上。
    """

    app, _search = create_agent_app(scripted("答案"))
    seed_owned_thread(app, uuid4())

    item = run(send(app, "GET", "/agent/threads")).json()["items"][0]

    assert set(item) == {"thread_id", "title", "created_at", "last_active_at"}


def test_list_reflects_a_thread_created_by_chatting() -> None:
    """新开一轮对话后，它立刻出现在列表里，标题是首条提问。

    把两条路径接起来：``POST /agent/chat`` 写的归属记录，和 ``GET /agent/threads`` 读的，
    必须是同一份数据。
    """

    app, _search = create_agent_app(scripted("答案"))

    async def work(client: httpx.AsyncClient) -> dict[str, Any]:
        thread_id = await start_conversation(client, "央行降息了吗")
        response = await client.get("/agent/threads")
        return {"thread_id": thread_id, "body": response.json()}

    result = run(within_lifespan(app, work))

    assert result["body"]["total"] == 1
    item = result["body"]["items"][0]
    assert item["thread_id"] == str(result["thread_id"])
    assert item["title"] == "央行降息了吗"
