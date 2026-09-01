"""``agent/replay.py`` 的离线测试：checkpointer 消息列表 → 前端轮次结构。

本文件不连 PostgreSQL、不访问网络、不调真实大模型。其中一条用例会跑**真实的**
``SummarizationMiddleware``（配假模型），因为它要钉住的正是那个中间件的输出形态——用手写的假摘要
消息去测，等于把「我以为它长这样」当成事实，上游一改测试照样通过、线上却出错。
"""

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent_lab.agent.replay import build_replay_turns
from tests.agent_helpers import (
    ScriptedChatModel,
    build_offline_graph,
    run,
    tool_call_message,
)


def test_plain_conversation_becomes_question_answer_pairs() -> None:
    """一问一答的历史按轮切开，顺序保持。"""

    messages = [
        HumanMessage(content="央行降息了吗"),
        AIMessage(content="降了 25 个基点。"),
        HumanMessage(content="什么时候"),
        AIMessage(content="上周四。"),
    ]

    turns, summarized, summary = build_replay_turns(messages)

    assert [(turn.question, turn.answer) for turn in turns] == [
        ("央行降息了吗", "降了 25 个基点。"),
        ("什么时候", "上周四。"),
    ]
    assert summarized is False
    assert summary is None


def test_tool_call_and_result_are_paired_by_tool_call_id() -> None:
    """工具轨迹按 ``tool_call_id`` 精确配对，不靠顺序也不靠工具名。

    刻意让两次调用同名、且结果**倒序**返回：如果实现是按「同名的最早那条」FIFO 配对
    （前端在 SSE 路径上不得不那么做，因为事件里没有 id），这条会把两个结果配错。
    回放路径有 id 可用，就该用它。
    """

    messages = [
        HumanMessage(content="比较两家的说法"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "search_news", "args": {"query": "甲"}, "id": "call-1"},
                {"name": "search_news", "args": {"query": "乙"}, "id": "call-2"},
            ],
        ),
        ToolMessage(content="乙的结果", tool_call_id="call-2", name="search_news"),
        ToolMessage(content="甲的结果", tool_call_id="call-1", name="search_news"),
        AIMessage(content="两家说法一致。"),
    ]

    turns, _summarized, _summary = build_replay_turns(messages)

    assert len(turns) == 1
    assert [(trace.arguments["query"], trace.content) for trace in turns[0].traces] == [
        ("甲", "甲的结果"),
        ("乙", "乙的结果"),
    ]


def test_failed_tool_result_keeps_its_failed_flag() -> None:
    """``status == "error"`` 的工具结果在回放里仍标成失败。"""

    messages = [
        HumanMessage(content="查一下"),
        tool_call_message("search_news", {"query": "利率"}),
        ToolMessage(
            content="工具调用失败：新闻数据库当前不可用。",
            tool_call_id="call-search_news",
            name="search_news",
            status="error",
        ),
        AIMessage(content="暂时查不了。"),
    ]

    turns, _summarized, _summary = build_replay_turns(messages)

    assert turns[0].traces[0].failed is True


def test_tool_call_without_result_replays_as_pending_content_none() -> None:
    """只有调用没有结果时 ``content`` 是 ``None``，不编一句「已中断」文案。

    这种历史来自「工具还没返回，运行就被取消或报错」。回放的职责是如实反映存下来的东西，
    提示语归前端——后端编一句的话，那句话会被当成工具真的返回过的内容。
    """

    messages = [
        HumanMessage(content="查一下"),
        tool_call_message("search_news", {"query": "利率"}),
    ]

    turns, _summarized, _summary = build_replay_turns(messages)

    assert turns[0].traces[0].content is None
    assert turns[0].traces[0].failed is False


def test_turn_without_answer_replays_with_empty_answer() -> None:
    """首轮运行失败留下的「只有提问」历史，回放成 answer 为空串的一轮。

    不丢掉这一轮：会话列表里它是存在的（归属记录在流开始前就写了），点进去却什么都没有会让人
    以为回放坏了。也不伪造一个 error——当时的失败原因没有存下来，编一个会误导排查方向。
    """

    turns, _summarized, _summary = build_replay_turns([HumanMessage(content="没答成的问题")])

    assert len(turns) == 1
    assert turns[0].question == "没答成的问题"
    assert turns[0].answer == ""


def test_multimodal_content_blocks_keep_only_text() -> None:
    """列表形态的 content 只取 ``type == "text"`` 的部分，不把结构原样拼进回答。"""

    messages = [
        HumanMessage(content="问题"),
        AIMessage(
            content=[
                {"type": "text", "text": "可见的回答。"},
                {"type": "tool_use", "id": "x", "name": "search_news", "input": {}},
            ]
        ),
    ]

    turns, _summarized, _summary = build_replay_turns(messages)

    assert turns[0].answer == "可见的回答。"


def test_model_message_before_any_question_is_dropped() -> None:
    """首条提问之前的模型消息被丢弃，而不是造出一轮空提问。"""

    messages = [
        AIMessage(content="没有对应提问的回答"),
        HumanMessage(content="真正的提问"),
        AIMessage(content="真正的回答"),
    ]

    turns, _summarized, _summary = build_replay_turns(messages)

    assert [(turn.question, turn.answer) for turn in turns] == [
        ("真正的提问", "真正的回答")
    ]


def test_summary_message_produced_by_real_middleware_is_recognised() -> None:
    """**契约测试**：真实 ``SummarizationMiddleware`` 造出的摘要消息能被认出来。

    为什么必须用真中间件跑一遍，而不是手写一条带 ``lc_source`` 的 HumanMessage：那个标记是
    langchain 的**内部约定**，不是公开契约。手写的假消息只能证明「实现和我写的假数据一致」，
    上游换个标记名、或者改成 SystemMessage，手写版照样通过，而线上会把一句英文摘要当成用户
    自己问过的话显示在对话记录里。

    这条测试的价值就在于上游一改它就红，而且失败信息直接指向「摘要没被识别」。

    实测形态（langchain 1.3.15）：压缩动作是 ``RemoveMessage(id=REMOVE_ALL_MESSAGES)`` 清空
    整个列表再重建，摘要被包成一条 **HumanMessage**，带
    ``additional_kwargs={"lc_source": "summarization"}``，正文前面还有一句英文
    ``Here is a summary of the conversation to date:``。
    """

    # 1、装一个真实中间件流水线的图。压缩阈值是 40 条消息（agent/limits.py），所以要先攒够历史。
    #    假模型每轮回一句，一轮产生 2 条消息（提问 + 回答），22 轮足够越过阈值。
    model = ScriptedChatModel(
        responses=[AIMessage(content=f"第 {index} 轮回答") for index in range(1, 40)]
    )
    graph = build_offline_graph(model)
    config = {"configurable": {"thread_id": str(uuid4())}}

    async def drive() -> list:
        for index in range(1, 23):
            await graph.ainvoke(
                {"messages": [{"role": "user", "content": f"第 {index} 轮提问"}]},
                config=config,
            )
        snapshot = await graph.aget_state(config)
        return snapshot.values["messages"]

    messages = run(drive())

    # 2、先确认压缩真的发生了。少了这一步，模型行为一变（比如阈值改大）这条测试会变成空转：
    #    没有摘要消息时 summarized 本来就是 False，断言 False == False 什么也没证明。
    assert any(
        getattr(message, "additional_kwargs", {}).get("lc_source") == "summarization"
        for message in messages
    ), "历史没有被压缩，这条用例失去了意义——检查 SUMMARIZATION_TRIGGER_MESSAGES 或轮数"

    turns, summarized, summary = build_replay_turns(messages)

    # 3、摘要被识别，而且没有混进轮次里冒充用户提问。
    assert summarized is True
    assert summary is not None
    questions = [turn.question for turn in turns]
    assert not any("summary of the conversation" in question for question in questions)
    assert all(question.endswith("轮提问") for question in questions)
