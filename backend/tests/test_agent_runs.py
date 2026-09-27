"""一次运行的生命周期：脱离连接、被中断时的补写。

工单 01（脱离连接）：客户端在流中途断开（关页面、断网、切走会话）之后，这次运行继续跑到结束、
并把结果写进会话；下次打开这个会话读到完整答案。改动之前，断开就是取消——checkpoint 里只剩一条
提问。

工单 02（中断补写）：运行被中断（上游失败、超时、用户停止）时，服务端把**已经推出去、但尚未落库**
的那部分模型输出补写进会话。checkpoint 的写入时机是每个节点完整结束，不是每个 token，所以中断一个
正在输出答案的节点，那一整格都不落库；而用户屏幕上确实已经看到了半截文字。

**为什么这些断言走 HTTP 层**：它是现有最高的接缝。假模型 + 内存会话历史已经能把「用户可见的结果」
（回放的答案、运行是否还在跑）完整断言出来，不需要为测试多开一个内部读数点。

**为什么「中断」必须先真正消耗事件流**：见 ``tests/agent_helpers.disconnect_mid_stream`` 的说明。
进程内的 ``httpx.ASGITransport`` 造不出「客户端在流中途断开」，用它写的用例在改动前也会通过。
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import openai
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk

from agent_lab.agent.limits import RUN_ZOMBIE_THRESHOLD_SECONDS
from tests.agent_helpers import (
    StreamingChatModel,
    disconnect_mid_stream,
    open_chat_stream,
    run,
)
from tests.app_helpers import create_agent_app, seed_owned_thread, send


# 轮询间隔与总上限。**上限不是产品行为**，只防止用例在实现出错时自己挂住；用假模型与内存
# checkpoint 时这一轮在毫秒级结束，一两次轮询就够。
_POLL_INTERVAL_SECONDS = 0.02
_POLL_DEADLINE_SECONDS = 10.0


def _frames(text_chunks: list[str]) -> list[dict[str, Any]]:
    """把响应体分块拼起来，切成 ``data:`` 帧并解析成 JSON 对象。"""

    body = "".join(text_chunks)
    return [
        json.loads(frame.removeprefix("data: "))
        for frame in body.split("\n\n")
        if frame.startswith("data: ")
    ]


def _thread_id(events: list[dict[str, Any]]) -> UUID:
    """从事件里取会话 id。

    取 ``run_started`` 而不是 ``done``：断开发生在首帧之后，那时只有 ``run_started`` 到了。
    它还顺带证明「首个事件在运行真正开跑之前就发出来了」，这正是刷新之后能凭服务端上报判断
    「这个会话有运行在途」的前提。
    """

    for event in events:
        if event["event"] == "run_started":
            return UUID(event["thread_id"])
    raise AssertionError("首帧里没有 run_started，无法得知这次运行属于哪个会话")


async def _wait_for_answer(
    client: httpx.AsyncClient,
    thread_id: UUID,
    *,
    deadline_seconds: float = _POLL_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """轮询回放接口，直到这一轮出现答案；返回最后一次读到的回放响应。"""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    replay: dict[str, Any] = {}
    while loop.time() < deadline:
        response = await client.get(f"/agent/threads/{thread_id}/messages")
        assert response.status_code == 200
        replay = response.json()
        turns = replay.get("turns") or []
        if turns and turns[-1].get("answer"):
            return replay
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(f"运行没有在期限内留下答案：{replay}")


async def _ask_and_read_fully(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """正常（不断开地）问一轮并把整条流读完，返回解析后的事件。"""

    async with client.stream("POST", "/agent/chat", json=payload) as response:
        assert response.status_code == 200
        body = "".join([chunk async for chunk in response.aiter_text()])
    return [
        json.loads(frame.removeprefix("data: "))
        for frame in body.split("\n\n")
        if frame.startswith("data: ")
    ]


class DyingStreamModel(StreamingChatModel):
    """吐一段字之后断线的假模型，用来复现「钱花了、答案没了」那一幕。

    第一次调用先流出 ``text`` 再抛上游超时；之后每次调用都直接抛，模仿重试与降级都没救回来的
    情形。只在第一次流出文本，是为了让「已推出的那段」在断言里是一个确定的值：重试会把同一次
    运行的文本再推一遍，那是真实行为（服务端累积的确实是它推出去的文本），但用它当断言对象会
    让用例在讲另一件事。

    Attributes:
        text: 第一次调用要流出的文本。
        attempts: 被调用了几次，用来确认重试真的发生了。
    """

    text: str = "降息 25 个"
    attempts: int = 0

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        """第一次吐字后抛超时，之后直接抛超时。"""

        self.attempts += 1
        if self.attempts == 1 and self.text:
            yield ChatGenerationChunk(message=AIMessageChunk(content=self.text))
        raise openai.APITimeoutError(request=None)  # type: ignore[arg-type]


class ToolThenDyingStreamModel(StreamingChatModel):
    """先吐字再要求调工具（第一个节点完整结束），第二个节点吐字中途断线。

    这是最常见的中断形态，也是「累积口径」唯一的雷区：第一个节点的文本已经随 checkpoint 落库，
    而累积器里同样有它。按整次运行累积会把已落库的文本再写一遍，回放出的答案出现重复
    （实测过：``我查一下。`` + ``我查一下。降息``）。

    重试与降级都救不回来（后续调用直接抛），只有追问 ``房贷`` 那一轮正常作答——那一条用来顺带
    确认补写没把会话弄死。

    「换了一轮提问」按消息里的用户提问条数判定，**不能看最后一条是不是提问**：同一轮里第二个
    模型节点拿到的最后一条是工具结果，按最后一条判会把同一轮当成新的一轮，于是又从头流一遍
    「我查一下。」，把这条用例要测的「重复」和循环搞混。
    """

    human_count: int = 0
    calls: int = 0

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        """第一轮：吐字 + 要求调工具、然后断线；问 ``房贷`` 的那一轮：正常作答。"""

        humans = sum(1 for message in messages if isinstance(message, HumanMessage))
        if humans != self.human_count:
            self.human_count, self.calls = humans, 0
        self.calls += 1

        if isinstance(messages[-1], HumanMessage) and "房贷" in str(messages[-1].content):
            yield ChatGenerationChunk(message=AIMessageChunk(content="第二轮答案。"))
            return
        if self.calls == 1:
            yield ChatGenerationChunk(message=AIMessageChunk(content="我查一下。"))
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "search_documents",
                            "args": '{"query": "央行降息"}',
                            "id": "call-search",
                            "index": 0,
                        }
                    ],
                )
            )
            return
        if self.calls == 2:
            # 只在第二个节点的第一次尝试里吐字：重试当然会再推一次它收到的同一段文本，但那是
            # 另一条语义（done 事件会校正它），用它当断言对象会让这条用例讲错事。
            yield ChatGenerationChunk(message=AIMessageChunk(content="降息"))
        raise openai.APITimeoutError(request=None)  # type: ignore[arg-type]


def test_a_run_finishes_after_the_client_disconnects_mid_stream() -> None:
    """客户端在首帧之后断开，随后回放能读到完整答案。

    这条同时覆盖「完全没有订阅者时运行也能跑完」：断开那一刻 Starlette 就取消掉了响应生成器，
    也就是这个运行**不再有任何订阅者**，而它后面还要再跑完模型、把结果写进会话。所以这两条验收
    条件是同一个观察，不需要为第二条另开一个读数点。
    """

    model = StreamingChatModel(messages=iter([AIMessage(content="降息 25 个基点。")]))
    app, _search = create_agent_app(model)

    async def scenario() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                chunks = await disconnect_mid_stream(
                    app,
                    path="/agent/chat",
                    payload={"message": "央行降息了吗"},
                )
                replay = await _wait_for_answer(client, _thread_id(_frames(chunks)))
                return _frames(chunks), replay

    events, replay = run(scenario())

    # 1、断开之前只收到了首帧（run_started）——证明断开确实发生在流的中途。
    assert [event["event"] for event in events] == ["run_started"]

    # 2、没有任何人在拉之后，这次运行仍然把完整答案写进了会话。
    turns = replay["turns"]
    assert turns[-1]["question"] == "央行降息了吗"
    assert turns[-1]["answer"] == "降息 25 个基点。"


def test_the_next_question_in_the_same_thread_sees_the_finished_answer() -> None:
    """上一轮断开后跑完的答案，是下一轮提问看到的历史的一部分。

    这条是「结果真的落进了会话」而不是「回放接口凑巧能读出来」的补强：下一轮从 checkpointer
    取历史，看到的是同一份内容。改动之前这一轮只有提问、没有答案。
    """

    model = StreamingChatModel(
        messages=iter([AIMessage(content="第一轮答案。"), AIMessage(content="第二轮答案。")])
    )
    app, _search = create_agent_app(model)

    async def scenario() -> dict[str, Any]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                chunks = await disconnect_mid_stream(
                    app,
                    path="/agent/chat",
                    payload={"message": "第一个问题"},
                )
                thread_id = _thread_id(_frames(chunks))
                await _wait_for_answer(client, thread_id)

                follow_up = await _ask_and_read_fully(
                    client,
                    {"message": "第二个问题", "thread_id": str(thread_id)},
                )
                assert follow_up[-1]["event"] == "done"
                response = await client.get(f"/agent/threads/{thread_id}/messages")
                assert response.status_code == 200
                return response.json()

    replay = run(scenario())

    assert [turn["question"] for turn in replay["turns"]] == ["第一个问题", "第二个问题"]
    assert [turn["answer"] for turn in replay["turns"]] == ["第一轮答案。", "第二轮答案。"]


def test_an_upstream_failure_keeps_the_text_already_emitted() -> None:
    """上游在答案输出到一半时失败，已经推出去的那段留在会话里。

    改动之前这一轮只剩提问：模型节点没跑完就不落库，而用户在屏幕上已经看到了半截文字。
    """

    # messages 是父类的必填字段；本假模型自己覆写 _stream，所以给它一个空脚本。
    model = DyingStreamModel(messages=iter([]))
    app, _search = create_agent_app(model)

    async def scenario() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                events = await _ask_and_read_fully(client, {"message": "央行降息了吗"})
                thread_id = UUID(events[-1]["thread_id"])
                response = await client.get(f"/agent/threads/{thread_id}/messages")
                assert response.status_code == 200
                return events, response.json()

    events, replay = run(scenario())

    # 1、这次运行确实是被上游失败中断的，不是别的路径。
    assert events[-1]["event"] == "error"
    assert model.attempts > 1, "重试没有发生的话，这条用例测的就不是「重试耗尽后失败」"

    # 2、失败之前推给浏览器的文字，回放时还在。
    turn = replay["turns"][-1]
    assert turn["question"] == "央行降息了吗"
    assert turn["answer"] == "降息 25 个"
    # 3、它被认成这一次运行的回答，并如实标成未完成。
    assert turn["run_id"] == events[0]["run_id"]
    assert turn["status"] == "incomplete"


def test_the_write_back_does_not_repeat_already_persisted_text() -> None:
    """补写只写「尚未落库」的那一段，不把先完成的节点再写一遍。

    假模型先吐字、再要求调工具：那时第一个模型节点已经完整结束、文本已落库。第二个节点吐字到一半
    断线，补写的对象只有那半个字。按整次运行累积的话，回放出的答案会是
    ``我查一下。我查一下。降息``——重复的正是第一段。

    顺带断言补写不把会话弄死：写回用的 ``as_node`` 决定图的 ``next`` 怎么算，写错会让这个会话
    下一轮提问卡住。
    """

    model = ToolThenDyingStreamModel(messages=iter([]))
    app, _search = create_agent_app(model)

    async def scenario() -> dict[str, Any]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                events = await _ask_and_read_fully(client, {"message": "央行降息了吗"})
                thread_id = UUID(events[-1]["thread_id"])

                follow_up = await _ask_and_read_fully(
                    client,
                    {"message": "那房贷呢", "thread_id": str(thread_id)},
                )
                assert follow_up[-1]["event"] == "done"

                response = await client.get(f"/agent/threads/{thread_id}/messages")
                assert response.status_code == 200
                return response.json()

    replay = run(scenario())

    assert [turn["answer"] for turn in replay["turns"]] == ["我查一下。降息", "第二轮答案。"]
    assert [turn["status"] for turn in replay["turns"]] == ["incomplete", "completed"]


class CountingStreamModel(StreamingChatModel):
    """记录被调用次数的逐 token 假模型。

    ``GenericFakeChatModel`` 自己没有计数器，而「被拒的那次根本没碰模型」只能靠次数断言。
    """

    calls: int = 0

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        """记一次调用，其余照父类逐 token 产出。"""

        self.calls += 1
        yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_a_second_submission_while_a_run_is_in_flight_is_rejected_with_409() -> None:
    """这个会话已经有运行在跑时，再次提交拿到 409 与稳定错误码。

    改动之前两次都受理，先完成的那次从历史里消失。这里刻意把「在途」预置在会话行上，而不是靠
    两个请求抢时序：验收要的是一条确定的规则，不是一次可能的竞态。
    """

    model = CountingStreamModel(messages=iter([AIMessage(content="不该跑到这里。")]))
    app, _search = create_agent_app(model)
    thread_id = uuid4()
    held = uuid4()
    seed_owned_thread(app, thread_id, active_run_id=held)

    response = run(send(app, "POST", "/agent/chat", json={"message": "再问一次", "thread_id": str(thread_id)}))

    assert response.status_code == 409
    assert response.json()["code"] == "agent_run_in_progress"
    # 被拒的那次不该动模型，也不该把别人的运行挤掉。
    assert model.calls == 0
    assert app.state.offline_threads.threads[thread_id].active_run_id == held


def test_two_almost_simultaneous_submissions_leave_exactly_one_run() -> None:
    """两个几乎同时的提交只有一个成功，另一个拿 409。

    **这条用例能证明什么、不能证明什么**：它证明「路由把占位失败翻译成 409」，证明不了那条占位
    写入在真库上原子。原子性由 ``test_agent_thread_service.py`` 的语句级断言与
    ``test_agent_thread_ownership_integration.py`` 的真库用例负责——内存替身里的判定没有 await
    点，天然不会交错，所以它只能做这一层。
    """

    model = StreamingChatModel(
        messages=iter([AIMessage(content="答案。"), AIMessage(content="答案。")])
    )
    app, _search = create_agent_app(model)
    thread_id = uuid4()
    # 预置一个已经失活的上一次运行，让这次提交有东西可抢。
    seed_owned_thread(
        app,
        thread_id,
        active_run_id=uuid4(),
        last_active_at=datetime.now(UTC) - timedelta(seconds=RUN_ZOMBIE_THRESHOLD_SECONDS * 2),
    )

    async def scenario() -> list[int]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                responses = await asyncio.gather(
                    client.post("/agent/chat", json={"message": "第一问", "thread_id": str(thread_id)}),
                    client.post("/agent/chat", json={"message": "第二问", "thread_id": str(thread_id)}),
                )
                return [response.status_code for response in responses]

    statuses = run(scenario())

    assert statuses.count(200) == 1
    assert statuses.count(409) == 1


def test_the_session_is_submittable_again_right_after_a_run_finishes() -> None:
    """运行结束之后同一个会话立刻恢复可提交。

    这条挡的是「先发终态事件、再释放占位」那种顺序：那样用户拿到 ``done`` 立刻追问，会撞上自己
    刚刚结束的那次运行还没释放的位置，得到一次莫名其妙的 409。
    """

    model = StreamingChatModel(
        messages=iter([AIMessage(content="第一轮答案。"), AIMessage(content="第二轮答案。")])
    )
    app, _search = create_agent_app(model)

    async def scenario() -> list[dict[str, Any]]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                first = await _ask_and_read_fully(client, {"message": "第一问"})
                thread_id = first[0]["thread_id"]
                second = await _ask_and_read_fully(
                    client, {"message": "第二问", "thread_id": thread_id}
                )
                return [*first, *second]

    events = run(scenario())

    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "done"
    assert events[-1]["answer"] == "第二轮答案。"


class SlowStreamModel(StreamingChatModel):
    """吐字前先等一会儿，用来把一次运行停在「已经开始、还没结束」那个确定的位置上。

    要观察「占位那一刻写下了什么」就必须在那一刻看，而假模型完成得太快。用固定延时而不是外部
    门闸：门闸本身要为测试多开一个内部句柄，而这里只需要一个比轮询间隔大得多的窗口。
    """

    delay: float = 0.3

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """等一会儿再逐 token 产出。"""

        await asyncio.sleep(self.delay)
        for chunk in self._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk


async def _wait_until(predicate: Any, *, deadline_seconds: float = 5.0) -> None:
    """轮询等待一个条件成立；超时就报错（上限只防用例自己挂住）。"""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("等待的条件没有在期限内成立")


def test_a_stale_stop_request_does_not_survive_the_next_claim() -> None:
    """新一次运行占位时，上一次留下的停止请求被清掉。

    「上一次留下的」不是假设：只在运行收尾时清是不够的——进程可能在清之前就被杀掉，留下一个陈旧
    的停止标记，于是刚起步的新运行一开场就被它停掉。那恰好是写入端用运行 id 比对想防的事，只是从
    写端挪到了读端（见 ADR 0037）。

    观察点刻意选在**运行还在跑的那一刻**：等到收尾再看就分不清是「占位时清的」还是「收尾时清的」，
    而后一条路正是规格说不够的那一条。

    这里断言的是内存替身里的那一行，因为「停止请求」没有对外读数点（它不是一个用户能看的字段）。
    真实实现把「清掉」放在那条条件写入的 SET 里，由 ``test_agent_thread_service.py`` 的语句级断言
    盯住。
    """

    model = SlowStreamModel(messages=iter([AIMessage(content="答案。")]))
    app, _search = create_agent_app(model)
    thread_id = uuid4()
    record = seed_owned_thread(
        app, thread_id, stop_requested_at=datetime.now(UTC) - timedelta(minutes=5)
    )

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                answering = asyncio.create_task(
                    _ask_and_read_fully(
                        client, {"message": "追问", "thread_id": str(thread_id)}
                    )
                )
                await _wait_until(lambda: record.active_run_id is not None)
                assert record.stop_requested_at is None
                await answering

    run(scenario())

    assert record.active_run_id is None


class EndlessStreamModel(StreamingChatModel):
    """长时间逐字产出、之后会要求调工具（也就是会再被调用一次）的假模型。

    用来验证「停止真的停住了服务端」：如果停止没有生效，这个模型会把这 300 个分片吐完、节点结束、
    图再去调它一次（``calls`` 变成 2），用例就能看出来。分片数刻意有界：实现退化时用例仍然会结束，
    只是走到另一条分支上失败，而不是永远转下去。
    """

    calls: int = 0
    chunks: int = 300

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """逐字产出（每片之间让出事件循环），最后要求调一次工具。"""

        self.calls += 1
        for _ in range(self.chunks):
            await asyncio.sleep(0.01)
            yield ChatGenerationChunk(message=AIMessageChunk(content="字"))
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "search_documents",
                        "args": '{"query": "央行降息"}',
                        "id": "call-search",
                        "index": 0,
                    }
                ],
            )
        )


async def _read_stream_frames(response: Any) -> list[dict[str, Any]]:
    """把一条 SSE 响应的全部帧读成事件对象。"""

    body = "".join([chunk async for chunk in response.aiter_text()])
    return [
        json.loads(frame.removeprefix("data: "))
        for frame in body.split("\n\n")
        if frame.startswith("data: ")
    ]


def test_stopping_a_run_ends_it_without_further_model_calls() -> None:
    """在途时停止：这次运行以终态结束，此后不再产生新的模型调用，已输出内容留在会话里。

    「不再产生新的模型调用」是这条用例最容易漏的一半：只断言「流结束了」的话，一个先把模型调用
    跑完、再装作停下的实现也能通过。所以假模型被设计成**停止不生效时它一定会被再调用一次**。

    这条链路必须用 ``open_chat_stream`` 手工驱动：停止请求要在服务端还在流的时候发出去，而
    ``httpx.ASGITransport`` 会在返回响应前把整个响应体收完，做不到那种交错。
    """

    model = EndlessStreamModel(messages=iter([]))
    app, _search = create_agent_app(model)

    async def scenario() -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                task, queue = await open_chat_stream(
                    app, path="/agent/chat", payload={"message": "央行降息了吗"}
                )
                events: list[dict[str, Any]] = []
                thread_id: str | None = None
                run_id: str | None = None
                stop_status: int | None = None
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    events.append(event)
                    if event["event"] == "run_started":
                        thread_id, run_id = event["thread_id"], event["run_id"]
                    # 看到第一个字就请求停止——运行此刻确实在途，而且还在继续产出。
                    if event["event"] == "token" and stop_status is None:
                        assert thread_id is not None and run_id is not None
                        target = {"thread_id": thread_id, "run_id": run_id}
                        stop_status = (await client.post("/agent/stop", json=target)).status_code
                        # 幂等：同一个请求再发一次也成功。
                        again = await client.post("/agent/stop", json=target)
                        assert again.status_code == 200
                await task

                assert thread_id is not None
                replay = await client.get(f"/agent/threads/{thread_id}/messages")
                assert replay.status_code == 200
                return events, stop_status, replay.json()

    # 上限只用来防止用例自己挂住（实现退化时这次运行会自己跑完，断言随即变红，不会转不下去）。
    events, stop_status, replay = run(asyncio.wait_for(scenario(), timeout=60.0))

    assert stop_status == 200
    # 1、这次运行以终态结束，而且是「截断」那一档（不是 completed）。
    assert events[-1]["event"] == "done"
    assert events[-1]["status"] == "incomplete"
    # 2、停止之后不再有新的模型调用：假模型只被调了一次。
    assert model.calls == 1
    # 3、已经推出去的文本留在会话里。
    tokens = "".join(event["text"] for event in events if event["event"] == "token")
    assert tokens
    assert replay["turns"][-1]["answer"] == tokens
    # 4、前端拿到的终态与刷新后回放到的内容是同一份口径——两者都来自同一份持久化状态。
    assert events[-1]["answer"] == replay["turns"][-1]["answer"]


def test_a_stop_request_for_another_run_does_not_touch_the_current_one() -> None:
    """在途运行的 id 与请求里的不相等时，停止请求不影响当前这一次运行。

    这就是「迟到的停止」那条路径：用户先点停止 → 旧运行已收尾 → 用户发下一次提问 → 那个停止请求
    才到达。服务端靠比对运行 id 挡住它；不做比对的话，刚起步的新运行会被一开场就停掉。
    """

    model = SlowStreamModel(messages=iter([AIMessage(content="完整答案。")]))
    app, _search = create_agent_app(model)
    thread_id = uuid4()
    seed_owned_thread(app, thread_id)

    async def scenario() -> tuple[list[dict[str, Any]], httpx.Response]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                answering = asyncio.create_task(
                    _ask_and_read_fully(
                        client, {"message": "追问", "thread_id": str(thread_id)}
                    )
                )
                # 等占位写下去，确保停止请求到达时这次运行确实在途。
                await _wait_until(
                    lambda: app.state.offline_threads.threads[thread_id].active_run_id is not None
                )
                stale = await client.post(
                    "/agent/stop",
                    json={"thread_id": str(thread_id), "run_id": str(uuid4())},
                )
                return await answering, stale

    events, stale = run(asyncio.wait_for(scenario(), timeout=30.0))

    assert stale.status_code == 200
    # 这次运行没被那个不属于它的停止请求碰到：照旧跑完。
    assert events[-1]["event"] == "done"
    assert events[-1]["status"] == "completed"
    assert events[-1]["answer"] == "完整答案。"


def test_a_stop_request_without_a_matching_run_is_idempotent() -> None:
    """没有可停的运行（已经收尾）时，停止请求仍然成功，且不改动任何东西。"""

    model = StreamingChatModel(messages=iter([AIMessage(content="答案。")]))
    app, _search = create_agent_app(model)

    async def scenario() -> list[int]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                events = await _ask_and_read_fully(client, {"message": "央行降息了吗"})
                target = {
                    "thread_id": events[0]["thread_id"],
                    "run_id": events[0]["run_id"],
                }
                first = await client.post("/agent/stop", json=target)
                second = await client.post("/agent/stop", json=target)
                return [first.status_code, second.status_code]

    assert run(scenario()) == [200, 200]


def test_stopping_a_thread_that_is_not_yours_is_a_404() -> None:
    """别人的会话按既有 404 形状返回，不泄露它是否存在。"""

    model = StreamingChatModel(messages=iter([AIMessage(content="答案。")]))
    app, _search = create_agent_app(model)
    someone_elses = uuid4()
    seed_owned_thread(app, someone_elses, user_id=uuid4())

    response = run(
        send(
            app,
            "POST",
            "/agent/stop",
            json={"thread_id": str(someone_elses), "run_id": str(uuid4())},
        )
    )

    assert response.status_code == 404
    assert response.json()["code"] == "agent_thread_not_found"


def test_the_replay_response_reports_the_run_in_flight() -> None:
    """读取会话历史的响应里带出当前在途运行的 id；没有在跑时为空。

    它是「刷新之后知道这一轮还在跑」的唯一渠道：「有运行在途」是只有服务端才知道的事，而正在跑的那
    一轮还没落库，从消息里看不出来。没有它就会出现自相矛盾的组合——界面显示「这一轮没有留下回答」，
    用户再发一条却被服务端以「还在生成中」拒绝。
    """

    model = SlowStreamModel(messages=iter([AIMessage(content="答案。")]))
    app, _search = create_agent_app(model)
    thread_id = uuid4()
    seed_owned_thread(app, thread_id)

    async def scenario() -> tuple[str, dict[str, Any], dict[str, Any]]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                answering = asyncio.create_task(
                    _ask_and_read_fully(
                        client, {"message": "追问", "thread_id": str(thread_id)}
                    )
                )
                await _wait_until(
                    lambda: app.state.offline_threads.threads[thread_id].active_run_id is not None
                )
                # 记下此刻在途的那个 id：跑完之后它会变回空，不能等到断言时再读。
                in_flight = str(app.state.offline_threads.threads[thread_id].active_run_id)
                during = await client.get(f"/agent/threads/{thread_id}/messages")
                await answering
                after = await client.get(f"/agent/threads/{thread_id}/messages")
                return in_flight, during.json(), after.json()

    in_flight, during, after = run(asyncio.wait_for(scenario(), timeout=30.0))

    assert during["active_run_id"] == in_flight
    # 在途那一轮此刻已经在 turns 里（提问在运行开始时就落库了），但还没有答案。光看 turns
    # 分不清「模型还没写」与「还在写」——这正是前端需要 active_run_id 的原因。
    assert [turn["answer"] for turn in during["turns"]] == [""]
    # 跑完之后空：前端据此结束轮询并换成最终内容。
    assert after["active_run_id"] is None
    assert after["turns"][-1]["answer"] == "答案。"
