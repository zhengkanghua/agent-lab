"""把一次运行的消息投影成会话历史业务表的行。

本模块只做纯内存翻译：输入是 ``replay.group_messages_by_run`` 分好组的原始消息、以及回放算好的
轮次，输出是「一组行」。它不读数据库、不调 checkpointer，也不决定写哪些组——取快照在
``agent/runs.py``，整组替换与幂等在 ``services/agent_thread_service.py``。

**为什么这一层要有**：行级形状是这张表的对外事实（一条消息几行、工具调用与结果怎么拆、运行
元数据装什么），抽成纯函数才能离线钉住；``runs.py`` 那边只剩「读一次快照 → 分组 → 投影 → 交出去」。

**摘要消息不进任何一组，也不产生行**：``group_messages_by_run`` 会把摘要跳过（它不属于某一次
对话），收尾写入也只投影分组出来的消息，所以压缩留下的摘要伪提问在业务表里一行都不占。历史摘要
只活在 checkpointer 的上下文记录里，会话历史那头只能看到压缩前的原文。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from langchain_core.messages import AIMessage, ToolMessage

from agent_lab.agent.evidence import ToolEvidence, tool_evidence
from agent_lab.agent.replay import RunMessageGroup, text_of
from agent_lab.knowledge.scope import ResolvedKnowledgeBaseScope
from agent_lab.schemas.agent_thread import AgentReplayTurn


logger = logging.getLogger(__name__)

# 行的角色取值，与 ``models.agent_thread_message.AgentThreadMessageRecord.role`` 的列说明同一套。
# 写入端只产出前四种；``summary`` 是历史库存量行里才有的取值，读侧仍然跳过它（列说明因此保留
# 这个值），但写入端不再产生它——摘要不被写进业务表。
MessageRole = Literal["question", "answer", "tool_call", "tool_result"]


@dataclass(frozen=True, slots=True)
class ThreadMessageRow:
    """一条消息在表里的那一行。

    不含会话、顺序号与写入时刻：那三样由写入方补齐（顺序号要按会话里已有的行现算）。

    Attributes:
        role: 这一行的角色。
        text: 正文；工具调用行没有正文。
        tool_name: 工具名，只有工具调用行有。
        tool_arguments: 这次调用的参数，只有工具调用行有。
        tool_call_id: 配对键，工具调用行与工具结果行各有。
        failed: 这次调用是否失败，只有工具结果行有。
        evidence: 该次调用实际使用的范围与引用证据，只有工具结果行有。
        run_meta: 运行元数据，只有该运行的提问行有。
    """

    role: MessageRole
    text: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_call_id: str | None = None
    failed: bool | None = None
    evidence: dict[str, Any] | None = None
    run_meta: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RunMessageRows:
    """一次运行那一组要落的全部行。

    Attributes:
        run_id: 这一组的运行标识；写入方按它整组替换、按它判断这一组是不是已经写过。
        rows: 按消息在快照里的先后排好的行；顺序号由写入方按这个先后逐个加一。
    """

    run_id: UUID
    rows: tuple[ThreadMessageRow, ...]


def project_run_messages(
    groups: Sequence[RunMessageGroup],
    *,
    turns: Sequence[AgentReplayTurn],
) -> tuple[RunMessageRows, ...]:
    """把按运行分好的消息投影成落表的行，逐组返回。

    Args:
        groups: ``replay.group_messages_by_run`` 的产物。
        turns: ``replay.build_replay_turns`` 算好的轮次；「是否完整作答」从它按运行标识取。

    Returns:
        每组一项，按传入顺序；**运行标识取不到的组不在里面**。

    Notes:
        纯内存转换，不执行 I/O。

        「是否完整作答」不在这里另判，而是用回放已经算好的那一份（按 ``run_id`` 找到对应轮次取
        它的 ``status``）：终态事件与刷新后的回放各说一套，正是这套共用判定要防的那件事。

        找不到运行标识的组被跳过并记一条日志，不编占位 id——编出来的 id 会把两次不同的运行并成
        一组，而这张表的切分与组装全靠它。
    """

    completed_by_run = {turn.run_id: turn.status == "completed" for turn in turns}
    projected: list[RunMessageRows] = []
    for group in groups:
        if group.run_id is None:
            logger.warning(
                "写入会话历史跳过运行标识缺失的一组 message_count=%s",
                len(group.messages),
            )
            continue
        projected.append(
            RunMessageRows(
                run_id=group.run_id,
                rows=_rows_of_group(
                    group, completed=completed_by_run.get(group.run_id, False)
                ),
            )
        )
    return tuple(projected)


def _rows_of_group(group: RunMessageGroup, *, completed: bool) -> tuple[ThreadMessageRow, ...]:
    """把一组消息摊成行。

    Args:
        group: 一组原始消息，首条是这次运行的提问（它也是运行元数据的载体）。
        completed: 这一轮是否完整作答，取自回放算好的完成态。
    """

    question = group.messages[0]
    # 运行元数据**透传**提问消息上 ``agent_run`` 里的内容：范围快照今天就在里面，「当时用的
    # 模型」那一份由模型目录那次改动加进同一处，这里原样带走、不自己造，也不再补别的东西——
    # 唯一补的是「是否完整作答」，它只有收尾这一刻算得出来。
    meta = dict(question.additional_kwargs.get("agent_run") or {})
    scope = ResolvedKnowledgeBaseScope.model_validate(meta["scope"]) if meta.get("scope") else None
    rows = [
        ThreadMessageRow(
            role="question",
            text=text_of(question),
            run_meta={**meta, "completed": completed},
        )
    ]

    for message in group.messages[1:]:
        if isinstance(message, AIMessage):
            # 正文为空串的助手消息不单独成行：回放本来就是把这轮所有助手正文拼起来，空行对它
            # 没有贡献；而它的工具调用照样各占一行。
            text = text_of(message)
            if text:
                rows.append(ThreadMessageRow(role="answer", text=text))
            rows.extend(
                ThreadMessageRow(
                    role="tool_call",
                    tool_name=call.get("name") or "unknown",
                    tool_arguments=dict(call.get("args") or {}),
                    tool_call_id=call.get("id") or None,
                )
                for call in message.tool_calls
            )
            continue
        if isinstance(message, ToolMessage):
            # 证据与轨迹同一套校验（运行标识、范围、成功与失败）：回放给出的引用就是这么算的，
            # 两边口径一致才不会出现「界面上有引用、表里没有」。失败的结果没有证据可存。
            artifact = (
                tool_evidence(message, run_id=group.run_id, scope=scope)
                if scope is not None
                else None
            )
            rows.append(
                ThreadMessageRow(
                    role="tool_result",
                    text=str(message.content),
                    tool_call_id=message.tool_call_id or None,
                    failed=message.status == "error",
                    evidence=_evidence_column(artifact) if artifact is not None else None,
                )
            )
    return tuple(rows)


def _evidence_column(artifact: ToolEvidence) -> dict[str, Any]:
    """把一次工具结果的证据投影成 ``evidence`` 列的内容。

    Notes:
        每条引用证据里**去掉** ``excerpt`` 与 ``truncated`` 两个键：这一列不再带正文片段（完整
        原文由本表的工具结果行承载，见 ADR 0044），而删字段本身是上下文策略那次改动的事——
        在这里先投影掉，那一步就不必回来改这一处。其余键原样保留。
    """

    return {
        "scope": artifact.scope.model_dump(mode="json"),
        "citations": [
            item.model_dump(mode="json", exclude={"excerpt", "truncated"})
            for item in artifact.evidence
        ],
    }


__all__ = [
    "MessageRole",
    "RunMessageRows",
    "ThreadMessageRow",
    "project_run_messages",
]
