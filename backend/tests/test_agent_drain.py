"""部署收尾时排空在途运行，由收到退出信号的那个进程自己收成未完成。

工单 01（排空）：ASGI 生命周期收尾发生时，正在跑的那一轮不再被砍在句子中间。收尾对本进程手上
所有在途运行一起生效（并发等待），不随运行数变慢；到排空上限还没走到边界的按放弃收尾（沿用
既有的补写与释放路径）。

**排空由旧进程自己收尾**：停在边界之后，补写已推出的文字、写会话历史、释放会话占位、发终态
事件，然后退出；会话因此立刻能接新提问（见 ADR 0051）。

**为什么这些断言走 HTTP 层**：它沿用 ``test_agent_runs.py`` 的既有接缝——真实图 + 假模型 +
``InMemorySaver`` + 内存会话表，只换模型与存储。排空的触发点是 lifespan 退出，所以用例在
``async with app.router.lifespan_context(app)`` 里发起运行、在流的中途退出生命周期，再读回放。

**排空只会发生在还有待跑节点的时候**：图先判「没有任务了就是跑完」，再判「有没有人要求排空」。
所以一轮只剩模型节点、排空请求打在它中途时，它会正常跑完（checkpoint 里是完整且完成的那一轮）；
只有后面还挂着工具节点时才会停在边界（未完成）。两条都是正常收尾，不是失败。

**业务表与 checkpoint 各回答一半问题。** 收尾里就落表了，所以「这一轮在不在、状态是什么」直接
读回放（业务表）——排空那一刻之后它就在了；但「它停在哪儿、有没有被截断」仍然只有 checkpoint
说得清，因为回放只给最终内容与完成态。读 checkpoint 用的是终态事件与回放共用的
``build_replay_turns``。
"""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver

from agent_lab.agent.replay import build_replay_turns
from tests.agent_helpers import StreamingChatModel, open_chat_stream, run
from tests.app_helpers import (
    InMemoryAgentThreadService,
    create_agent_app,
    seed_owned_thread,
    send,
)


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


async def _collect(queue: "asyncio.Queue[dict[str, Any] | None]") -> list[dict[str, Any]]:
    """把队列里剩余的事件读干（``None`` 表示流结束）。"""

    events: list[dict[str, Any]] = []
    while True:
        event = await queue.get()
        if event is None:
            return events
        events.append(event)


async def _open_and_wait_for_token(
    app: Any,
    *,
    payload: dict[str, Any],
) -> tuple[asyncio.Task, "asyncio.Queue[dict[str, Any] | None]", list[dict[str, Any]]]:
    """发起一次运行并读到第一个 token；返回 ``(任务, 队列, 已读到的事件)``。

    读到第一个 token 才返回，是为了让调用方在「这次运行确实在途、而且正在吐字」的那一刻退出
    lifespan 触发排空。
    """

    task, queue = await open_chat_stream(app, path="/agent/chat", payload=payload)
    events: list[dict[str, Any]] = []
    while True:
        event = await queue.get()
        assert event is not None, "流在第一个 token 之前就结束了"
        events.append(event)
        if event["event"] == "token":
            return task, queue, events


async def _read_replay(
    checkpointer: Any,
    threads: InMemoryAgentThreadService,
    thread_id: UUID,
) -> dict[str, Any]:
    """用一个独立实例读会话回放（它读业务表里那些行）。

    刻意不复用被排空的那个应用：它的模型与图都在用来制造排空，另起一个才能只读回放、不扰动被观察
    的状态。读回放只需要一个能调 `GET /agent/threads/{id}/messages` 的实例，所以用默认的离线注册表
    （毫秒级周期、不碰真库）。checkpointer 仍然要传：回放所在的图要从同一份会话历史里取。
    """

    reader, _search = create_agent_app(
        StreamingChatModel(messages=iter([])),
        checkpointer=checkpointer,
        threads=threads,
    )
    async with reader.router.lifespan_context(reader):
        transport = httpx.ASGITransport(app=reader)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(f"/agent/threads/{thread_id}/messages")
            assert response.status_code == 200
            return response.json()


async def _turns_from_checkpointer(
    graph: Any,
    thread_id: UUID,
) -> tuple[Any, ...]:
    """从 checkpointer 现算这一轮的轮次，用来观察「排空停在哪儿」。

    它回答的是业务表回答不了的那半句：「图停在哪个节点」只有 checkpoint 说得清——回放只给这一轮
    的最终内容与完成态，而「工具调用只有调用、没有结果」这种边界位置只在快照里。所以同一轮
    有时两边都要读：业务表证明它落表了，checkpoint 证明它停在了边界。

    Args:
        graph: 运行用的图；它身上挂着那个共享的 ``InMemorySaver``。调用方要在 lifespan
            内取到它（退出后 ``app.state.agent_runtime`` 会被置空），图的读方法在退出后仍可用。
    """

    snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
    turns, _summarized, _summary = build_replay_turns((snapshot.values or {}).get("messages") or [])
    return turns


class SlowCompletingStreamModel(StreamingChatModel):
    """逐字慢速产出、最后正常结束（不调工具）的假模型。

    只在模型节点中途留出一段可被排空请求命中的窗口。因为它后面没有待跑节点，排空请求会在节点
    跑完之后被判成「图已经跑完」，所以这一轮正常完成——用于验证「排空不会把它砍成半截」。
    """

    chunks: int = 20
    delay: float = 0.01

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        """慢速逐字产出，节点正常结束。"""

        for _ in range(self.chunks):
            await asyncio.sleep(self.delay)
            yield ChatGenerationChunk(message=AIMessageChunk(content="字"))


class ToolBoundaryStreamModel(StreamingChatModel):
    """吐一段字、再要求调工具的假模型；排空会把它停在「模型节点已落库、工具待跑」的边界。

    重复调用总是走同一条分支：排空停在第一个节点之后，所以后续节点根本不会被跑到。
    """

    delay: float = 0.03

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        """先慢速吐出第一段，再给出一个工具调用。"""

        await asyncio.sleep(self.delay)
        yield ChatGenerationChunk(message=AIMessageChunk(content="我查一下。"))
        await asyncio.sleep(self.delay)
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


def test_draining_a_bare_model_node_lets_the_run_finish_instead_of_truncating_it() -> None:
    """排空请求打在只剩模型节点的一轮上时，它正常跑完，而不是被切成半截并补写。

    这是与改动前最直接的对比：改动前收尾取消运行、把已推出去的半截文字补写成一条「未完成」的
    模型消息；现在它做完当前节点后判「没有任务了」正常收尾，回放是完成、内容是完整的那一段。

    这一轮也不会留下任何「没写完」的东西：整轮完成态是 ``completed``，且内容不多不少。
    """

    model = SlowCompletingStreamModel(messages=iter([]))
    checkpointer = InMemorySaver()
    app, _search = create_agent_app(model, checkpointer=checkpointer)

    async def scenario() -> tuple[list[dict[str, Any]], tuple[Any, ...]]:
        async with app.router.lifespan_context(app):
            graph = app.state.agent_runtime.graph
            task, queue, first = await _open_and_wait_for_token(
                app, payload={"message": "央行降息了吗"}
            )
            thread_id = UUID(first[0]["thread_id"])
            # 退出 lifespan：这里触发排空，当前模型节点跑完后图没有别的任务，正常收尾。
        rest = await _collect(queue)
        await task
        return [*first, *rest], await _turns_from_checkpointer(graph, thread_id)

    events, turns = run(scenario())

    full_answer = "字" * model.chunks
    # 1、排空不会把这一轮砍掉：已经推出去的字全部落库，不多不少（补写会让它多出一截或只剩半截）。
    tokens = "".join(event["text"] for event in events if event["event"] == "token")
    assert tokens == full_answer
    # 2、持久化的内容与推出去的一致，而且已完成（模型的完整回复，不是补写的那条）。
    assert turns[-1].answer == full_answer
    assert turns[-1].status == "completed"


def test_draining_two_in_flight_runs_stops_both_at_the_boundary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """手上同时有两个在途运行时，收尾对两者一起生效；各自停在可排空边界的边界，都不是被取消。

    边界落点的证据是两样东西：第一个模型节点的文本原样落库（没有被截断或重复补写），以及那次
    工具调用只有调用、没有结果——说明图停在了「模型节点已完成、工具节点还没跑」的位置，那只有
    checkpoint 说得清。而「这一轮已经落表、会话已解锁」是另一回事：排空由旧进程自己收尾，收尾里
    就落表了，所以那一半直接读会话历史与内存会话表。
    """

    model = ToolBoundaryStreamModel(messages=iter([]))
    checkpointer = InMemorySaver()
    app, _search = create_agent_app(model, checkpointer=checkpointer)
    threads = app.state.offline_threads
    first_thread, second_thread = uuid4(), uuid4()
    seed_owned_thread(app, first_thread)
    seed_owned_thread(app, second_thread)

    async def scenario() -> tuple[list[dict[str, Any]], tuple[Any, ...], tuple[Any, ...], UUID, UUID]:
        async with app.router.lifespan_context(app):
            graph = app.state.agent_runtime.graph
            first_task, first_queue = await open_chat_stream(
                app, path="/agent/chat", payload={"message": "第一问", "thread_id": str(first_thread)}
            )
            second_task, second_queue = await open_chat_stream(
                app, path="/agent/chat", payload={"message": "第二问", "thread_id": str(second_thread)}
            )
            # 等两次运行都真的占上了位（都在途），再退出 lifespan 触发排空。
            await _wait_until(
                lambda: app.state.offline_threads.threads[first_thread].active_run_id is not None
                and app.state.offline_threads.threads[second_thread].active_run_id is not None
            )
            # 读到任一方吐出第一个字，确保排空请求打在模型节点中途。
            while True:
                event = await first_queue.get()
                if event is not None and event["event"] == "token":
                    break
        first_events = await _collect(first_queue)
        second_events = await _collect(second_queue)
        await first_task
        await second_task
        first_turns = await _turns_from_checkpointer(graph, first_thread)
        second_turns = await _turns_from_checkpointer(graph, second_thread)
        return first_events, first_turns, second_turns, first_thread, second_thread

    with caplog.at_level(logging.INFO, logger="agent_lab.agent.runs"):
        _first_events, first_turns, second_turns, first_thread, second_thread = run(
            asyncio.wait_for(scenario(), timeout=30.0)
        )

    for turns in (first_turns, second_turns):
        turn = turns[-1]
        # 1、第一个节点的文本完整落库：既不是空、也不是补写出来的重复一截。
        assert turn.answer == "我查一下。"
        # 2、没有走到终态——它停在边界，这一轮按未完成收尾。
        assert turn.status == "incomplete"
        # 3、工具调用只有调用、没有结果：图确实停在工具节点之前。
        assert turn.traces and turn.traces[0].content is None
    # 4、两个被排空的运行各留下一条只含 id 的日志（没排空超时，所以两条都是边界那条）。
    stopped_at_boundary = [each for each in caplog.messages if "已停在排空边界" in each]
    assert len(stopped_at_boundary) == 2
    assert all(
        str(turns[-1].run_id) in " ".join(stopped_at_boundary)
        for turns in (first_turns, second_turns)
    )
    # 5、两个会话都由旧进程自己收完了：占位释放，而那一轮已经落进
    #    会话历史表——这就是「先落表、再释放」之后读回去能看到的东西。
    for thread_id, turns in ((first_thread, first_turns), (second_thread, second_turns)):
        record = threads.threads[thread_id]
        assert record.active_run_id is None
        written = [
            group.run_id for each in threads.recorded_run_messages for group in each.runs
        ]
        assert turns[-1].run_id in written
        assert threads.messages[thread_id], "那一轮应该已经写进会话历史表"


def test_a_drained_run_still_unlocks_the_session_when_the_terminal_event_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """终态事件算不出来不影响收尾：落表与释放占位照做，会话不会因此锁死。

    终态事件是这一轮唯一的「派生物」，算不出来只意味着订阅方少收最后一条；会话历史与占位是事实，
    不能被它拖住。所以这里故意让它报错，逐项确认剩下的事都做完了：日志里有一条带两个 id 的错误、
    会话位已空、那一轮已落表、流只是少了一条终态。

    那条错误日志本身也是断言对象：无 try/except 时异常会从驱动任务里逸出去，而那个任务没人 await，
    就变成一条不带 id 的 asyncio 警告——运维就查不出是哪一轮了。
    """

    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("checkpoint 读不出来")

    monkeypatch.setattr("agent_lab.agent.runs.build_terminal_event", _boom)
    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    app, _search = create_agent_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )

    async def scenario() -> tuple[UUID, list[dict[str, Any]], dict[str, Any]]:
        async with app.router.lifespan_context(app):
            task, queue, first = await _open_and_wait_for_token(
                app, payload={"message": "央行降息了吗"}
            )
            thread_id = UUID(first[0]["thread_id"])
        rest = await _collect(queue)
        await task
        replay = await _read_replay(checkpointer, threads, thread_id)
        return thread_id, [*first, *rest], replay

    with caplog.at_level(logging.ERROR, logger="agent_lab.agent.runs"):
        thread_id, events, replay = run(scenario())

    # 1、失败只进日志：那条错误看得见，且带两个 id。
    errors = [each for each in caplog.messages if "构造排空运行的终态事件失败" in each]
    assert len(errors) == 1
    # 2、会话被释放——这是「不会永久锁死」的直接证据。
    assert threads.threads[thread_id].active_run_id is None
    # 3、会话历史那一次落表照做，那一轮仍按未完成收尾（释放之前落的表，所以读得到）。
    assert replay["turns"][-1]["status"] == "incomplete"
    assert replay["turns"][-1]["answer"] == "我查一下。"
    # 4、代价只是订阅方少一条终态事件（流直接关闭）：这是唯一可以丢的东西。
    assert not [event for event in events if event["event"] == "done"]


def test_a_drained_session_accepts_the_next_question_immediately() -> None:
    """排空之后同一个会话立刻能接着问：返回 200 并真的跑到终态，而不是 409 或一直等下去。

    这是这次改动最容易漏的一条验收：收尾里少一步「释放会话占位」，那一轮照样标成未完成、用户
    照样看到半截内容，但会话永久锁死——而锁死的表现只在部署之后才出现。所以这里逐层断言：会话位
    已空、那一轮已经落表（先落表再释放，所以读得到）、紧接着那次 POST 不是 409 而是跑到了 done。

    也暗含了「补写是空操作」：重写后下面那句 ``answer == "我查一下。"`` 是多一个少一个都不行
    的——重复补写会让它变成 ``我查一下。我查一下。``。
    """

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    app, _search = create_agent_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )

    async def drain_one() -> tuple[UUID, list[dict[str, Any]]]:
        async with app.router.lifespan_context(app):
            task, queue, first = await _open_and_wait_for_token(
                app, payload={"message": "央行降息了吗"}
            )
            thread_id = UUID(first[0]["thread_id"])
        rest = await _collect(queue)
        await task
        return thread_id, [*first, *rest]

    thread_id, drained_events = run(drain_one())
    record = threads.threads[thread_id]
    # 1、订阅方仍然收到了一条终态事件：它来自与回放共用的 build_terminal_event，所以说的与表里
    #    一致——这一轮未完成、停在已推出的那句上。
    terminal = drained_events[-1]
    assert terminal["event"] == "done"
    assert terminal["status"] == "incomplete"
    assert terminal["answer"] == "我查一下。"
    # 2、会话位已释放：在途运行 id 清空。
    assert record.active_run_id is None

    # 3、那一轮已经落进会话历史业务表（释放之前落的表，所以现在读回放读得到），状态未完成。
    replay = run(_read_replay(checkpointer, threads, thread_id))
    assert replay["active_run_id"] is None
    assert replay["turns"][-1]["status"] == "incomplete"
    assert replay["turns"][-1]["answer"] == "我查一下。"

    # 4、立刻在同一个会话里发新提问：200，而且真的跑到终态（不是 409、也不是停在等待态）。
    reader, _search2 = create_agent_app(
        StreamingChatModel(messages=iter([AIMessage(content="降息了。")])),
        checkpointer=checkpointer,
        threads=threads,
    )
    response = run(
        send(
            reader,
            "POST",
            "/agent/chat",
            json={"message": "那房贷呢", "thread_id": str(thread_id)},
        )
    )
    assert response.status_code == 200
    events = _frames([response.content.decode("utf-8")])
    assert events[-1]["event"] == "done"
    # 5、新那一轮跑完之后会话又是干净的，用户可以接着问下一句。
    assert threads.threads[thread_id].active_run_id is None


class NeverEndingStreamModel(StreamingChatModel):
    """永远走不到 superstep 边界的假模型，用来验证排空上限到点后按放弃收尾。"""

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        """一直产出（每片之间让出事件循环），节点永远不结束。"""

        while True:
            await asyncio.sleep(0.01)
            yield ChatGenerationChunk(message=AIMessageChunk(content="字"))


async def _wait_until(predicate: Any, *, deadline_seconds: float = _POLL_DEADLINE_SECONDS) -> None:
    """轮询等待一个条件成立；超时就报错（上限只防用例自己挂住）。"""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("等待的条件没有在期限内成立")


def test_a_run_that_misses_the_drain_deadline_is_abandoned_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """排空上限到点还没走到边界的运行由旧进程自己收成未完成、释放会话，并留下一条日志。

    这条是「放弃」这条收尾路径：它与走到边界的排空走同一条收尾（补写、落表、释放会话位、发终态
    事件），区别只在日志与原因。用户看到未写完的内容、会话已解锁，可以立刻重新提问——与强杀
    那一类在界面上表现一致。
    """

    model = NeverEndingStreamModel(messages=iter([]))
    checkpointer = InMemorySaver()
    app, _search = create_agent_app(
        model, checkpointer=checkpointer, drain_timeout=0.05
    )
    threads = app.state.offline_threads

    async def scenario() -> tuple[UUID, str, dict[str, Any]]:
        async with app.router.lifespan_context(app):
            task, queue, first = await _open_and_wait_for_token(
                app, payload={"message": "央行降息了吗"}
            )
            thread_id = UUID(first[0]["thread_id"])
            run_id = first[0]["run_id"]
            # 退出 lifespan：排空在 0.05 秒内等不到边界，按放弃收尾。
        await _collect(queue)
        await task
        replay = await _read_replay(checkpointer, threads, thread_id)
        return thread_id, run_id, replay

    with caplog.at_level(logging.WARNING, logger="agent_lab.agent.runs"):
        thread_id, run_id, replay = run(scenario())

    # 1、会话被释放：用户能立刻重新提问，而不是被一个卡住的会话挡住。
    assert app.state.offline_threads.threads[thread_id].active_run_id is None
    # 2、那一轮如实标成未完成，已经推出去的内容留在会话里。
    assert replay["turns"][-1]["status"] == "incomplete"
    assert replay["turns"][-1]["answer"]
    # 3、放弃这件事在日志里看得见，而且只含 id。
    abandoned = [each for each in caplog.messages if "放弃" in each]
    assert len(abandoned) == 1
    # 4、放弃也是一种收尾：这一轮照样落进会话历史表（完成态是未答完），而不是因为「不是正常
    #    答完」就不写。它走的是与停止、上游失败同一支收尾代码。
    recorded = app.state.offline_threads.recorded_run_messages
    assert len(recorded) == 1
    assert [group.run_id for group in recorded[0].runs] == [UUID(run_id)]
    assert [row.role for row in recorded[0].runs[0].rows] == ["question", "answer"]
    assert recorded[0].runs[0].rows[0].run_meta["completed"] is False
    assert str(run_id) in abandoned[0]
