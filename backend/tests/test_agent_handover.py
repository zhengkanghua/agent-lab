"""部署收尾时排空在途运行、由别的进程接手续跑。

工单 01（排空）：ASGI 生命周期收尾发生时，正在跑的那一轮不再被砍在句子中间。收尾对本进程手上
所有在途运行一起生效（并发等待），不随运行数变慢；到排空上限还没走到边界的按放弃收尾（沿用
既有的补写与释放路径）。

**为什么这些断言走 HTTP 层**：它沿用 ``test_agent_runs.py`` 的既有接缝——真实图 + 假模型 +
``InMemorySaver`` + 内存会话表，只换模型与存储。排空的触发点是 lifespan 退出，所以用例在
``async with app.router.lifespan_context(app)`` 里发起运行、在流的中途退出生命周期，再读回放。

**排空只会发生在还有待跑节点的时候**：图先判「没有任务了就是跑完」，再判「有没有人要求排空」。
所以一轮只剩模型节点、排空请求打在它中途时，它会正常跑完（checkpoint 里是完整且完成的那一轮）；
只有后面还挂着工具节点时才会停在边界（未完成）。两条都是正常收尾，不是失败。

**排空那一刻还没落表，所以观察它要用 checkpointer 那条路径。** 会话历史的写入只发生在非排空的
收尾里（排空交给接手方写，见工单 03），所以回放（读业务表）此时看不到这一轮；要断言「它停在
哪儿、有没有被截断」只能读 checkpoint，用的还是终态事件与接手共用的 ``build_replay_turns``。
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver

from agent_lab.agent.limits import RUN_ZOMBIE_THRESHOLD_SECONDS
from agent_lab.api.dependencies import get_llm_model_selection_service
from agent_lab.agent.replay import build_replay_turns
from tests.agent_helpers import FailingChatModel, StreamingChatModel, open_chat_stream, run
from tests.app_helpers import (
    InMemoryAgentThreadService,
    InMemoryLlmModelSelectionService,
    OfflineCatalogModel,
    create_agent_app,
    offline_agent_run_registry_factory,
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
    """用一个「不扫描接手标记」的实例读会话回放（它读业务表里那些行）。

    刻意不复用被排空的那个应用：它的注册表带图，再进一次 lifespan 就会去接手（工单 03 的行为），
    那样读回放本身会改变被观察的状态。读回放只需要一个能调 `GET /agent/threads/{id}/messages`
    的实例，所以注册表传 ``graph=None``。checkpointer 仍然要传：同一个应用里还有需要图的东西，
    而且接手写下的行也要跟它对齐。
    """

    reader, _search = create_agent_app(
        StreamingChatModel(messages=iter([])),
        checkpointer=checkpointer,
        threads=threads,
        agent_run_registry_factory=lambda runtime=None: offline_agent_run_registry_factory(
            threads, runtime=None
        ),
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

    排空那一刻会话历史表里还没有这一行（写入由接手方在最终收尾时做），所以那一段时间里
    用户的界面上是「正在生成」，回放也是空的。要断言「已经推出的文字完整落库、图停在哪个
    节点」就只能读 checkpoint——终态事件与接手读的也是同一处、同一套组装。

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

    重复调用总是走同一条分支：``_drive`` 只看第一次节点，排空之后就交给接手者（本次用例不接手）。
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

    这一轮不需要接手，所以它也**不产生「等接手」的标记**（那是还有待跑节点时才有的）。
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
    """手上同时有两个在途运行时，收尾对两者一起生效；各自停在可交接的边界，都不是被取消。

    边界落点的证据是两样东西：第一个模型节点的文本原样落库（没有被截断或重复补写），以及那次
    工具调用只有调用、没有结果——说明图停在了「模型节点已完成、工具节点还没跑」的位置。它读
    checkpoint：这两个被排空的运行还没落表，接手方最终收尾时才会写。
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
        # 2、没有走到终态——它停在边界，等接手者续跑。
        assert turn.status == "incomplete"
        # 3、工具调用只有调用、没有结果：图确实停在工具节点之前。
        assert turn.traces and turn.traces[0].content is None
    # 4、两个被排空的运行各留下一条只含 id 的日志。
    drained = [each for each in caplog.messages if "已排空" in each]
    assert len(drained) == 2
    assert all(str(turns[-1].run_id) in " ".join(drained) for turns in (first_turns, second_turns))
    # 5、两次运行的会话都被标记成「等接手」：在途运行 id 还在，排空时刻已写下。
    for thread_id in (first_thread, second_thread):
        record = app.state.offline_threads.threads[thread_id]
        assert record.drained_at is not None
        assert record.active_run_id is not None


def test_a_drained_session_rejects_new_questions_and_survives_the_zombie_threshold() -> None:
    """排空之后那个会话进入「等接手」：新提问被 409 拒，且不会被失活分支顶掉。

    第二半是这条用例真正的用途：排空之后旧进程不再续期活跃时间，失活分支两分钟后就会命中，
    如果占位条件没排除带标记的会话，用户就能在标记还在的时候开新一轮，两个进程写同一份图状态。
    所以必须把活跃时间推过失活阈值再试一次——否则在毫秒级测试里这条永远绿。
    """

    model = ToolBoundaryStreamModel(messages=iter([]))
    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    app, _search = create_agent_app(model, checkpointer=checkpointer, threads=threads)

    async def drain_one() -> UUID:
        async with app.router.lifespan_context(app):
            task, queue, first = await _open_and_wait_for_token(
                app, payload={"message": "央行降息了吗"}
            )
            thread_id = UUID(first[0]["thread_id"])
        await _collect(queue)
        await task
        return thread_id

    thread_id = run(drain_one())
    record = threads.threads[thread_id]
    # 1、排空真的在会话行上留下了标记，而且在途运行 id 没被释放。
    assert record.drained_at is not None
    assert record.active_run_id is not None

    # 2、拿一个「有图、但不扫描接手标记」的实例来发新提问（与生产的坏副本不认领同形）。
    reader, _ = create_agent_app(
        model,
        checkpointer=checkpointer,
        threads=threads,
        agent_run_registry_factory=lambda runtime=None: offline_agent_run_registry_factory(
            threads, runtime=None
        ),
    )

    async def ask() -> tuple[httpx.Response, httpx.Response]:
        async with reader.router.lifespan_context(reader):
            transport = httpx.ASGITransport(app=reader)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                fresh = await client.post(
                    "/agent/chat", json={"message": "再问一次", "thread_id": str(thread_id)}
                )
                # 把活跃时间推过失活阈值：这一步会命中失活分支，而带标记的会话必须把它排除。
                record.last_active_at = datetime.now(UTC) - timedelta(
                    seconds=RUN_ZOMBIE_THRESHOLD_SECONDS * 2
                )
                stale = await client.post(
                    "/agent/chat", json={"message": "再问一次", "thread_id": str(thread_id)}
                )
                return fresh, stale

    fresh, stale = run(ask())

    assert fresh.status_code == 409
    assert fresh.json()["code"] == "agent_run_in_progress"
    assert stale.status_code == 409
    assert stale.json()["code"] == "agent_run_in_progress"


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

    这条是「放弃」这条收尾路径：它**不经过接手**（没有标记），用户看到未写完的内容、会话已解锁，
    可以立刻重新提问——与强杀那一类在界面上表现一致。
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


# ---- 工单 03：接手 ----


class CountingAnswerModel(StreamingChatModel):
    """记录被调用次数的正常作答模型，用来断言「同一次运行只被接手了一次」。"""

    calls: int = 0

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        """记一次调用，其余照父类产出。"""

        self.calls += 1
        yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)


def _draining_app(model: Any, *, checkpointer: Any, threads: InMemoryAgentThreadService, drain_timeout: float | None = None) -> Any:
    """建一个应用，用它把一个多节点运行排空到边界，返回 ``(应用, 会话 id)``。"""

    app, _search = create_agent_app(
        model, checkpointer=checkpointer, threads=threads, drain_timeout=drain_timeout
    )

    async def scenario() -> UUID:
        async with app.router.lifespan_context(app):
            task, queue, first = await _open_and_wait_for_token(
                app, payload={"message": "央行降息了吗"}
            )
            thread_id = UUID(first[0]["thread_id"])
        await _collect(queue)
        await task
        return thread_id

    return app, run(scenario())


def test_a_new_instance_takes_over_the_drained_run_and_finishes_it() -> None:
    """排空留下的库状态交给一个新实例，它自动接手把运行跑完、回放给出完整答案、会话解锁。"""

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    _first_app, thread_id = _draining_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )
    assert threads.threads[thread_id].drained_at is not None
    # 排空到可交接边界的那一刻**不写**会话历史：那次运行还没结束，写由接手方在最终收尾时做。
    assert threads.recorded_run_messages == []

    app, _search = create_agent_app(
        StreamingChatModel(messages=iter([AIMessage(content="完整答案。")])),
        checkpointer=checkpointer,
        threads=threads,
    )

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            # 接手者把那个中间件/工具/模型节点跑完后释放会话位。
            await _wait_until(lambda: threads.threads[thread_id].active_run_id is None)

    run(asyncio.wait_for(scenario(), timeout=15.0))

    record = threads.threads[thread_id]
    assert record.drained_at is None
    assert record.active_run_id is None
    replay = run(_read_replay(checkpointer, threads, thread_id))
    # 上一轮的两个模型节点合起来就是完整答案；没有新提问被拼进去。
    assert replay["turns"][-1]["status"] == "completed"
    assert replay["turns"][-1]["answer"] == "我查一下。完整答案。"
    assert replay["turns"][-1]["question"] == "央行降息了吗"
    assert replay["active_run_id"] is None
    # 接手方在最终收尾时才写，而且每次运行只写一次；写的是那一轮自己那一组。
    assert len(threads.recorded_run_messages) == 1
    recorded = threads.recorded_run_messages[0]
    assert recorded.thread_id == thread_id
    assert [group.run_id for group in recorded.runs] == [recorded.run_id]
    assert [row.role for row in recorded.runs[0].rows] == [
        "question",
        "answer",
        "tool_call",
        "tool_result",
        "answer",
    ]


class RecordingResolver:
    """按 id 给出预置客户端的解析替身，并记下被问过哪些 id。"""

    def __init__(self, clients: dict[UUID, Any]) -> None:
        self._clients = clients
        self.requested: list[UUID] = []

    async def resolve_client(self, model_id: UUID) -> Any:
        self.requested.append(model_id)
        return self._clients[model_id]


def test_a_taken_over_run_still_uses_the_model_frozen_in_its_question() -> None:
    """接手那一轮解析的是提问消息里冻结的那个模型，不是会话当前的选择、也不是当时的默认。

    这一轮的用户是带着那个模型开始的，接管是平台内部的事。所以用例把会话的选择换成另一个
    模型、把那一个在目录里标成停用：接手路径不过「开始运行之前解析」那道 HTTP 门，它读的是
    冻结快照——解析来源只被问过那一个 id，替身客户端也只答出那一个模型的文本。
    """

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    frozen_id, other_id = uuid4(), uuid4()

    draining_resolver = RecordingResolver(
        {
            frozen_id: ToolBoundaryStreamModel(messages=iter([])),
            other_id: FailingChatModel(error=AssertionError("不该用别的模型")),
        }
    )
    first_app, _search = create_agent_app(
        None, model_resolver=draining_resolver, checkpointer=checkpointer, threads=threads
    )
    first_catalog = InMemoryLlmModelSelectionService(
        [
            OfflineCatalogModel(other_id, is_default=True),
            OfflineCatalogModel(frozen_id),
        ]
    )
    first_app.dependency_overrides[get_llm_model_selection_service] = lambda: first_catalog

    async def drain_one() -> UUID:
        async with first_app.router.lifespan_context(first_app):
            task, queue, first = await _open_and_wait_for_token(
                first_app,
                payload={"message": "央行降息了吗", "llm_model_id": str(frozen_id)},
            )
            thread_id = UUID(first[0]["thread_id"])
        await _collect(queue)
        await task
        return thread_id

    thread_id = run(drain_one())
    assert threads.threads[thread_id].drained_at is not None
    # 排空之后这一轮已经不是会话当前的选择了，而且在新的目录里那条已被停用。
    threads.threads[thread_id].llm_model_id = other_id

    taking_resolver = RecordingResolver(
        {
            frozen_id: StreamingChatModel(messages=iter([AIMessage(content="完整答案。")])),
            other_id: FailingChatModel(error=AssertionError("不该用别的模型")),
        }
    )
    taking_app, _search2 = create_agent_app(
        None, model_resolver=taking_resolver, checkpointer=checkpointer, threads=threads
    )
    taking_catalog = InMemoryLlmModelSelectionService(
        [
            OfflineCatalogModel(other_id, is_default=True),
            OfflineCatalogModel(frozen_id, enabled=False),
        ]
    )
    taking_app.dependency_overrides[get_llm_model_selection_service] = lambda: taking_catalog

    async def take_over() -> None:
        async with taking_app.router.lifespan_context(taking_app):
            await _wait_until(lambda: threads.threads[thread_id].active_run_id is None)

    run(asyncio.wait_for(take_over(), timeout=15.0))

    assert set(taking_resolver.requested) == {frozen_id}, "接手只该解析快照里那个模型"
    replay = run(_read_replay(checkpointer, threads, thread_id))
    assert replay["turns"][-1]["status"] == "completed"
    assert replay["turns"][-1]["answer"] == "我查一下。完整答案。"
    assert replay["turns"][-1]["llm_model"]["id"] == str(frozen_id), "回放读的也是那一轮的快照"


def test_two_instances_racing_for_one_marker_leave_exactly_one_takeover() -> None:
    """两个实例同时扫到同一个标记时只有一个接手，另一个不写任何东西。"""

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    _first_app, thread_id = _draining_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )

    first_model = CountingAnswerModel(messages=iter([AIMessage(content="第一份答案。")]))
    second_model = CountingAnswerModel(messages=iter([AIMessage(content="第二份答案。")]))
    first_app, _search1 = create_agent_app(first_model, checkpointer=checkpointer, threads=threads)
    second_app, _search2 = create_agent_app(second_model, checkpointer=checkpointer, threads=threads)

    async def scenario() -> None:
        async with first_app.router.lifespan_context(first_app), second_app.router.lifespan_context(
            second_app
        ):
            await _wait_until(lambda: threads.threads[thread_id].active_run_id is None)

    run(asyncio.wait_for(scenario(), timeout=15.0))

    # 只有抢到所有权的那一个实例调了模型；另一个什么也没写。
    assert first_model.calls + second_model.calls == 1
    assert threads.threads[thread_id].drained_at is None


def test_an_instance_without_a_graph_does_not_claim_the_marker() -> None:
    """拿不到可用的图时不认领：标记还在、会话仍被 409 拒（坏副本不替健康副本放弃）。"""

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    _first_app, thread_id = _draining_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )

    # Agent 装配失败的实例：它有完整的会话 Service，但注册表拿不到图。
    broken, _search = create_agent_app(
        ToolBoundaryStreamModel(messages=iter([])),
        checkpointer=checkpointer,
        threads=threads,
        agent_build_error=RuntimeError("装配失败"),
    )

    async def scenario() -> None:
        async with broken.router.lifespan_context(broken):
            # 跑过好几个扫描周期，确认它确实什么都没接。
            await asyncio.sleep(0.05)

    run(scenario())

    record = threads.threads[thread_id]
    assert record.drained_at is not None
    assert record.active_run_id is not None

    # 用有图但不扫描的实例发新提问：标记还在，会话被 409 拒。
    response = run(
        send(
            _reader_holder(checkpointer, threads),
            "POST",
            "/agent/chat",
            json={"message": "再问一次", "thread_id": str(thread_id)},
        )
    )
    assert response.status_code == 409
    assert response.json()["code"] == "agent_run_in_progress"


def _reader_holder(checkpointer: Any, threads: InMemoryAgentThreadService) -> Any:
    """建一个「有图、但不扫描接手标记」的实例，用来在做断言前后发普通请求。"""

    reader, _search = create_agent_app(
        StreamingChatModel(messages=iter([])),
        checkpointer=checkpointer,
        threads=threads,
        agent_run_registry_factory=lambda runtime=None: offline_agent_run_registry_factory(
            threads, runtime=None
        ),
    )
    return reader


def test_a_claim_that_cannot_rebuild_its_context_is_abandoned_and_unlocks_the_session() -> None:
    """认领成功但重建不出运行范围：按放弃收尾，会话立刻能开新一轮。"""

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    app, _search = create_agent_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )
    thread_id, run_id = uuid4(), uuid4()
    # 标记与在途运行都在，但 checkpoint 里没有对应的那一轮（最典型是历史压缩把提问抹掉了）。
    seed_owned_thread(app, thread_id, active_run_id=run_id, drained_at=datetime.now(UTC))

    taker, _search2 = create_agent_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )

    async def scenario() -> None:
        async with taker.router.lifespan_context(taker):
            await _wait_until(lambda: threads.threads[thread_id].active_run_id is None)

    run(asyncio.wait_for(scenario(), timeout=10.0))

    record = threads.threads[thread_id]
    assert record.active_run_id is None
    assert record.drained_at is None

    # 会话立刻能开新一轮：不再被 409 拒。
    answer = run(
        send(
            _reader_holder(checkpointer, threads),
            "POST",
            "/agent/chat",
            json={"message": "新问题", "thread_id": str(thread_id)},
        )
    )
    assert answer.status_code == 200


def test_a_resumed_run_that_fails_mid_way_is_abandoned_and_unlocks_the_session() -> None:
    """接手之后运行中途抛错：按未完成收尾、释放会话位，用户可以重新提问。"""

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    _first_app, thread_id = _draining_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )
    taker, _search = create_agent_app(
        FailingChatModel(error=openai.APITimeoutError(request=None)),  # type: ignore[arg-type]
        checkpointer=checkpointer,
        threads=threads,
    )

    async def scenario() -> None:
        async with taker.router.lifespan_context(taker):
            await _wait_until(lambda: threads.threads[thread_id].active_run_id is None)

    run(asyncio.wait_for(scenario(), timeout=15.0))

    assert threads.threads[thread_id].active_run_id is None
    replay = run(_read_replay(checkpointer, threads, thread_id))
    assert replay["turns"][-1]["status"] == "incomplete"

    answer = run(
        send(
            _reader_holder(checkpointer, threads),
            "POST",
            "/agent/chat",
            json={"message": "新问题", "thread_id": str(thread_id)},
        )
    )
    assert answer.status_code == 200


def test_a_run_abandoned_by_drain_timeout_is_not_taken_over() -> None:
    """排空超时按放弃收尾的运行没有标记，接手者扫不到它、也不会去动它。"""

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    _first_app, thread_id = _draining_app(
        NeverEndingStreamModel(messages=iter([])),
        checkpointer=checkpointer,
        threads=threads,
        drain_timeout=0.05,
    )
    record = threads.threads[thread_id]
    assert record.drained_at is None
    assert record.active_run_id is None

    idle_model = CountingAnswerModel(messages=iter([AIMessage(content="不该跑到这里。")]))
    taker, _search = create_agent_app(idle_model, checkpointer=checkpointer, threads=threads)

    async def scenario() -> None:
        async with taker.router.lifespan_context(taker):
            await asyncio.sleep(0.05)

    run(scenario())

    assert idle_model.calls == 0


def test_a_stop_request_written_before_takeover_still_stops_the_run() -> None:
    """交接不吞停止：排空之后、接手之前写下的停止请求，接手之后仍然生效。"""

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    _first_app, thread_id = _draining_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )
    in_flight = threads.threads[thread_id].active_run_id
    assert in_flight is not None
    run(threads.request_stop(thread_id=thread_id, run_id=in_flight))

    # 接手之后要跑的那一段会慢慢吐字，给停止轮询留出生效窗口。
    taker, _search = create_agent_app(
        SlowCompletingStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )

    async def scenario() -> None:
        async with taker.router.lifespan_context(taker):
            await _wait_until(lambda: threads.threads[thread_id].active_run_id is None)

    run(asyncio.wait_for(scenario(), timeout=15.0))

    replay = run(_read_replay(checkpointer, threads, thread_id))
    assert replay["turns"][-1]["status"] == "incomplete"


def test_a_claimed_run_is_handed_over_again_when_the_taking_instance_shuts_down() -> None:
    """一个进程接手之后立刻进入收尾：那次运行会被重新写下标记、交给下一个进程。"""

    checkpointer = InMemorySaver()
    threads = InMemoryAgentThreadService()
    _first_app, thread_id = _draining_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )

    # 接手之后又做了一次工具调用，所以它后面还有待跑节点，收尾时会被重新排空。
    taker, _search = create_agent_app(
        ToolBoundaryStreamModel(messages=iter([])), checkpointer=checkpointer, threads=threads
    )

    async def scenario() -> None:
        async with taker.router.lifespan_context(taker):
            await _wait_until(
                lambda: thread_id in getattr(taker.state.agent_run_registry, "_runs", {})
            )
        # 退出 lifespan：这一刻触发排空，接手的运行被重新写下标记。

    run(asyncio.wait_for(scenario(), timeout=15.0))

    record = threads.threads[thread_id]
    assert record.drained_at is not None
    assert record.active_run_id is not None
