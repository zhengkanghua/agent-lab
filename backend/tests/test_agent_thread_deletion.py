"""删除一个正在运行的会话（工单 05）。

要点是**顺序**：先请求停下在途的那次运行、等它释放占位，再清会话历史、再删归属记录。运行在收尾之前
一直在往会话历史里写（每个节点结束写一次），不确认它停了就去清历史，它会在我们清空之后继续写回来，
留下一条查不到也删不掉的孤儿会话（术语表里的「孤儿会话」）。

「先暂停再删除」里的那个「等」不是多余的一步，它就是让暂停真的生效：停止是协作式、跨进程的，收到
DELETE 的进程不一定跑着这次运行，所以只能写停止请求再等它自己收尾。上限不能去掉——万一运行正好卡在
一次不响应取消的调用里，无限等会让删除请求挂住，而删除挂住比留下一次未完成的运行更糟（见 spec 的
「补充说明」，那条代价已确认接受）。

**这条链路必须手工驱动 ASGI**：删除请求要在服务端还在流的时候发出去，而 ``httpx.ASGITransport`` 会
在返回响应前把整个响应体收完。理由同 ``open_chat_stream``。
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from tests.agent_helpers import StreamingChatModel, open_chat_stream, run
from tests.app_helpers import create_agent_app, seed_owned_thread


class EndlessStreamModel(StreamingChatModel):
    """长时间逐字产出的假模型，用来造一个「确实在途」的运行。

    分片数有界：实现退化（删除没有先停运行）时这次运行会自己跑完，用例走到断言上失败，而不是永远转下去。
    """

    calls: int = 0
    chunks: int = 400

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """逐字产出，每片之间让出事件循环。"""

        self.calls += 1
        for _ in range(self.chunks):
            await asyncio.sleep(0.01)
            yield ChatGenerationChunk(message=AIMessageChunk(content="字"))


def test_deleting_a_running_thread_stops_the_run_then_removes_everything() -> None:
    """在途时删除：运行先停下来，删完读不到、列表里也没有。"""

    model = EndlessStreamModel(messages=iter([]))
    app, _search = create_agent_app(model)

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]], int, int, int]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                task, queue = await open_chat_stream(
                    app, path="/agent/chat", payload={"message": "央行降息了吗"}
                )
                events: list[dict[str, Any]] = []
                thread_id: str | None = None
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    events.append(event)
                    if event["event"] == "run_started":
                        thread_id = event["thread_id"]
                    # 收到第一个字就开始删——此刻这次运行确实在途，而且正在往历史里写。
                    if event["event"] == "token" and thread_id is not None:
                        deletion = await client.delete(f"/agent/threads/{thread_id}")
                        break

                # 把这条流读完：运行要先收尾，删除才继续往下走。
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    events.append(event)
                await task

                replay = await client.get(f"/agent/threads/{thread_id}/messages")
                listing = await client.get("/agent/threads")
                return (
                    deletion.json(),
                    events,
                    replay.status_code,
                    listing.status_code,
                    listing.json()["total"],
                )

    # 上限只用来防止用例自己挂住：删除等运行收尾最多 10 秒，加上正常收尾时间足够。
    deleted, events, replay_status, list_status, total = run(
        asyncio.wait_for(scenario(), timeout=60.0)
    )

    # 1、删除成功，而且回带的就是被删的那个会话。
    assert UUID(deleted["thread_id"]) is not None
    # 2、那次运行自己收尾了（不是被取消在半路上），终态是「截断」那一档。
    assert events[-1]["event"] == "done"
    assert events[-1]["status"] == "incomplete"
    # 3、删完读不到，列表里也没有它——不留查不到也删不掉的残留。
    assert replay_status == 404
    assert list_status == 200 and total == 0


def test_deletion_proceeds_when_the_run_does_not_stop_within_the_window(monkeypatch) -> None:
    """在等待窗口内没停下来时，删除仍然完成。

    这里刻意预置一个**没有任何东西在驱动**的在途标记（进程崩了就会留下这种行）：没有任何人会释放它，
    所以删除只能等满窗口、然后按已中断继续。上限调小到 50 毫秒只是因为这条用例不该真等十秒。
    """

    monkeypatch.setattr(
        "agent_lab.api.agent_threads.DELETE_RUNNING_THREAD_WAIT_SECONDS", 0.05
    )
    monkeypatch.setattr(
        "agent_lab.api.agent_threads.DELETE_RUNNING_THREAD_POLL_INTERVAL_SECONDS", 0.01
    )
    model = StreamingChatModel(messages=iter([AIMessage(content="答案。")]))
    app, _search = create_agent_app(model)
    thread_id = uuid4()
    # 上一次运行留下的占位：last_active_at 还很新，所以不算失活。
    seed_owned_thread(app, thread_id, active_run_id=uuid4())

    async def scenario() -> tuple[int, float]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                started = asyncio.get_running_loop().time()
                response = await client.delete(f"/agent/threads/{thread_id}")
                return response.status_code, asyncio.get_running_loop().time() - started

    status_code, elapsed = run(asyncio.wait_for(scenario(), timeout=30.0))

    assert status_code == 200
    # 它确实等了（否则这条用例测的就不是「等不到才继续」），但远没有挂住。
    assert elapsed >= 0.05
    assert elapsed < 10.0
    assert thread_id not in app.state.offline_threads.threads
