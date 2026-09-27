"""端到端接缝：中间件内部的历史摘要压缩调用也进账本（内存 SQLite）。

这是唯一能证明「摘要压缩调用真的被记账」的用例：它在真实的 LangGraph 图里跑够轮次逼出摘要压缩，
走真实的装配 → 采集器 → 落库 → 查询链路。它落点是内存 SQLite，因此证明的是**记账逻辑**，不是
真库行为；真库落库路径由 ``tests/test_usage_postgres_integration.py`` 覆盖。

**为什么起步点是红的。** 旧实现只把「模型节点的产出」加进合计，而摘要调用发生在中间件内部、
不在模型节点，于是长会话里最烧钱的那部分一次都进不了那份合计。把这件事说准很重要：不是框架
把中间件内部的调用从流里删掉，而是那行过滤只认模型节点。这条用例因此断言账本条数等于**模型
客户端被真实调用的次数**（摘要 + 回答共两次），而不是等于模型节点的产出次数（一次）。

本文件不连 PostgreSQL、不访问网络、不调真实大模型。
"""

import asyncio
from typing import Any
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from tests.agent_helpers import ScriptedChatModel
from tests.app_helpers import create_agent_app, seed_owned_thread
from tests.usage_helpers import usage_service


def run(coroutine: Any) -> Any:
    """执行异步测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


def long_history() -> list[BaseMessage]:
    """造一段够长的历史，让本轮提问越过摘要压缩的触发阈值。

    触发条件是消息条数（见 ``agent/limits.py`` 的 ``SUMMARIZATION_TRIGGER_MESSAGES``），不是
    token 数：token 计数依赖分词器，而中转站背后用哪个分词器我们并不掌握。
    """

    history: list[BaseMessage] = []
    for index in range(20):
        history.append(HumanMessage(content=f"旧问{index}"))
        history.append(AIMessage(content=f"旧答{index}"))
    history.append(HumanMessage(content="上一问"))
    return history


def test_the_summarization_call_lands_in_the_ledger_too() -> None:
    """一轮触发摘要压缩的对话：账本里既有摘要那次调用，也有同轮的主调用，且共享运行标识。"""

    thread_id = uuid4()
    model = ScriptedChatModel(
        responses=[AIMessage(content="旧背景摘要"), AIMessage(content="当前答案")]
    )

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with usage_service() as usage_runtime:
            app, _search = create_agent_app(model, usage_runtime=usage_runtime)
            async with app.router.lifespan_context(app):
                seed_owned_thread(app, thread_id)
                # 预置历史而不是真的聊 20 轮：摘要触发条件只看消息条数，预置能把这条用例从
                # 20 次 HTTP 往返压成 1 次，而中间件走的仍是真实路径。
                await app.state.agent_runtime.graph.aupdate_state(
                    {"configurable": {"thread_id": str(thread_id)}},
                    {"messages": long_history()},
                )
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    async with client.stream(
                        "POST",
                        "/agent/chat",
                        json={"message": "当前提问", "thread_id": str(thread_id)},
                    ) as response:
                        assert response.status_code == 200
                        async for _chunk in response.aiter_text():
                            pass
                    # 写入是排到事件循环上的，先收干再查，否则查询与写入会赛跑。
                    await usage_runtime.collector.drain()
                    replay = (await client.get(f"/agent/threads/{thread_id}/messages")).json()
                    page = (await client.get("/usage/records")).json()
            return replay, page

    replay, page = run(scenario())
    records = page["items"]

    assert replay["summarized"] is True, "这一轮必须真的触发了摘要压缩，否则用例什么都没证明"
    assert "旧背景摘要" in (replay["summary"] or "")
    assert model.call_count == 2, "模型客户端被真实调用两次：一次摘要、一次回答"
    assert len(records) == model.call_count, "摘要那次调用也必须留下记录"
    assert {item["run_id"] for item in records} == {replay["turns"][-1]["run_id"]}, (
        "摘要调用与同轮主调用属于同一次运行，运行标识必须一致"
    )
    assert all(item["status"] == "completed" for item in records)
