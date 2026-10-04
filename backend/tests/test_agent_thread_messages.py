"""运行收尾把这一轮写进会话历史业务表（``agent_thread_messages``）。

覆盖三件事：

1. **行级投影**（纯函数）：提问、回答、每个工具调用、每个工具结果各是什么样，空正文的助手消息
   不单独成行，工具调用与结果靠 id 配对，运行元数据透传，证据列去掉正文片段。
2. **写入语义**（真实 ORM 与事务跑在内存 SQLite 上，照 ``tests/task_helpers`` 的做法）：整组替换、
   重复收尾只留一份且沿用第一次的顺序号、只写表里还缺的那些组（缺的那些就是被强杀那一轮留下的：
   补进去的内容照快照写，快照里已成占位文字的正文也照实落表）、顺序号接着会话里已有的最大值、写
   一个会话不动另一个会话的行，以及**摘要行**（压缩留下的那一行）跟着「产生它的那一次运行」那一组
   走、同一会话里只有一行。
3. **应用层收尾真的会写**（离线图 + 内存 checkpointer + 真实会话 Service 跑在内存 SQLite 上）：
   正常答完、用户停止、上游失败各写出一轮；写表失败只记日志、释放会话位与终态事件照旧。
   排空那一支**不写**、接手方收尾时才写，那条断言在 ``tests/test_agent_handover.py`` 里
   （那两条用例本来就在跑排空与接手）。
4. **删会话连带删这张表**（同样跑在真实 ORM 与事务上）：用户删会话、旧会话清理那条批量删、
   以及孤儿残余按 ``thread_id`` 删，都走 Service 里那三个方法；跨会话隔离与归属不匹配时的回滚
   同在这一节。（三条命令各自的入口与参数解析在 ``tests/test_cli.py``。）

**为什么断言表里的事实**：这张表是用户回看的唯一数据来源，「跑完一次运行之后表里有哪些行」才是
验收面，不是「写了几条 SQL」。所以整套用例都读表里读出来的行，不去数语句。

**载荷为什么用真库**：``InMemoryAgentThreadService`` 只记「谁在什么时候调了写入方」，它没有表、
也不复制整组替换与顺序号那套语义（复制一份必然与真实实现漂移）。行怎么落必须由真实 Service 在
真实库上证明，所以这里宁可多一层 SQLite 夹具，也不给那份语义写第二份实现。
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from agent_lab.agent.context import AgentContext
from agent_lab.agent.errors import AgentThreadNotFoundError
from agent_lab.agent.evidence import DocumentEvidence, ToolEvidence
from agent_lab.agent.limits import PRUNED_TOOL_RESULT_PLACEHOLDER
from agent_lab.agent.replay import build_replay_turns, group_messages_by_run, summary_messages
from agent_lab.agent.streaming import stream_agent_events
from agent_lab.agent.thread_messages import RunMessageRows, project_run_messages
from agent_lab.agent.tools.search_documents import build_search_documents_tool
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.models.agent_thread import AgentThreadRecord
from agent_lab.models.agent_thread_message import AgentThreadMessageRecord
from agent_lab.models.user_preference import UserPreferenceRecord
from agent_lab.schemas.llm_models import ResolvedLlmModel
from agent_lab.services.agent_thread_service import AgentThreadService
from tests.agent_helpers import (
    OFFLINE_LANGSMITH_SETTINGS,
    ScriptedChatModel,
    StreamingChatModel,
    build_offline_graph,
    open_chat_stream,
    parallel_tool_call_message,
    run,
    tool_call_message,
    window_that_triggers,
)
from tests.agent_scope_helpers import NEWS_SCOPE, invoke_tool_message
from tests.app_helpers import DEFAULT_OFFLINE_MODEL_ID, create_agent_app
from tests.test_agent_runs import DyingStreamModel
from tests.test_agent_tools import FakeSearchService, build_result


# ---- 行级投影（纯函数）----


def question(
    text: str,
    *,
    run_id: UUID,
    scope: Any = NEWS_SCOPE,
    model: dict[str, Any] | None = None,
) -> HumanMessage:
    """构造一条带运行元数据的用户提问。

    形状照 ``agent/streaming.py`` 真实写进消息的那一份：``run_id`` 无条件带，范围与当轮模型
    只在真有时带（键名与写入方一致，都是 ``llm_model``）。
    """

    agent_run: dict[str, Any] = {"run_id": str(run_id)}
    if scope is not None:
        agent_run["scope"] = scope.model_dump(mode="json")
    if model is not None:
        agent_run["llm_model"] = model
    return HumanMessage(content=text, additional_kwargs={"agent_run": agent_run})


def answered(run_id: UUID, text: str, **message_kwargs: Any) -> AIMessage:
    """构造一条已完整作答的助手消息（中间件会给它盖上运行标识与完成态）。"""

    return AIMessage(
        content=text,
        additional_kwargs={"agent_run": {"run_id": str(run_id), "completed": True}},
        **message_kwargs,
    )


def artifact(run_id: UUID, *, evidence: tuple[DocumentEvidence, ...] = ()) -> dict[str, Any]:
    """构造一条 Tool 结果消息上挂的那份 artifact。"""

    return ToolEvidence(run_id=run_id, scope=NEWS_SCOPE, evidence=evidence).model_dump(mode="json")


def citation(**changes: Any) -> DocumentEvidence:
    """一条引用证据；默认值都能过校验，用例只覆盖关心的那几个字段。"""

    values: dict[str, Any] = {
        "document_id": uuid4(),
        "knowledge_base_id": DEFAULT_NEWS_KNOWLEDGE_BASE_ID,
        "knowledge_base_name": "新闻",
        "title": "央行宣布降息",
        "content_hash": "a" * 64,
        "excerpt": "降息 25 个基点。",
        "kind": "match",
    }
    return DocumentEvidence(**(values | changes))


def project(messages: list[Any]) -> tuple[RunMessageRows, ...]:
    """走生产同一条链路：按运行分组 → 投影（完成态取回放算好的那一份）。

    输入是快照里的原始消息（含摘要消息）：生产那边也是把 ``summary_messages`` 的结果与分组
    一起交给投影，摘要行才不会漏写。
    """

    turns, _, _ = build_replay_turns(messages)
    return project_run_messages(
        group_messages_by_run(messages), turns=turns, summaries=summary_messages(messages)
    )


def summary(
    text: str, *, produced_by: UUID, boundary: UUID | None
) -> HumanMessage:
    """一条压缩留下的摘要伪提问，形状照 ``agent/middleware._build_new_messages`` 写出来的那份。

    ``produced_by`` 是产生它的那一次运行（会话历史表按它判断这一行属于哪一组），``boundary``
    是它记下的分界标记（被折掉那一段里最后一次提问的运行标识）。
    """

    return HumanMessage(
        content=f"Here is a summary of the conversation to date: {text}",
        additional_kwargs={
            "lc_source": "summarization",
            "produced_by_run_id": str(produced_by),
            "memory_boundary_run_id": None if boundary is None else str(boundary),
        },
    )


def summaries_of(rows: list[AgentThreadMessageRecord]) -> list[AgentThreadMessageRecord]:
    """表里那些摘要行——「表里只有一行摘要」这条断言反复要取。"""

    return [row for row in rows if row.role == "summary"]


def crashed_run_messages(run_id: UUID, *, tool_text: str) -> list[Any]:
    """一次「被强杀」运行的原始消息：提问、一次工具调用与结果、一段回答。

    模拟的场面是：这次运行的收尾从未执行，所以它的消息只留在 checkpoint 里、表里没有。``tool_text``
    就是快照里那份工具正文——被后续运行触发清理换成占位文字之后，它就是含占位文字的那一份。
    """

    return [
        question("第二问", run_id=run_id),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "search_documents", "args": {"query": "降息"}, "id": "call-crashed"}
            ],
        ),
        ToolMessage(content=tool_text, tool_call_id="call-crashed", name="search_documents"),
        answered(run_id, "第二答。"),
    ]


def test_a_run_becomes_question_answer_and_every_tool_row() -> None:
    """一轮里的提问、回答、工具调用、工具结果各占一行，顺序与消息在快照里的先后一致。"""

    run_id = uuid4()
    rows = project(
        [
            question("央行降息了吗", run_id=run_id),
            AIMessage(
                content="我查一下。",
                tool_calls=[
                    {"name": "search_documents", "args": {"query": "央行降息"}, "id": "call-search"}
                ],
            ),
            ToolMessage(
                content="[[E1]] 央行宣布降息。",
                tool_call_id="call-search",
                name="search_documents",
                artifact=artifact(run_id, evidence=(citation(),)),
            ),
            answered(run_id, "降息 25 个基点。"),
        ]
    )

    assert len(rows) == 1
    assert rows[0].run_id == run_id
    assert [row.role for row in rows[0].rows] == [
        "question",
        "answer",
        "tool_call",
        "tool_result",
        "answer",
    ]
    assert [row.text for row in rows[0].rows] == [
        "央行降息了吗",
        "我查一下。",
        None,
        "[[E1]] 央行宣布降息。",
        "降息 25 个基点。",
    ]
    tool_call, tool_result = rows[0].rows[2], rows[0].rows[3]
    assert (tool_call.tool_name, tool_call.tool_arguments, tool_call.tool_call_id) == (
        "search_documents",
        {"query": "央行降息"},
        "call-search",
    )
    assert (tool_result.tool_call_id, tool_result.failed) == ("call-search", False)


def test_an_empty_assistant_text_does_not_become_its_own_row() -> None:
    """正文为空串的助手消息不单独成行：它对回放没有贡献（回放本来就是拼这一轮的助手正文）。"""

    run_id = uuid4()
    rows = project(
        [
            question("央行降息了吗", run_id=run_id),
            AIMessage(
                content="",
                tool_calls=[{"name": "search_documents", "args": {"query": "降息"}, "id": "call-1"}],
            ),
            ToolMessage(content="检索结果", tool_call_id="call-1", name="search_documents"),
        ]
    )

    assert [row.role for row in rows[0].rows] == ["question", "tool_call", "tool_result"]


def test_every_parallel_tool_call_and_result_gets_its_own_row() -> None:
    """每个工具调用、每个工具结果各占一行，两者靠 ``tool_call_id`` 配对、不靠顺序。"""

    run_id = uuid4()
    rows = project(
        [
            question("两样都查一下", run_id=run_id),
            parallel_tool_call_message(
                [
                    ("call-a", "search_documents", {"query": "甲"}),
                    ("call-b", "read_document", {"document_id": str(uuid4())}),
                ]
            ),
            # 结果按相反的次序到达：并发调用的返回顺序不保证。
            ToolMessage(content="乙的结果", tool_call_id="call-b", name="read_document"),
            ToolMessage(content="甲的结果", tool_call_id="call-a", name="search_documents"),
        ]
    )

    calls = [row for row in rows[0].rows if row.role == "tool_call"]
    results = [row for row in rows[0].rows if row.role == "tool_result"]
    assert [(row.tool_call_id, row.tool_name) for row in calls] == [
        ("call-a", "search_documents"),
        ("call-b", "read_document"),
    ]
    assert [(row.tool_call_id, row.text) for row in results] == [
        ("call-b", "乙的结果"),
        ("call-a", "甲的结果"),
    ]


def test_a_failed_tool_result_is_marked_and_carries_no_evidence() -> None:
    """失败的工具结果如实标上 ``failed``，并且没有证据可存。"""

    run_id = uuid4()
    rows = project(
        [
            question("查一下", run_id=run_id),
            AIMessage(
                content="",
                tool_calls=[{"name": "search_documents", "args": {"query": "甲"}, "id": "call-1"}],
            ),
            ToolMessage(
                content="检索服务不可用。",
                tool_call_id="call-1",
                name="search_documents",
                status="error",
            ),
        ]
    )

    result = rows[0].rows[-1]
    assert (result.role, result.failed, result.evidence) == ("tool_result", True, None)


def test_the_question_row_keeps_the_scope_and_the_model_snapshot() -> None:
    """提问行的运行元数据透传当时的范围快照与模型快照，只额外补一个「是否完整作答」。"""

    run_id = uuid4()
    model = {"id": str(uuid4()), "display_name": "演示模型", "context_window": 32768}
    rows = project(
        [
            question("央行降息了吗", run_id=run_id, model=model),
            answered(run_id, "降息 25 个基点。"),
        ]
    )

    meta = rows[0].rows[0].run_meta
    assert meta is not None
    assert meta["scope"] == NEWS_SCOPE.model_dump(mode="json")
    assert meta["llm_model"] == model
    assert meta["completed"] is True


def test_the_completion_flag_comes_from_the_replay_decision() -> None:
    """「是否完整作答」取自回放算好的那一份，不另判一套。

    这里用一段被上游截断的答案：消息上盖着 ``completed: true``，但回放的 ``is_complete_answer``
    会把它判成未完成（``finish_reason=length``）。写入方照回放那份写成 ``False``——Done 事件与
    刷新后的回放各说一套，正是这条要挡的。
    """

    run_id = uuid4()
    truncated = answered(run_id, "半句", response_metadata={"finish_reason": "length"})
    rows = project([question("央行降息了吗", run_id=run_id), truncated])

    meta = rows[0].rows[0].run_meta
    assert meta is not None
    assert meta["completed"] is False
    # 另一半：回放自己也是这么判的——两边读的是同一份判定。
    turns, _, _ = build_replay_turns([question("央行降息了吗", run_id=run_id), truncated])
    assert turns[0].status == "incomplete"


def test_a_group_without_a_run_id_is_skipped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    """取不到运行标识的组不写、也不编占位 id，只留一条日志。"""

    messages = [HumanMessage(content="没有运行元数据的老提问"), AIMessage(content="答案。")]

    with caplog.at_level(logging.WARNING, logger="agent_lab.agent.thread_messages"):
        rows = project(messages)

    assert rows == ()
    assert [each for each in caplog.messages if "运行标识缺失" in each]


def test_the_evidence_column_drops_the_excerpt_and_truncated_keys() -> None:
    """证据列去掉 ``excerpt`` 与 ``truncated``，其余键原样保留。

    用**真实工具**产出 artifact（不与手搭的字典比对），走的是 ``search_documents`` 那条路径，
    所以「列里存的是什么形状」与生产同一份来源。
    """

    run_id = uuid4()
    context = AgentContext(run_id=run_id, scope=NEWS_SCOPE)
    tool = build_search_documents_tool(FakeSearchService([build_result(additional=1)]))
    result = run(invoke_tool_message(tool, {"query": "利率"}, context=context))
    rows = project(
        [
            question("利率", run_id=run_id),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_documents", "args": {"query": "利率"}, "id": "call-test"}
                ],
            ),
            result,
        ]
    )

    evidence = rows[0].rows[-1].evidence
    assert evidence is not None
    assert evidence["scope"] == NEWS_SCOPE.model_dump(mode="json")
    assert len(evidence["citations"]) == 2
    for item in evidence["citations"]:
        assert set(item) == {
            "citation_id",
            "document_id",
            "knowledge_base_id",
            "knowledge_base_name",
            "title",
            "content_hash",
            "source_name",
            "upload_filename",
            "url",
            "published_at",
            "kind",
        }
        assert item["content_hash"] == "a" * 64


# ---- 写入语义（真实 ORM 与事务跑在内存 SQLite 上）----

# JSONB 在 SQLite 上落到 JSON 列：这里只为夹具能建表，不模拟 PostgreSQL 的 JSONB 语义。
@compiles(JSONB, "sqlite")
def _jsonb_as_json(_type: Any, _compiler: Any, **_kwargs: Any) -> str:
    return "JSON"


@asynccontextmanager
async def history_database(directory: Path):
    """一个临时 SQLite 文件上的真实会话表与会话历史表。

    Args:
        directory: 放库文件的临时目录（用例传 pytest 的 ``tmp_path``）。

    Yields:
        绑定这个库的 ``async_sessionmaker``。

    Notes:
        表由 ORM 元数据建成，写入走真实的 ``AgentThreadService``——本文件的断言要的是「表里
        真的落了这些行」，替身或假 Session 给不了这个。

        落成**文件**而不是 ``:memory:``：内存库随它那条连接同生共死，而应用生命周期里有后台
        协程在读会话行（停止轮询、排空扫描），收尾时会把它们取消掉；被取消的那一次一旦让连接
        作废，整张内存库就没了，用例会随机变红（实测过）。文件库不吃这一套。
    """

    engine = create_async_engine(f"sqlite+aiosqlite:///{(directory / 'history.db').as_posix()}")
    metadata = MetaData()
    for model in (AgentThreadRecord, UserPreferenceRecord, AgentThreadMessageRecord):
        model.__table__.to_metadata(metadata)
    # ``agent_threads.scope`` 的库上默认值写成 ``'{...}'::jsonb``，SQLite 认不出这个转换语法。
    # 每一行都由 Service 显式写 scope，用不到库上的默认值，所以建表前去掉它。
    metadata.tables["agent_threads"].c.scope.server_default = None
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        yield sessions
    finally:
        await engine.dispose()


async def read_rows(sessions: Any, thread_id: UUID) -> list[AgentThreadMessageRecord]:
    """按会话读出全部行，顺序按读取路径唯一认的那个键（``seq``）。"""

    async with sessions() as session:
        rows = await session.scalars(
            select(AgentThreadMessageRecord)
            .where(AgentThreadMessageRecord.thread_id == thread_id)
            .order_by(AgentThreadMessageRecord.seq)
        )
        return list(rows)


def test_a_run_group_lands_in_the_table(tmp_path: Path) -> None:
    """一次收尾把这一组按顺序号落进表：行内容与顺序号都对得上。"""

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id, run_id = uuid4(), uuid4()
            group = project(
                [question("央行降息了吗", run_id=run_id), answered(run_id, "降息 25 个基点。")]
            )

            written = await service.record_run_messages(
                thread_id=thread_id, run_id=run_id, runs=group
            )

            assert written == 2
            rows = await read_rows(sessions, thread_id)
            assert [(row.role, row.seq, row.text, row.run_id) for row in rows] == [
                ("question", 0, "央行降息了吗", run_id),
                ("answer", 1, "降息 25 个基点。", run_id),
            ]
            assert rows[0].run_meta is not None
            assert rows[0].run_meta["completed"] is True
            assert rows[0].created_at is not None
            # 别的角色的列在这两行上是空的：不是「填了个空串」，是没这一回事。
            assert (rows[0].tool_name, rows[0].failed, rows[0].evidence) == (None, None, None)

    run(verify())


def test_writing_the_same_run_twice_keeps_one_copy(tmp_path: Path) -> None:
    """同一次收尾执行两遍，表里那些行只有一份。"""

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id, run_id = uuid4(), uuid4()
            group = project(
                [question("央行降息了吗", run_id=run_id), answered(run_id, "降息 25 个基点。")]
            )

            await service.record_run_messages(thread_id=thread_id, run_id=run_id, runs=group)
            await service.record_run_messages(thread_id=thread_id, run_id=run_id, runs=group)

            rows = await read_rows(sessions, thread_id)
            assert [(row.role, row.seq, row.text) for row in rows] == [
                ("question", 0, "央行降息了吗"),
                ("answer", 1, "降息 25 个基点。"),
            ]

    run(verify())


def test_a_repeat_write_keeps_the_sequence_numbers_of_the_first_write(tmp_path: Path) -> None:
    """重复收尾时沿用第一次写入的那一组顺序号——否则较早的一轮会排到更晚的轮次之后。

    这是「先删后插」唯一危险的后果：不把原顺序号读回来，重写的那一组会从会话最大顺序号之后重排，
    回看顺序就变了。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id, first_run, second_run = uuid4(), uuid4(), uuid4()
            first = project(
                [question("第一问", run_id=first_run), answered(first_run, "第一答。")]
            )
            second = project(
                [question("第二问", run_id=second_run), answered(second_run, "第二答。")]
            )

            await service.record_run_messages(
                thread_id=thread_id, run_id=first_run, runs=first
            )
            await service.record_run_messages(
                thread_id=thread_id, run_id=second_run, runs=second
            )
            before = [(row.run_id, row.seq, row.text) for row in await read_rows(sessions, thread_id)]

            # 第一次运行那一次收尾又被执行了一遍（接手后收尾、或收尾被重跑）。
            await service.record_run_messages(
                thread_id=thread_id, run_id=first_run, runs=(*first, *second)
            )

            after = [(row.run_id, row.seq, row.text) for row in await read_rows(sessions, thread_id)]
            assert after == before
            assert [seq for run_id, seq, _ in after if run_id == first_run] == [0, 1]
            assert [seq for run_id, seq, _ in after if run_id == second_run] == [2, 3]

    run(verify())


def test_a_group_already_in_the_table_is_left_untouched(tmp_path: Path) -> None:
    """表里已经有的组一字不动：快照里那份可能已经被压成占位文字。

    这条挡的是「把快照里能组出来的轮全部重写」那种实现——它会把用户看到的原文换成占位文字，
    与「本表写完之后永不清理」正好相反。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id, earlier_run, current_run = uuid4(), uuid4(), uuid4()
            stored_original = project(
                [question("第一问", run_id=earlier_run), answered(earlier_run, "原文回答。")]
            )
            from_current_snapshot = project(
                [question("第一问", run_id=earlier_run), answered(earlier_run, "占位文字。")]
            )
            current = project(
                [question("第二问", run_id=current_run), answered(current_run, "第二答。")]
            )

            await service.record_run_messages(
                thread_id=thread_id, run_id=earlier_run, runs=stored_original
            )
            await service.record_run_messages(
                thread_id=thread_id,
                run_id=current_run,
                runs=(*from_current_snapshot, *current),
            )

            rows = await read_rows(sessions, thread_id)
            assert [row.text for row in rows if row.run_id == earlier_run] == [
                "第一问",
                "原文回答。",
            ]
            assert [row.text for row in rows if row.run_id == current_run] == ["第二问", "第二答。"]

    run(verify())


def test_a_group_the_table_never_got_is_filled_in_by_the_next_finish(tmp_path: Path) -> None:
    """被强杀那一轮（快照里有、表里没有）由下一次收尾补进表，已经落表的那一组一字不动。

    现实来源：运行 B 被强杀，它的收尾从未执行，那一组只留在 checkpoint 里；用户下次进来又问 C，
    C 收尾时快照里有 A、B、C，而 B 不在表里——B 就是这么补上的（见 spec 0003 的「写入方只写表里
    还缺的那些运行组」）。反过来，如果用户再也不回来，就没有任何收尾会经过这里，B 那一组不落表
    ——那是仓库既有的「没有收尾机会」边界：被强杀的那一轮本来就靠用户重新提问。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id, first_run, killed_run, current_run = uuid4(), uuid4(), uuid4(), uuid4()
            first = project(
                [question("第一问", run_id=first_run), answered(first_run, "第一答。")]
            )
            killed = project(
                crashed_run_messages(killed_run, tool_text="被强杀那一轮的工具结果原文。")
            )
            current = project(
                [question("第三问", run_id=current_run), answered(current_run, "第三答。")]
            )

            await service.record_run_messages(
                thread_id=thread_id, run_id=first_run, runs=first
            )
            stored_first = await read_rows(sessions, thread_id)
            # 1、B 的收尾从未执行、也没人替它写：那一刻表里只有 A 那一组。
            assert [row.run_id for row in stored_first] == [first_run, first_run]

            # 2、C 收尾：快照里 A、B、C 都在，B 是「快照里有、表里没有」的那一组。
            written = await service.record_run_messages(
                thread_id=thread_id, run_id=current_run, runs=(*first, *killed, *current)
            )

            rows = await read_rows(sessions, thread_id)
            # 3、只写了缺的那两组，B 与 C 的行都在，B 排在 C 前面（顺序跟着快照里的先后）。
            assert written == len(killed[0].rows) + len(current[0].rows)
            assert [row.run_id for row in rows] == [
                first_run,
                first_run,
                killed_run,
                killed_run,
                killed_run,
                killed_run,
                current_run,
                current_run,
            ]
            assert [row.role for row in rows if row.run_id == killed_run] == [
                "question",
                "tool_call",
                "tool_result",
                "answer",
            ]
            assert [row.text for row in rows if row.run_id == killed_run] == [
                "第二问",
                None,
                "被强杀那一轮的工具结果原文。",
                "第二答。",
            ]
            assert [row.text for row in rows if row.run_id == current_run] == [
                "第三问",
                "第三答。",
            ]
            # 4、A 那一组一字不动：行 id、顺序号、正文全是第一次写入的那一份。
            assert [(row.id, row.seq, row.text) for row in rows if row.run_id == first_run] == [
                (row.id, row.seq, row.text) for row in stored_first
            ]
            # 5、补上来的两组接着会话里已有的最大顺序号往后排：整条时间线仍是单个递增序列。
            assert [row.seq for row in rows] == list(range(8))

    run(verify())


def test_a_backfilled_group_is_written_exactly_as_the_snapshot_has_it(tmp_path: Path) -> None:
    """快照里工具正文是什么就写什么：含占位文字的那一份照实补进表，不会被「修好」成原文。

    被强杀那一次运行的旧工具正文，会在它之后某次运行触发清理时被换成占位文字（见
    ``agent/limits.PRUNED_TOOL_RESULT_PLACEHOLDER``）；而补写读的就是同一份 checkpoint，所以表里
    落的就是占位。这不是 bug，是「那一次运行没有收尾机会」的代价，所以这里直接构造那份含占位文字
    的快照来验证，不等真实清理发生。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id, first_run, killed_run, current_run = uuid4(), uuid4(), uuid4(), uuid4()
            # 清理之前交给模型的正文是「正文开头。+ 中段原文。×50 + 正文结尾。」；清理之后留在
            # checkpoint 里的就是下面这一份（中段换成占位文字）。
            pruned_tool_text = "正文开头。" + PRUNED_TOOL_RESULT_PLACEHOLDER + "正文结尾。"
            first = project(
                [question("第一问", run_id=first_run), answered(first_run, "第一答。")]
            )
            killed = project(crashed_run_messages(killed_run, tool_text=pruned_tool_text))
            current = project(
                [question("第三问", run_id=current_run), answered(current_run, "第三答。")]
            )

            await service.record_run_messages(
                thread_id=thread_id, run_id=first_run, runs=first
            )
            await service.record_run_messages(
                thread_id=thread_id, run_id=current_run, runs=(*first, *killed, *current)
            )

            rows = await read_rows(sessions, thread_id)
            # 那一组照实落表：工具结果那一行就是快照里那份占位版正文。
            assert [row.text for row in rows if row.run_id == killed_run] == [
                "第二问",
                None,
                pruned_tool_text,
                "第二答。",
            ]
            tool_result = next(
                row for row in rows if row.run_id == killed_run and row.role == "tool_result"
            )
            assert tool_result.text == pruned_tool_text

    run(verify())


def test_the_sequence_numbers_continue_after_the_last_row_of_the_session(tmp_path: Path) -> None:
    """新的一组从该会话已有的最大顺序号加一开始。"""

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id, first_run, second_run = uuid4(), uuid4(), uuid4()
            first = project(
                [
                    question("第一问", run_id=first_run),
                    answered(first_run, "第一答。"),
                    answered(first_run, "补充一句。"),
                ]
            )
            second = project(
                [question("第二问", run_id=second_run), answered(second_run, "第二答。")]
            )

            await service.record_run_messages(
                thread_id=thread_id, run_id=first_run, runs=first
            )
            await service.record_run_messages(
                thread_id=thread_id, run_id=second_run, runs=second
            )

            rows = await read_rows(sessions, thread_id)
            assert [row.seq for row in rows] == [0, 1, 2, 3, 4]

    run(verify())


def test_writing_one_session_leaves_another_session_alone(tmp_path: Path) -> None:
    """按（会话，运行）整组替换只动自己这一会话的行。"""

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            run_id = uuid4()
            group = project([question("同一个问题", run_id=run_id), answered(run_id, "答案。")])
            mine, other = uuid4(), uuid4()

            await service.record_run_messages(thread_id=mine, run_id=run_id, runs=group)
            await service.record_run_messages(thread_id=other, run_id=run_id, runs=group)
            other_before = [row.id for row in await read_rows(sessions, other)]

            await service.record_run_messages(thread_id=mine, run_id=run_id, runs=group)

            assert [row.id for row in await read_rows(sessions, other)] == other_before
            assert len(await read_rows(sessions, mine)) == 2

    run(verify())


# ---- 摘要行（压缩留下的那一行）----


# 真跑一次压缩用的那段历史：长到足以触发压缩（触发线是上下文窗口的比例），每条提问都带着
# 自己的运行标识。文本重复是因为要凑出真实的 token 量，不是为了好看。
COMPRESSED_HISTORY_TEXT = "这一段旧对话要被折进摘要里。" * 8
CURRENT_QUESTION_TEXT = "本次提问"


def test_a_compression_leaves_exactly_one_summary_row_however_many_runs_follow(
    tmp_path: Path,
) -> None:
    """摘要行只有一行：之后每一次运行都不会把它再写一遍。

    摘要消息一次压缩之后就留在状态头部、不会自己消失，所以之后每一次运行的收尾都能在快照里
    「找到」它。判据必须是「产生它的运行 == 这一组的运行」，不是「快照里有摘要」——照后者实现，
    第二次运行起就会多写一行，同一会话里累积一串。这里跑第一次、第二次、第三次运行，每次都数
    一遍表里 ``role="summary"`` 的行。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id, first_run, second_run, third_run = uuid4(), uuid4(), uuid4(), uuid4()
            boundary = uuid4()
            snapshot: list[Any] = [
                summary("更早那段聊过的旧背景", produced_by=first_run, boundary=boundary),
                question("第一问", run_id=first_run),
                answered(first_run, "第一答。"),
            ]

            for index, run_id in enumerate((first_run, second_run, third_run)):
                if index > 0:
                    snapshot = [
                        *snapshot,
                        question(f"第{index + 1}问", run_id=run_id),
                        answered(run_id, f"第{index + 1}答。"),
                    ]
                await service.record_run_messages(
                    thread_id=thread_id, run_id=run_id, runs=project(snapshot)
                )

                rows = await read_rows(sessions, thread_id)
                # 1、摘要行始终只有一行：它属于第一次运行那一组，后续运行既不重写它、也不给自己
                #    再添一行；分界标记与它是哪次运行产生的都保持第一次写下的值。
                assert len(summaries_of(rows)) == 1, "多写了一次摘要行"
                stored = summaries_of(rows)[0]
                assert (stored.run_id, stored.memory_boundary_run_id) == (first_run, boundary)
                # 2、其余的行照旧按运行往后长：第一次 3 行（摘要 + 问 + 答），之后每次各 2 行。
                assert len(rows) == 3 + 2 * index
                assert [row.seq for row in rows] == list(range(len(rows)))

    run(verify())


def test_a_summary_row_of_a_backfilled_group_is_written_with_it(tmp_path: Path) -> None:
    """补写那一组也写它自己的摘要行：判据是「产生它的运行 == 该组运行」。

    场面：产生摘要的那次运行（A）收尾从未执行（被强杀），它的组与摘要行都不在表里；后来的运行
    （B）收尾时把 A 的组补齐，摘要行因此跟着落进 A 那一组，并排在 A 的最前面——它属于 A，不
    属于正在收尾的 B。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id, killed_run, current_run = uuid4(), uuid4(), uuid4()
            boundary = uuid4()
            snapshot = [
                summary("被强杀那次压缩留下的摘要", produced_by=killed_run, boundary=boundary),
                question("第二问", run_id=killed_run),
                answered(killed_run, "第二答。"),
                question("第三问", run_id=current_run),
                answered(current_run, "第三答。"),
            ]

            written = await service.record_run_messages(
                thread_id=thread_id, run_id=current_run, runs=project(snapshot)
            )

            rows = await read_rows(sessions, thread_id)
            assert written == 5
            # 摘要行排在 A 那一组的最前面（提问行之前），而不是当前运行那一组。
            assert [(row.run_id, row.role, row.seq) for row in rows] == [
                (killed_run, "summary", 0),
                (killed_run, "question", 1),
                (killed_run, "answer", 2),
                (current_run, "question", 3),
                (current_run, "answer", 4),
            ]
            stored = summaries_of(rows)[0]
            assert stored.memory_boundary_run_id == boundary
            assert stored.text == (
                "Here is a summary of the conversation to date: 被强杀那次压缩留下的摘要"
            )

    run(verify())


def test_a_real_compression_lands_one_summary_row_with_the_boundary(tmp_path: Path) -> None:
    """真的压缩过一次之后：表里多出一行摘要，分界标记指向被折掉那一段里最后一次提问。

    走真实的中间件流水线（离线图 + 内存 checkpointer）：摘要消息上的两个字段只在压缩那一刻
    算得出来，手搭的消息只能证明「贴上去的字段被搬进了表」，证明不了「贴上去的是什么」。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id = uuid4()
            history_runs = [uuid4() for _ in range(8)]
            history = [
                message
                for index, run_id in enumerate(history_runs)
                for message in (
                    question(f"旧问 {index}：{COMPRESSED_HISTORY_TEXT}", run_id=run_id),
                    answered(run_id, f"旧答 {index}：{COMPRESSED_HISTORY_TEXT}"),
                )
            ]
            # 窗口取「刚好触发」的一半：驱动一次真实运行会往请求里多塞一条当前日期，行数对不上
            # 就用它压回来（见 tests/test_agent_helpers 的 ``window_that_triggers``）。
            window = (
                window_that_triggers([*history, HumanMessage(content=CURRENT_QUESTION_TEXT)])
                // 2
            )
            context = AgentContext(
                llm_model=ResolvedLlmModel(
                    id=uuid4(), display_name="演示模型", context_window=window
                )
            )
            model = ScriptedChatModel(
                responses=[
                    AIMessage(content="折掉那一段的摘要。"),
                    AIMessage(content="本次回答。"),
                ]
            )
            graph = build_offline_graph(model)
            config = {"configurable": {"thread_id": str(thread_id)}}
            await graph.aupdate_state(config, {"messages": history})
            async for _ in stream_agent_events(
                graph,
                message=CURRENT_QUESTION_TEXT,
                thread_id=thread_id,
                context=context,
                langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
            ):
                pass
            messages = list((await graph.aget_state(config)).values["messages"])

            # 前提：压缩真的发生，摘要落在状态头部。
            assert messages[0].additional_kwargs.get("lc_source") == "summarization"
            current_index = next(
                index
                for index, message in enumerate(messages)
                if getattr(message, "text", None) == CURRENT_QUESTION_TEXT
            )
            preserved = messages[1:current_index]
            assert [message.text for message in preserved] == [
                message.text for message in history[len(history) - len(preserved) :]
            ], "保留段必须是历史的后缀，否则下面算不出被折掉的是哪一段"
            dropped_questions = [
                message
                for message in history[: len(history) - len(preserved)]
                if isinstance(message, HumanMessage)
            ]
            assert len(dropped_questions) >= 2, "段里要有多个提问，「取最后一次」与「取第一次」才分得开"

            await service.record_run_messages(
                thread_id=thread_id, run_id=context.run_id, runs=project(messages)
            )
            rows = await read_rows(sessions, thread_id)

            assert len(summaries_of(rows)) == 1
            stored = summaries_of(rows)[0]
            # 1、这一行属于产生它的那一次运行。
            assert stored.run_id == context.run_id
            # 2、分界标记取的是被折掉那一段里**最后一次**提问的运行标识，不是第一次、也不是本次。
            assert str(stored.memory_boundary_run_id) == dropped_questions[-1].additional_kwargs[
                "agent_run"
            ]["run_id"]
            assert str(stored.memory_boundary_run_id) != dropped_questions[0].additional_kwargs[
                "agent_run"
            ]["run_id"]
            assert stored.memory_boundary_run_id != context.run_id
            # 3、正文存摘要消息的原文（含上游加的英文前缀）。
            assert stored.text == messages[0].content
            assert "折掉那一段的摘要。" in (stored.text or "")

    run(verify())


def test_a_second_compression_without_a_question_keeps_the_previous_boundary(
    tmp_path: Path,
) -> None:
    """第二次压缩且被折掉那一段里一条提问都没有：写进表的那一行沿用上一条摘要的分界标记。

    状态头部已经有一条更早的摘要（产生它的是更早的某一次运行），而保留段从摘要之后起——这次
    被折掉的段里只有那条摘要、它后面取过的工具结果与回答，一条提问都没有。「没有提问就沿用
    上一条」的判定在中间件里（见 ``tests/test_agent_middleware.py``），这条用例钉的是
    **沿用下来的那个值真的经收尾进了表**：新摘要落在产生它的那一组，旧摘要不落（它的产生运行
    不属于任何一组）。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            thread_id = uuid4()
            previous_boundary = uuid4()
            previous_summary = summary(
                "更早那段聊过的旧背景", produced_by=uuid4(), boundary=previous_boundary
            )
            seed = [
                previous_summary,
                parallel_tool_call_message(
                    [("call-old", "search_documents", {"query": "旧查询"})]
                ),
                ToolMessage(
                    content=f"上一轮取到的资料：{COMPRESSED_HISTORY_TEXT}",
                    tool_call_id="call-old",
                    name="search_documents",
                ),
                AIMessage(content=f"上一答：{COMPRESSED_HISTORY_TEXT}"),
            ]
            window = (
                window_that_triggers([*seed, HumanMessage(content=CURRENT_QUESTION_TEXT)])
                // 8
            )
            context = AgentContext(
                llm_model=ResolvedLlmModel(
                    id=uuid4(), display_name="演示模型", context_window=window
                )
            )
            model = ScriptedChatModel(
                responses=[
                    AIMessage(content="第二次压缩的摘要。"),
                    AIMessage(content="本次回答。"),
                ]
            )
            graph = build_offline_graph(model)
            config = {"configurable": {"thread_id": str(thread_id)}}
            await graph.aupdate_state(config, {"messages": seed})
            async for _ in stream_agent_events(
                graph,
                message=CURRENT_QUESTION_TEXT,
                thread_id=thread_id,
                context=context,
                langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
            ):
                pass
            messages = list((await graph.aget_state(config)).values["messages"])

            # 前提：这真的是一次新的压缩（新的摘要在头部，产生它的是本次运行）。
            assert messages[0] is not previous_summary
            assert messages[0].additional_kwargs.get("produced_by_run_id") == str(context.run_id)
            # 前提：被折掉的那一段以旧摘要开头，而且段里一条提问都没有。
            questions = [
                message
                for message in messages[1:]
                if isinstance(message, HumanMessage)
                and message.additional_kwargs.get("lc_source") != "summarization"
            ]
            assert [message.text for message in questions] == [CURRENT_QUESTION_TEXT]

            await service.record_run_messages(
                thread_id=thread_id, run_id=context.run_id, runs=project(messages)
            )
            rows = await read_rows(sessions, thread_id)

            # 1、表里只有新摘要那一行：沿用下来的旧摘要在头部，但它的产生运行不是本次，不落表。
            assert len(summaries_of(rows)) == 1
            stored = summaries_of(rows)[0]
            assert (stored.run_id, stored.memory_boundary_run_id) == (
                context.run_id,
                previous_boundary,
            )
            assert stored.memory_boundary_run_id is not None, "沿用是必须的：记空等于白记"
            assert stored.memory_boundary_run_id != context.run_id
            # 2、正文是新摘要的原文，不是沿用下来的那一条。
            assert stored.text == messages[0].content
            assert "第二次压缩的摘要。" in (stored.text or "")
            assert all(row.text != previous_summary.content for row in rows)

    run(verify())


# ---- 应用层收尾真的会写（离线图 + 内存 checkpointer + 真实数据库的写入路径）----


async def ask_fully(client: httpx.AsyncClient, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """正常读完一次 SSE 请求，返回解析后的事件。"""

    async with client.stream("POST", "/agent/chat", json=payload) as response:
        assert response.status_code == 200
        body = "".join([chunk async for chunk in response.aiter_text()])
    return [
        json.loads(frame.removeprefix("data: "))
        for frame in body.split("\n\n")
        if frame.startswith("data: ")
    ]


def test_a_finished_run_lands_in_the_history_table(tmp_path: Path) -> None:
    """正常答完的一轮：提问、回答、工具调用、工具结果都在表里，引用证据也在。"""

    async def scenario() -> tuple[list[dict[str, Any]], list[AgentThreadMessageRecord]]:
        async with history_database(tmp_path) as sessions:
            model = ScriptedChatModel(
                responses=[
                    AIMessage(
                        content="我查一下。",
                        tool_calls=[
                            {
                                "name": "search_documents",
                                "args": {"query": "央行降息"},
                                "id": "call-search",
                            }
                        ],
                    ),
                    AIMessage(content="降息 25 个基点。"),
                ]
            )
            # 会话归属 Service 换成真实实现：写表由它落进这套内存 SQLite。
            app, search = create_agent_app(
                model, threads=AgentThreadService(sessions), checkpointer=InMemorySaver()
            )
            # 让检索工具真的命中一篇文档：工具结果行才有引用证据可投影。
            search.service.search_documents = AsyncMock(
                return_value=[build_result(additional=1)]
            )

            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    events = await ask_fully(client, {"message": "央行降息了吗"})
                    assert events[-1]["event"] == "done"
                    thread_id = UUID(events[0]["thread_id"])
            return events, await read_rows(sessions, thread_id)

    events, rows = run(scenario())

    run_id = UUID(events[0]["run_id"])
    assert [row.role for row in rows] == ["question", "answer", "tool_call", "tool_result", "answer"]
    assert {row.run_id for row in rows} == {run_id}
    # 1、提问行带着当时的范围快照、完成态与**当轮模型快照**（范围与 run_started 事件里报给前端的
    #    是同一份；模型那一份由「会话里选模型」那条路冻结进来，见 spec 0002）。
    assert rows[0].text == "央行降息了吗"
    assert rows[0].run_meta is not None
    assert rows[0].run_meta["scope"] == events[0]["scope"]
    assert rows[0].run_meta["completed"] is True
    frozen_model = rows[0].run_meta["llm_model"]
    # 快照必须自足：展示名与上下文窗口都在里面，回看与接手都不回查目录。
    assert frozen_model["display_name"] == "演示模型"
    assert frozen_model["context_window"] == 32768
    assert UUID(frozen_model["id"]) == DEFAULT_OFFLINE_MODEL_ID
    # 2、工具调用与工具结果各一行，靠 id 配对，参数与失败标记如实落表。
    tool_call, tool_result = rows[2], rows[3]
    assert (tool_call.tool_name, tool_call.tool_arguments, tool_call.tool_call_id) == (
        "search_documents",
        {"query": "央行降息"},
        "call-search",
    )
    assert (tool_result.tool_call_id, tool_result.failed) == ("call-search", False)
    assert "第 0 段正文内容。" in (tool_result.text or "")
    # 3、引用证据在列里，且不含正文片段（正文由本表的工具结果行承载）。
    evidence = tool_result.evidence
    assert evidence is not None
    assert evidence["scope"]["mode"] == "all"
    assert [item["content_hash"] for item in evidence["citations"]] == ["a" * 64, "a" * 64]
    assert all("excerpt" not in item and "truncated" not in item for item in evidence["citations"])
    # 4、最后一行是回答正文。
    assert rows[4].text == "降息 25 个基点。"


def test_the_done_event_and_the_replay_agree_on_the_same_session(tmp_path: Path) -> None:
    """同一个会话：终态事件（读 checkpointer）与刷新后的回放（读业务表）给出同一份答案、状态与引用。

    两条路径刻意各读各的源——终态事件发出时这一轮可能还没落表——所以「结论一致」只能由共用的
    纯函数保证（分轮、完成态判定、引用核验），这条用例就是那份保证的验收面。引用那里顺带钉住
    「引用项不含正文片段」：那一轮的资料原文由工具结果行承载，引用只给身份与版本。
    """

    class CitingModel(ScriptedChatModel):
        """先调检索工具，再照拄它给出的引用标识作答。

        引用标识由应用随机生成，写死的脚本抄不到，只能从工具返回的 artifact 里读出来。
        """

        def _generate(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
            retrieved = [message for message in messages if isinstance(message, ToolMessage)]
            if retrieved:
                ids = [message.artifact["evidence"][0]["citation_id"] for message in retrieved]
                self.responses = [AIMessage(content=f"备份保留 7 天。[[{ids[0]}]]")]
                self.i = 0
            return super()._generate(messages, *args, **kwargs)

    async def scenario() -> tuple[list[dict[str, Any]], dict[str, Any], list[AgentThreadMessageRecord]]:
        async with history_database(tmp_path) as sessions:
            model = CitingModel(
                responses=[tool_call_message("search_documents", {"query": "备份"})]
            )
            app, search = create_agent_app(
                model, threads=AgentThreadService(sessions), checkpointer=InMemorySaver()
            )
            # 让检索工具真的命中一篇文档：这一轮才有引用可比。
            search.service.search_documents = AsyncMock(
                return_value=[build_result(additional=1)]
            )

            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    events = await ask_fully(client, {"message": "备份保留多久"})
                    thread_id = UUID(events[0]["thread_id"])
                    replay = (await client.get(f"/agent/threads/{thread_id}/messages")).json()
            return events, replay, await read_rows(sessions, thread_id)

    events, replay, rows = run(scenario())

    done = events[-1]
    turn = replay["turns"][-1]
    assert done["event"] == "done"
    # 1、答案、完成状态、引用逐项相同（两份都是同一套纯函数算出来的）。
    assert turn["answer"] == done["answer"]
    assert turn["status"] == done["status"] == "completed"
    assert turn["citations"] == done["citations"]
    assert turn["invalid_citations"] == done["invalid_citations"]
    # 2、引用真有一条（两个空列表比相等什么也证明不了），而且它只有身份与版本，没有正文
    #    片段字段——那一轮的资料原文由工具结果行承载。
    assert turn["citations"], "这一轮没有引用，相等断言证明不了引用一致"
    assert [item["content_hash"] for item in turn["citations"]] == ["a" * 64]
    assert all(
        "excerpt" not in item and "truncated" not in item for item in turn["citations"]
    )
    # 3、这一轮确实在表里（两者不是「都恰好为空」；提问那一轮的工具调用是空正文，所以只有
    #    一条回答行）。
    assert [row.role for row in rows] == [
        "question",
        "tool_call",
        "tool_result",
        "answer",
    ]


def test_a_failed_run_lands_in_the_history_table(tmp_path: Path) -> None:
    """上游失败中断的那一轮也写：提问与已经推出去的那段正文都在，完成态是未答完。"""

    async def scenario() -> tuple[list[dict[str, Any]], list[AgentThreadMessageRecord]]:
        async with history_database(tmp_path) as sessions:
            model = DyingStreamModel(messages=iter([]))
            app, _search = create_agent_app(
                model, threads=AgentThreadService(sessions), checkpointer=InMemorySaver()
            )

            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    events = await ask_fully(client, {"message": "央行降息了吗"})
                    thread_id = UUID(events[0]["thread_id"])
            return events, await read_rows(sessions, thread_id)

    events, rows = run(scenario())

    assert events[-1]["event"] == "error"
    assert [row.role for row in rows] == ["question", "answer"]
    # 补写（已推出未落库的那段）发生在写表之前，所以它也在表里。
    assert rows[1].text == "降息 25 个"
    assert rows[1].run_id == UUID(events[0]["run_id"])
    assert rows[0].run_meta is not None
    assert rows[0].run_meta["completed"] is False


class ObservingHistoryService(AgentThreadService):
    """写表那一刻顺手把会话行上的在途运行 id 抄下来。

    用来钉「写表在释放会话位之前」这个先后：那一瞬间会话行上还挂着本次运行的占位。反过来
    （先释放再写）这里会读到 ``None``。刻意继承真实实现，因为它要读的就是真实库里的那一行。
    """

    def __init__(self, session_factory: Any) -> None:
        super().__init__(session_factory)
        self.active_run_id_at_write: UUID | None = None

    async def record_run_messages(self, **kwargs: Any) -> int:
        async with self._session_factory() as session:
            row = await session.get(AgentThreadRecord, kwargs["thread_id"])
            self.active_run_id_at_write = None if row is None else row.active_run_id
        return await super().record_run_messages(**kwargs)


def test_the_history_is_written_before_the_session_slot_is_released(tmp_path: Path) -> None:
    """写表在释放会话位之前：那一瞬间会话行上还挂着本次运行的占位。

    顺序反过来的话，中间会多出一段「会话已解锁、历史还没落表」的窗口：用户这时追问会被放行，
    而刷新页面看到的却是「正在生成」加一轮查不到的旧历史。终态事件在释放**之后**发是既有保证，
    这一条是它的另一半。
    """

    async def scenario() -> tuple[str, UUID | None]:
        async with history_database(tmp_path) as sessions:
            model = ScriptedChatModel(responses=[AIMessage(content="降息 25 个基点。")])
            threads = ObservingHistoryService(sessions)
            app, _search = create_agent_app(
                model, threads=threads, checkpointer=InMemorySaver()
            )

            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    events = await ask_fully(client, {"message": "央行降息了吗"})
            return events[0]["run_id"], threads.active_run_id_at_write

    run_id, active_run_id_at_write = run(scenario())

    assert active_run_id_at_write == UUID(run_id)


class EndlessStreamModel(StreamingChatModel):
    """先吐一小段字、然后长时间静默的假模型：用来验证「用户按停止」那条收尾。

    驱动者**每个循环都看一次停止标志**（`AgentRunRegistry._drive` 的循环体开头），所以「流必须
    随时静默」不是停止生效的前提。这个假模型仍然保留有限分片与末尾静默，理由是让用例在任何
    实现下都会结束：分片数有界保证实现真的退化时它仍旧会跑到模型自己停下（然后走到另一条分支
    上、断言变红），而不是永远转下去；末尾那段静默则让「停止生效」与「停止没生效」分成两条
    不同的分支——前者几十毫秒内结束，后者要等静默结束、模型再被调用一次才变红，两者的现象
    分得开。
    """

    chunks: int = 20

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        """产出一段字，然后停下不动，等停止请求进来。"""

        for _ in range(self.chunks):
            await asyncio.sleep(0.01)
            yield ChatGenerationChunk(message=AIMessageChunk(content="字"))
        # 收尾前的一段静默：它在任何实现下都能让「模型自己停下」与「用户按了停止」这两条路
        # 分得开（后者不会等这段静默）。
        await asyncio.sleep(5.0)


def test_a_stopped_run_lands_in_the_history_table(tmp_path: Path) -> None:
    """用户按停止的那一轮也写：已经推出去的文字留在表里，完成态是未答完。"""

    async def scenario() -> tuple[list[dict[str, Any]], list[AgentThreadMessageRecord]]:
        async with history_database(tmp_path) as sessions:
            model = EndlessStreamModel(messages=iter([]))
            app, _search = create_agent_app(
                model, threads=AgentThreadService(sessions), checkpointer=InMemorySaver()
            )

            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    task, queue = await open_chat_stream(
                        app, path="/agent/chat", payload={"message": "央行降息了吗"}
                    )
                    events: list[dict[str, Any]] = []
                    thread_id: str | None = None
                    stop_status: int | None = None
                    while True:
                        event = await queue.get()
                        if event is None:
                            break
                        events.append(event)
                        if event["event"] == "run_started":
                            thread_id = event["thread_id"]
                            run_id = event["run_id"]
                        # 看到第一个字就请求停止：此刻这次运行确实在途。
                        if event["event"] == "token" and stop_status is None:
                            assert thread_id is not None
                            stop_status = (
                                await client.post(
                                    "/agent/stop",
                                    json={"thread_id": thread_id, "run_id": run_id},
                                )
                            ).status_code
                    await task
                    assert stop_status == 200
                    assert thread_id is not None
            return events, await read_rows(sessions, UUID(thread_id))

    events, rows = run(asyncio.wait_for(scenario(), timeout=30.0))

    assert events[-1]["event"] == "done"
    assert events[-1]["status"] == "incomplete"
    tokens = "".join(event["text"] for event in events if event["event"] == "token")
    assert tokens
    assert [row.role for row in rows] == ["question", "answer"]
    assert rows[1].text == tokens
    assert rows[0].run_meta is not None
    assert rows[0].run_meta["completed"] is False


class BrokenHistoryService(AgentThreadService):
    """写会话历史必失败的会话 Service：其余方法照旧，用来验证失败只记日志。

    刻意继承真实实现而不是另写一个替身：会话占位、停止、释放都要正常发生，只有写表这一步失败。
    """

    async def record_run_messages(self, **_kwargs: Any) -> int:
        """模拟「同一个事务写不进去」。"""

        raise RuntimeError("库不可用")


def test_a_write_failure_only_logs_and_the_run_still_finishes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """写表失败：只记日志，会话位照旧释放、终态事件照旧发出，表里什么都没有。"""

    async def scenario() -> tuple[list[dict[str, Any]], Any, list[AgentThreadMessageRecord]]:
        async with history_database(tmp_path) as sessions:
            model = ScriptedChatModel(responses=[AIMessage(content="降息 25 个基点。")])
            threads = BrokenHistoryService(sessions)
            app, _search = create_agent_app(
                model, threads=threads, checkpointer=InMemorySaver()
            )

            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    events = await ask_fully(client, {"message": "央行降息了吗"})
                    thread_id = UUID(events[0]["thread_id"])
                    # 会话位已经释放：用户能立刻追问（发第二次提问拿 200 而不是 409）。
                    follow_up = await ask_fully(
                        client, {"message": "那房贷呢", "thread_id": str(thread_id)}
                    )
            async with sessions() as session:
                record = await session.get(AgentThreadRecord, thread_id)
            return events, (
                record.active_run_id,
                follow_up[-1]["event"],
                follow_up[0]["run_id"],
            ), await read_rows(sessions, thread_id)

    with caplog.at_level(logging.ERROR, logger="agent_lab.agent.runs"):
        events, (active_run_id, follow_up_event, follow_up_run_id), rows = run(scenario())

    # 1、终态事件照旧发出（这次运行的答案还是给了用户）。
    assert events[-1]["event"] == "done"
    assert events[-1]["answer"] == "降息 25 个基点。"
    # 2、写表失败只记日志、没有异常冒出来，而且**不重试**：两次运行各只留一条日志。
    #    按运行标识去重计数而不是笼统数条数——重试留下的是一条一模一样的日志，用它才看得出来。
    failures = [each for each in caplog.messages if "写入会话历史失败" in each]
    logged_run_ids = [each.split("run_id=")[1].split(" ")[0] for each in failures]
    assert sorted(logged_run_ids) == sorted([events[0]["run_id"], follow_up_run_id])
    # 3、会话位照旧释放：第二次提问（也走这条失败的写入路径）正常跑完。
    assert active_run_id is None
    assert follow_up_event == "done"
    # 4、表里始终什么都没有。
    assert rows == []


def test_the_finish_path_fills_in_the_group_of_a_run_that_never_wrote(tmp_path: Path) -> None:
    """真实收尾也会补缺组：第一次运行那一组没落表，第二次运行的收尾把它一起补上。

    第一次运行的写入失败与「被强杀」留下同一个可观察状态：那一组只在 checkpoint 里、表里没有。
    这条钉住让补写生效的那一段确实在收尾路径上——``runs.py`` 把快照里**全部**组交给写入方
    （``group_messages_by_run`` 的整份结果），而不是只交本次运行那一组；只交一组的话，结束时表里
    只会剩下第二次运行自己那两行。第一次运行那次失败本身不在断言范围内（它由上一个用例负责）。
    """

    async def scenario() -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], list[AgentThreadMessageRecord]
    ]:
        async with history_database(tmp_path) as sessions:
            checkpointer = InMemorySaver()
            # 第一次运行：写入方必失败，所以它那一组不落表；图状态留在共享的 checkpointer 里。
            broken, _search = create_agent_app(
                ScriptedChatModel(responses=[AIMessage(content="第一答。")]),
                threads=BrokenHistoryService(sessions),
                checkpointer=checkpointer,
            )
            async with broken.router.lifespan_context(broken):
                transport = httpx.ASGITransport(app=broken)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    first_events = await ask_fully(client, {"message": "第一问"})
            thread_id = UUID(first_events[0]["thread_id"])
            assert await read_rows(sessions, thread_id) == []

            # 第二次运行：同一个 checkpointer、同一个库，写入方是真的。
            app, _search = create_agent_app(
                ScriptedChatModel(responses=[AIMessage(content="第二答。")]),
                threads=AgentThreadService(sessions),
                checkpointer=checkpointer,
            )
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    second_events = await ask_fully(
                        client, {"message": "第二问", "thread_id": str(thread_id)}
                    )
            return first_events, second_events, await read_rows(sessions, thread_id)

    first_events, second_events, rows = run(scenario())

    assert first_events[-1]["event"] == "done"
    assert second_events[-1]["event"] == "done"
    first_run, second_run = UUID(first_events[0]["run_id"]), UUID(second_events[0]["run_id"])
    assert first_run != second_run
    # 两个运行组都在，从没落过表的那一组排在它前面。
    assert [(row.run_id, row.text) for row in rows] == [
        (first_run, "第一问"),
        (first_run, "第一答。"),
        (second_run, "第二问"),
        (second_run, "第二答。"),
    ]
    assert [row.seq for row in rows] == [0, 1, 2, 3]


# ---- 删除连带（真实 ORM 与事务跑在内存 SQLite 上）----
#
# 库里没有外键约束，删一行会话不会带走这张表的行，连带删除全靠 Service 在同一事务里做
# （见 ADR 0028 与 backend/AGENTS.md）。所以这一节的断言只有一个方向：**删完再读表**。


async def seed_session_with_rows(
    sessions: Any, *, user_id: UUID, first_message: str, answer: str
) -> UUID:
    """建一个会话并给它写下一轮问答，返回会话 id。

    建行走真实的 ``ensure_thread``、写行走真实的 ``record_run_messages``：本节断言的是「删会话
    之后表里那些行没了」，所以预置数据也走生产那两条路径，不另造一份形状不同的行。
    """

    service = AgentThreadService(sessions)
    run_id = uuid4()
    thread_id, _prompt = await service.ensure_thread(
        user_id=user_id, thread_id=None, first_message=first_message, run_id=run_id
    )
    await service.record_run_messages(
        thread_id=thread_id,
        run_id=run_id,
        runs=project([question(first_message, run_id=run_id), answered(run_id, answer)]),
    )
    return thread_id


def test_deleting_a_thread_removes_only_that_threads_rows(tmp_path: Path) -> None:
    """删一个会话：它自己的历史行没了，同一个库里另一个会话的行一行不动。

    跨会话隔离是这条路径的核心风险——删错范围就是别人的历史不可逆地没了，而库里没有外键兜底，
    范围全靠那个 ``thread_id`` 条件。所以两个会话各写一组、删掉其中一个再逐行比对。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            owner = uuid4()
            doomed = await seed_session_with_rows(
                sessions, user_id=owner, first_message="要删的会话", answer="它的答案。"
            )
            kept = await seed_session_with_rows(
                sessions, user_id=owner, first_message="留下的会话", answer="它的答案。"
            )
            kept_rows = [(row.role, row.text) for row in await read_rows(sessions, kept)]
            assert len(kept_rows) == 2 and len(await read_rows(sessions, doomed)) == 2, (
                "预置的行不齐，这条用例证明不了任何事"
            )

            await service.delete_thread_record(user_id=owner, thread_id=doomed)

            assert await read_rows(sessions, doomed) == []
            assert [(row.role, row.text) for row in await read_rows(sessions, kept)] == kept_rows
            # 会话归属行也一起没了；另一个会话还在。
            assert await service.list_known_thread_ids() == {kept}

    run(verify())


def test_deleting_a_thread_that_is_not_yours_keeps_its_rows(tmp_path: Path) -> None:
    """归属不匹配时整体回滚：连先删的那批历史行也回来。

    实现是「先删历史行、再删归属行、删不到就 rollback」。少一个回滚，别人的历史就被静默删掉，
    而接口仍然返回 404——用户在界面上看到的只是一个删不掉的会话。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            owner = uuid4()
            thread_id = await seed_session_with_rows(
                sessions, user_id=owner, first_message="别人的会话", answer="他的答案。"
            )
            before = [(row.role, row.text) for row in await read_rows(sessions, thread_id)]

            with pytest.raises(AgentThreadNotFoundError):
                await AgentThreadService(sessions).delete_thread_record(
                    user_id=uuid4(), thread_id=thread_id
                )

            assert [(row.role, row.text) for row in await read_rows(sessions, thread_id)] == before

    run(verify())


def test_deleting_old_threads_removes_their_rows_and_spares_the_rest(tmp_path: Path) -> None:
    """旧会话清理那条批量删：点名的会话两张表的行一起没，没点名的会话一行不动。"""

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            owner = uuid4()
            stale = await seed_session_with_rows(
                sessions, user_id=owner, first_message="旧会话", answer="旧答案。"
            )
            fresh = await seed_session_with_rows(
                sessions, user_id=owner, first_message="新会话", answer="新答案。"
            )
            fresh_rows = [(row.role, row.text) for row in await read_rows(sessions, fresh)]

            deleted = await service.delete_threads([str(stale)])

            assert deleted == 1
            assert await read_rows(sessions, stale) == []
            assert [(row.role, row.text) for row in await read_rows(sessions, fresh)] == fresh_rows
            assert await service.list_known_thread_ids() == {fresh}

    run(verify())


def test_a_leftover_with_no_ownership_row_is_readable_and_deletable(tmp_path: Path) -> None:
    """孤儿残余：只有历史表里有行、没有归属行——按 ``thread_id`` 读得到也删得掉。

    这种行的来路是删一个**正在运行**的会话（等它停下的上限到了删除就继续，而那次运行随后仍会把
    收尾内容写回本表）。它既不在归属表里、也不在 checkpointer 里，所以只能靠 ``thread_id``
    这一侧把候选集补全、再按 id 删。
    """

    async def verify() -> None:
        async with history_database(tmp_path) as sessions:
            service = AgentThreadService(sessions)
            leftover, leftover_run = uuid4(), uuid4()
            # 直接写一组没有归属行的历史行：写入侧不核对会话行是否还在，这正是残余的来路。
            await service.record_run_messages(
                thread_id=leftover,
                run_id=leftover_run,
                runs=project(
                    [question("孤儿提问", run_id=leftover_run), answered(leftover_run, "孤儿答案。")]
                ),
            )
            assert await service.list_known_thread_ids() == set()

            assert await service.list_message_thread_ids() == {leftover}
            assert await service.delete_thread_messages([str(leftover)]) == 2
            assert await read_rows(sessions, leftover) == []

    run(verify())
