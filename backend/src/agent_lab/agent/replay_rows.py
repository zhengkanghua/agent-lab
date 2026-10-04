"""把 ``agent_thread_messages`` 的行组装成用户回看的轮次。

本模块只做纯内存翻译：输入是一个会话按 ``seq`` 排好序的消息行，输出是 ``schemas.agent_thread``
的回放模型。它不读数据库、不调 checkpointer——按会话说取行在
``services/agent_thread_service.py``，归属校验在 ``api/agent_threads.py``。

**为什么回放读这张表**：checkpointer 里的消息会被压缩破坏性重写，用户能看到什么不该由压缩策略
决定（见 ADR 0043 与 ADR 0044）；这张表写完不再改，压缩也不动它。代价是同一段对话有两份数据，
所以终态事件（``agent/streaming.py`` 的 ``build_terminal_event``）与接手重建上下文
（``agent/runs.py`` 的 ``_read_frozen_run_meta``）仍读 checkpointer——它们发生在收尾前后，那时
这一轮可能还没落表。

**两条路径的口径必须一致**，否则会出现「Done 说答完了，刷新后那一轮变成没答完」。所以这里不复刻
任何判定规则，全部沿用写入侧与回放侧已有的纯函数：

- 行的正文与证据：写入侧用 ``replay.text_of`` 与 ``evidence.tool_evidence`` 投影，本模块读回来的
  就是同一份（见 ``agent/thread_messages.py``）；
- 完成态：共用 ``replay.turn_status``。写入侧把 ``build_replay_turns`` 算出的状态冻结进提问行的
  ``run_meta.completed``，本模块读它，不再自己判一遍；
- 引用：共用 ``evidence.validate_tool_evidence``（证据块校验）与 ``evidence.resolve_citations``
  （答案里的标识关联到证据）。

**一条消息一行，轮次不是数据**：同一 ``run_id`` 的行拼成一轮，提问行是那一轮的运行元数据载体
（范围快照、完成态、当轮模型都在它身上）；``answer`` 行按顺序拼成回答正文；``tool_call`` 与
``tool_result`` 行按 ``tool_call_id`` 配对，不靠顺序——并发工具调用的返回顺序不保证。``summary``
行不属于任何一轮，它只承载压缩分界标记。
"""

import logging
from collections.abc import Sequence
from uuid import UUID

from agent_lab.agent.evidence import (
    DocumentEvidence,
    ToolEvidence,
    resolve_citations,
    validate_tool_evidence,
)
from agent_lab.agent.replay import turn_status
from agent_lab.knowledge.scope import ResolvedKnowledgeBaseScope
from agent_lab.models.agent_thread_message import AgentThreadMessageRecord
from agent_lab.schemas.agent_thread import AgentReplayTrace, AgentReplayTurn
from agent_lab.schemas.llm_models import ResolvedLlmModel


logger = logging.getLogger(__name__)

# 行的角色取值，与 ``models.agent_thread_message`` 的列说明同一套。本模块只读，不判断哪个角色
# 该有哪些列——写入侧那份投影（``agent/thread_messages.py``）才是形状的真源。
QUESTION_ROLE = "question"
ANSWER_ROLE = "answer"
TOOL_CALL_ROLE = "tool_call"
TOOL_RESULT_ROLE = "tool_result"
SUMMARY_ROLE = "summary"


def build_replay_turns_from_rows(
    rows: Sequence[AgentThreadMessageRecord],
) -> tuple[tuple[AgentReplayTurn, ...], UUID | None]:
    """把一张会话的消息行组装成按时间排列的轮次，并给出压缩分界标记。

    Args:
        rows: 该会话的全部消息行，**按 ``seq`` 升序**（那是这张表唯一承诺的顺序）。

    Returns:
        ``(轮次, 模型只剩摘要的那一段的最后一轮运行 id)``。会话里没有行时是 ``((), None)``，
        没有任何摘要行时后一项也是 ``None``。

    Notes:
        纯内存转换，不执行任何 I/O。

        分界标记取**顺序号最大的那一条摘要行**的 ``memory_boundary_run_id``：摘要行一次压缩写一
        行、旧行不删，而分界标记在后来的压缩里可能沿用上一条的值（被折掉的那一段里一条提问都没有
        时），所以只有最近那一条是「此刻模型只保留了摘要」的真实边界；取最旧一条会把线画得比实际早。

        回放**不再给出摘要正文**：界面不展示它，分界线靠那个标记就够了（见 spec 0003 的「读取
        路径彻底离开 checkpointer」）。
    """

    turns: list[AgentReplayTurn] = []
    for run_id, group in _rows_by_run(rows).items():
        turn = _build_turn(run_id, group)
        if turn is not None:
            turns.append(turn)
    return tuple(turns), _memory_boundary_run_id(rows)


def _rows_by_run(
    rows: Sequence[AgentThreadMessageRecord],
) -> dict[UUID, list[AgentThreadMessageRecord]]:
    """把行按运行分组，组与组之间保持行出现的先后。

    Notes:
        纯内存转换。摘要行不进任何一组：它不属于某一次对话，只是分界标记的载体。
    """

    groups: dict[UUID, list[AgentThreadMessageRecord]] = {}
    for row in rows:
        if row.role == SUMMARY_ROLE:
            continue
        groups.setdefault(row.run_id, []).append(row)
    return groups


def _memory_boundary_run_id(rows: Sequence[AgentThreadMessageRecord]) -> UUID | None:
    """取顺序号最大的那条摘要行上的分界标记。"""

    latest = max(
        (row for row in rows if row.role == SUMMARY_ROLE),
        key=lambda row: row.seq,
        default=None,
    )
    return None if latest is None else latest.memory_boundary_run_id


def _build_turn(
    run_id: UUID,
    rows: Sequence[AgentThreadMessageRecord],
) -> AgentReplayTurn | None:
    """把一组行定型成对外的一轮；这一组没有提问行时返回 ``None``。"""

    question = next((row for row in rows if row.role == QUESTION_ROLE), None)
    if question is None:
        # 提问行是这一轮运行元数据（范围、完成态、当轮模型）的唯一载体，缺了它组不出轮次。
        # 正常写入不会产出这种组，真出现说明有行丢了或被写坏了——跳过并记日志，而不是让整个
        # 回放接口 500：用户看不到这一轮，别的轮次照常。
        logger.warning("回放跳过一个缺少提问行的运行组 run_id=%s", run_id)
        return None

    # 运行时元数据是**当时**的快照（范围与模型都不回查目录），条目后来改名或停用不改写已经
    # 发生过的那几轮；`completed` 是写入那一刻由回放算出的完成态，见模块说明。
    meta = question.run_meta or {}
    scope = ResolvedKnowledgeBaseScope.model_validate(meta["scope"]) if meta.get("scope") else None
    llm_model = ResolvedLlmModel.model_validate(meta["llm_model"]) if meta.get("llm_model") else None
    answer = "".join(row.text or "" for row in rows if row.role == ANSWER_ROLE)
    results = {
        row.tool_call_id: row
        for row in rows
        if row.role == TOOL_RESULT_ROLE and row.tool_call_id
    }
    traces = tuple(
        _build_trace(row, results.get(row.tool_call_id or ""), run_id=run_id, scope=scope)
        for row in rows
        if row.role == TOOL_CALL_ROLE
    )
    citations, invalid = resolve_citations(
        answer, _evidence_of_rows(rows, run_id=run_id, scope=scope)
    )
    return AgentReplayTurn(
        question=question.text or "",
        answer=answer,
        traces=traces,
        run_id=run_id,
        scope=scope,
        llm_model=llm_model,
        status=turn_status(completed=bool(meta.get("completed")), traces=traces),
        citations=citations,
        invalid_citations=invalid,
    )


def _build_trace(
    tool_call: AgentThreadMessageRecord,
    result: AgentThreadMessageRecord | None,
    *,
    run_id: UUID,
    scope: ResolvedKnowledgeBaseScope | None,
) -> AgentReplayTrace:
    """把一行工具调用连同它配对的那行结果合成一条轨迹。

    Args:
        tool_call: 工具调用行。
        result: 按 ``tool_call_id`` 找到的结果行；找不到时为 ``None``。
        run_id: 这一轮的运行标识。
        scope: 这一轮冻结的范围；为 ``None`` 时不取证据。

    Returns:
        合成后的轨迹；没有结果行时 ``content`` 为 ``None``。

    Notes:
        纯内存转换。``content`` 为 ``None`` 表示表里只有调用没有结果，也就是那一轮在工具返回之前
        就结束了（用户取消、或运行报错）；与读 checkpointer 那条路径同义，提示语归前端。
    """

    artifact = _evidence_of(result, run_id=run_id, scope=scope) if result is not None else None
    return AgentReplayTrace(
        tool=tool_call.tool_name or "unknown",
        arguments=dict(tool_call.tool_arguments or {}),
        content=None if result is None else result.text,
        failed=bool(result.failed) if result is not None else False,
        scope=artifact.scope if artifact else None,
    )


def _evidence_of_rows(
    rows: Sequence[AgentThreadMessageRecord],
    *,
    run_id: UUID,
    scope: ResolvedKnowledgeBaseScope | None,
) -> list[DocumentEvidence]:
    """把一组里全部工具结果行的证据摊平成一份引用列表。

    Notes:
        纯内存转换。空手写一遍循环而不是写成推导式：这里要把「行没通过校验」的 ``None`` 排掉，
        写在推导式里比这个循环难读。
    """

    collected: list[DocumentEvidence] = []
    for row in rows:
        if row.role != TOOL_RESULT_ROLE:
            continue
        artifact = _evidence_of(row, run_id=run_id, scope=scope)
        if artifact is not None:
            collected.extend(artifact.evidence)
    return collected


def _evidence_of(
    row: AgentThreadMessageRecord,
    *,
    run_id: UUID,
    scope: ResolvedKnowledgeBaseScope | None,
) -> ToolEvidence | None:
    """把工具结果行的 ``evidence`` 列校验回证据块；这一行没有证据或范围未知时返回 ``None``。"""

    if row.evidence is None or scope is None:
        return None
    # 列的键名与 ``ToolEvidence`` 的字段名不同（列上是 ``scope`` 与 ``citations``，见写入侧的
    # ``agent/thread_messages._evidence_column``），所以这里显式搬一次；运行标识也不在列里，
    # 它由行自己的 ``run_id`` 给出。拼起来才是完整的证据块，校验规则与读 checkpointer 那条
    # 路径同一份（``validate_tool_evidence``）。
    validated = validate_tool_evidence(
        {
            "run_id": str(run_id),
            "scope": row.evidence.get("scope"),
            "evidence": row.evidence.get("citations") or [],
        },
        run_id=run_id,
        scope=scope,
    )
    if validated is None:
        logger.warning("回放跳过一个没通过校验的工具证据 run_id=%s", run_id)
    return validated


__all__ = [
    "ANSWER_ROLE",
    "QUESTION_ROLE",
    "SUMMARY_ROLE",
    "TOOL_CALL_ROLE",
    "TOOL_RESULT_ROLE",
    "build_replay_turns_from_rows",
]
