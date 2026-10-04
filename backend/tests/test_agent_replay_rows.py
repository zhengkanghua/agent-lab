"""``agent/replay_rows.py`` 的离线测试：会话历史表的行 → 用户回看的轮次。

行由用例**直接构造**（不落库、不起应用）：这一层的验收面是「行怎么拼成轮次」，行本身怎么落由
``tests/test_agent_thread_messages.py`` 在真实库上验，接口那一层由
``tests/test_agent_threads_api.py`` 验。

不连 PostgreSQL、不访问网络、不调真实大模型。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from agent_lab.agent.evidence import DocumentEvidence
from agent_lab.agent.replay_rows import build_replay_turns_from_rows
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.knowledge.scope import KnowledgeBaseSummary, ResolvedKnowledgeBaseScope
from agent_lab.models.agent_thread_message import AgentThreadMessageRecord
from agent_lab.schemas.llm_models import ResolvedLlmModel
from tests.agent_scope_helpers import NEWS_SCOPE


THREAD_ID = UUID("10000000-0000-4000-8000-0000000000ff")
OTHER_BASE = KnowledgeBaseSummary(id=uuid4(), key="manuals", name="操作资料", description="操作手册")
WIDER_SCOPE = ResolvedKnowledgeBaseScope(
    mode="all", knowledge_bases=(*NEWS_SCOPE.knowledge_bases, OTHER_BASE)
)
FROZEN_MODEL = ResolvedLlmModel(
    id=UUID("40000000-0000-4000-8000-0000000000b1"),
    display_name="备用模型",
    context_window=8192,
)


def stored(role: str, *, run_id: UUID, seq: int, **columns: Any) -> AgentThreadMessageRecord:
    """一行历史消息；只填这条用例关心的列，其余留空（与表里的可空列同义）。"""

    return AgentThreadMessageRecord(
        id=uuid4(),
        thread_id=THREAD_ID,
        run_id=run_id,
        seq=seq,
        role=role,
        created_at=datetime.now(UTC),
        **columns,
    )


def question_row(
    run_id: UUID,
    *,
    seq: int = 0,
    text: str = "央行降息了吗",
    scope: ResolvedKnowledgeBaseScope | None = NEWS_SCOPE,
    model: ResolvedLlmModel | None = None,
    completed: bool = True,
) -> AgentThreadMessageRecord:
    """提问行：这一轮的运行元数据（范围快照、完成态、当轮模型）都在它身上。

    ``completed`` 写入侧写的是回放算出的完成态；这里默认给「完整作答」，个别用例显式改。
    """

    meta: dict[str, Any] = {"run_id": str(run_id), "completed": completed}
    if scope is not None:
        meta["scope"] = scope.model_dump(mode="json")
    if model is not None:
        meta["llm_model"] = model.model_dump(mode="json")
    return stored("question", run_id=run_id, seq=seq, text=text, run_meta=meta)


def evidence_column(*citations: DocumentEvidence, scope: ResolvedKnowledgeBaseScope = NEWS_SCOPE) -> dict[str, Any]:
    """工具结果行的 ``evidence`` 列：该次调用实际使用的范围与引用证据。

    形状与写入侧的投影一致（见 ``agent/thread_messages._evidence_column``）。
    """

    return {
        "scope": scope.model_dump(mode="json"),
        "citations": [item.model_dump(mode="json") for item in citations],
    }


def citation(**changes: Any) -> DocumentEvidence:
    """一条引用证据；默认值都能过校验，用例只覆盖关心的那几个字段。"""

    values: dict[str, Any] = {
        "document_id": uuid4(),
        "knowledge_base_id": DEFAULT_NEWS_KNOWLEDGE_BASE_ID,
        "knowledge_base_name": "新闻",
        "title": "央行宣布降息",
        "content_hash": "a" * 64,
        "kind": "match",
    }
    return DocumentEvidence(**(values | changes))


def test_rows_become_the_turn_the_user_saw() -> None:
    """一轮的行拼成用户当时看到的那一轮：问答、工具轨迹、状态、范围与当轮模型。"""

    run_id = uuid4()
    turns, boundary = build_replay_turns_from_rows(
        [
            question_row(run_id, seq=0, model=FROZEN_MODEL),
            stored("answer", run_id=run_id, seq=1, text="我查一下。"),
            stored(
                "tool_call",
                run_id=run_id,
                seq=2,
                tool_name="search_documents",
                tool_arguments={"query": "央行降息"},
                tool_call_id="call-search",
            ),
            stored(
                "tool_result",
                run_id=run_id,
                seq=3,
                text="新闻资料正文。",
                tool_call_id="call-search",
                failed=False,
                evidence=evidence_column(citation()),
            ),
            stored("answer", run_id=run_id, seq=4, text="降了 25 个基点。"),
        ]
    )

    assert len(turns) == 1
    turn = turns[0]
    assert (turn.question, turn.answer) == ("央行降息了吗", "我查一下。降了 25 个基点。")
    assert turn.run_id == run_id
    assert turn.status == "completed"
    assert turn.scope == NEWS_SCOPE
    # 当轮模型取提问行上的冻结快照：展示名与窗口都自足，不回查目录。
    assert turn.llm_model == FROZEN_MODEL
    assert [(trace.tool, trace.arguments, trace.content, trace.failed) for trace in turn.traces] == [
        ("search_documents", {"query": "央行降息"}, "新闻资料正文。", False)
    ]
    # 工具轨迹带上这次调用实际使用的范围；没有摘要行时没有分界标记。
    assert turn.traces[0].scope == NEWS_SCOPE
    assert boundary is None


def test_tool_call_and_result_are_paired_by_tool_call_id() -> None:
    """工具调用与结果靠 ``tool_call_id`` 配对，不靠顺序。

    刻意让两次调用同名、结果**倒序**写在表里：靠顺序配对的实现会把两个结果配错，而并发工具
    调用的返回顺序本来就不保证。
    """

    run_id = uuid4()
    turns, _boundary = build_replay_turns_from_rows(
        [
            question_row(run_id),
            stored(
                "tool_call",
                run_id=run_id,
                seq=1,
                tool_name="search_documents",
                tool_arguments={"query": "甲"},
                tool_call_id="call-1",
            ),
            stored(
                "tool_call",
                run_id=run_id,
                seq=2,
                tool_name="search_documents",
                tool_arguments={"query": "乙"},
                tool_call_id="call-2",
            ),
            stored("tool_result", run_id=run_id, seq=3, text="乙的结果", tool_call_id="call-2"),
            stored("tool_result", run_id=run_id, seq=4, text="甲的结果", tool_call_id="call-1"),
            stored("answer", run_id=run_id, seq=5, text="两家说法一致。"),
        ]
    )

    assert [
        (trace.arguments["query"], trace.content) for trace in turns[0].traces
    ] == [("甲", "甲的结果"), ("乙", "乙的结果")]


def test_a_call_without_its_result_is_incomplete_and_has_no_content() -> None:
    """只有调用没有结果时 ``content`` 是 ``None``，这一轮也不算是完整作答。

    这种表来自「工具还没返回，运行就结束了」。完成态与读 checkpointer 那条路径同一份判定
    （``replay.turn_status``）：就算写入侧把完成标记写成了真，缺了结果的轮次也不能显示成答完。
    """

    run_id = uuid4()
    turns, _boundary = build_replay_turns_from_rows(
        [
            question_row(run_id, completed=True),
            stored(
                "tool_call",
                run_id=run_id,
                seq=1,
                tool_name="search_documents",
                tool_arguments={"query": "利率"},
                tool_call_id="call-search",
            ),
        ]
    )

    assert turns[0].traces[0].content is None
    assert turns[0].traces[0].failed is False
    assert turns[0].status == "incomplete"


def test_a_run_that_never_answered_stays_incomplete() -> None:
    """写入侧冻结的完成态是未答完时，回放如实标成未完成。"""

    run_id = uuid4()
    turns, _boundary = build_replay_turns_from_rows(
        [question_row(run_id, completed=False), stored("answer", run_id=run_id, seq=1, text="半句")]
    )

    assert (turns[0].answer, turns[0].status) == ("半句", "incomplete")


def test_turns_follow_the_sequence_number_across_runs() -> None:
    """同一会话里多次运行按顺序号排成多轮，各自带着自己的范围快照。"""

    first_run, second_run = uuid4(), uuid4()
    turns, _boundary = build_replay_turns_from_rows(
        [
            question_row(first_run, seq=0, text="第一问"),
            stored("answer", run_id=first_run, seq=1, text="第一答。"),
            question_row(second_run, seq=2, text="第二问", scope=None),
            stored("answer", run_id=second_run, seq=3, text="第二答。"),
        ]
    )

    assert [(turn.question, turn.answer) for turn in turns] == [
        ("第一问", "第一答。"),
        ("第二问", "第二答。"),
    ]
    assert [turn.scope for turn in turns] == [NEWS_SCOPE, None]
    # 没有盖下范围快照的那一轮没有当轮模型，也不该回落到会话当前的选择。
    assert turns[1].llm_model is None


def test_no_rows_gives_no_turns_and_no_boundary() -> None:
    """表里一行都没有时给空轮次与空标记，不报错。"""

    assert build_replay_turns_from_rows([]) == ((), None)


def test_a_group_without_its_question_row_is_skipped_but_the_rest_still_replays() -> None:
    """没有提问行的那一组不构成一轮（元数据无处可取），但不影响别的轮次。"""

    good_run, broken_run = uuid4(), uuid4()
    turns, _boundary = build_replay_turns_from_rows(
        [
            stored("answer", run_id=broken_run, seq=0, text="孤零零的回答"),
            question_row(good_run, seq=1, text="完整提问"),
            stored("answer", run_id=good_run, seq=2, text="完整回答。"),
        ]
    )

    assert [(turn.question, turn.answer) for turn in turns] == [("完整提问", "完整回答。")]


def test_a_summary_row_only_carries_the_boundary_marker() -> None:
    """直接放进表的两行摘要：分界标记取顺序号**最大**那条，摘要行自己不成一轮。"""

    run_id = uuid4()
    earlier_runs, latest_run = uuid4(), uuid4()
    turns, boundary = build_replay_turns_from_rows(
        [
            stored(
                "summary",
                run_id=uuid4(),
                seq=0,
                text="Here is a summary of the conversation to date: 旧背景",
                memory_boundary_run_id=earlier_runs,
            ),
            question_row(run_id, seq=1),
            stored("answer", run_id=run_id, seq=2, text="降了 25 个基点。"),
            stored(
                "summary",
                run_id=uuid4(),
                seq=9,
                text="Here is a summary of the conversation to date: 新背景",
                memory_boundary_run_id=latest_run,
            ),
        ]
    )

    assert [turn.question for turn in turns] == ["央行降息了吗"]
    assert boundary == latest_run


def test_citations_come_from_the_answer_identifiers() -> None:
    """引用由答案里的标识关联到证据：没有证据的标识进 ``invalid_citations``。"""

    run_id = uuid4()
    cited = citation(citation_id="E1a2b3c4d5e6f")
    unused = citation(citation_id="Eabcdefabcdef")
    answer = f"降了 25 个基点 [[{cited.citation_id}]]；伪造 [[Edeadbeef0000]]"
    turns, _boundary = build_replay_turns_from_rows(
        [
            question_row(run_id),
            stored(
                "tool_call",
                run_id=run_id,
                seq=1,
                tool_name="search_documents",
                tool_arguments={"query": "利率"},
                tool_call_id="call-search",
            ),
            stored(
                "tool_result",
                run_id=run_id,
                seq=2,
                text="新闻资料正文。",
                tool_call_id="call-search",
                failed=False,
                evidence=evidence_column(cited, unused),
            ),
            stored("answer", run_id=run_id, seq=3, text=answer),
        ]
    )

    assert turns[0].citations == (cited,)
    assert turns[0].invalid_citations == ("Edeadbeef0000",)


def test_evidence_covering_a_wider_scope_than_the_turn_is_refused() -> None:
    """证据块声明的范围比这一轮宽时不取它——与读 checkpointer 那条路径同一份校验。

    写入侧本来就按这条规则过滤过一次；这里再校一遍是因为表里的行也可能被写过之外的途径改动，
    而「引用只在当轮范围内成立」是提交给用户的保证。
    """

    run_id = uuid4()
    cited = citation(citation_id="E1a2b3c4d5e6f")
    turns, _boundary = build_replay_turns_from_rows(
        [
            question_row(run_id, scope=NEWS_SCOPE),
            stored(
                "tool_call",
                run_id=run_id,
                seq=1,
                tool_name="search_documents",
                tool_arguments={"query": "利率"},
                tool_call_id="call-search",
            ),
            stored(
                "tool_result",
                run_id=run_id,
                seq=2,
                text="越界的资料。",
                tool_call_id="call-search",
                failed=False,
                evidence=evidence_column(cited, scope=WIDER_SCOPE),
            ),
            stored("answer", run_id=run_id, seq=3, text=f"资料 [[{cited.citation_id}]]"),
        ]
    )

    assert turns[0].citations == ()
    assert turns[0].invalid_citations == ("E1a2b3c4d5e6f",)
