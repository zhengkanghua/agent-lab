"""声明会话历史消息的 PostgreSQL 实体。

这张表是**用户能回看的会话历史**：一次运行收尾时把这一轮写进来，一条消息一行。它与 checkpointer
的四张 ``checkpoint*`` 表（见 ADR 0004）是两份不同的东西——那边是模型看到的上下文记录，会被压缩
破坏性重写；这边是用户看到的记录，写完不再改（见 ADR 0043 与 ADR 0044）。

**轮次不是数据，是读的时候组装出来的视图**：本表只按 ``run_id`` 记「这条消息属于哪一次运行」，
读的时候把同一 ``run_id`` 的行拼成一轮。唯一的细化是助手消息带来的每一个工具调用、每一个工具
结果各占一行——两者靠 ``tool_call_id`` 配对，不靠顺序，因为并发工具调用的返回顺序不保证。

``thread_id`` 是**逻辑外键**（指向 ``agent_threads.thread_id``）：列与索引在，库上没有
``FOREIGN KEY`` 约束，连带删除由业务层在同一事务里做（见 ADR 0028 与 ``backend/AGENTS.md``）。
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent_lab.db.base import Base


class AgentThreadMessageRecord(Base):
    """agent_thread_messages 表：一次会话里逐条消息的记录。

    写入只发生在运行收尾（``AgentRunRegistry._drive`` 的收尾路径），且按（会话，运行）整组
    替换：先删这一组的旧行、再按顺序插入。所以同一次收尾被执行两遍，表里那些行也只有一份。

    ``seq`` 是**会话内**递增的顺序号，读的时候只按它排序；唯一键也只建在（会话，``seq``）上，
    **不把 ``run_id`` 算进来**——把运行 id 放进唯一键就等于把「一组内唯一」当成表的语义，而
    真正的语义是整个会话一条时间线。

    大部分列只有特定角色会用到（``tool_name`` 只有工具调用行、``run_meta`` 只有提问行、
    ``memory_boundary_run_id`` 只有摘要行），所以它们可空，而不是按角色拆成几张表。
    """

    __tablename__ = "agent_thread_messages"
    __table_args__ = (
        # 兜底用的唯一键：同一个会话里两个不同的顺序号才是两条不同的消息，撞号说明写入方
        # 算错了顺序，宁可让插入失败（收尾只记日志）也不要静默写成两条并列的行。
        UniqueConstraint("thread_id", "seq", name="uq_agent_thread_messages_thread_id_seq"),
        # 写入方每次收尾都要读「这一会话已有哪几个运行组、各有几个顺序号」，并按（会话，运行）
        # 整组替换删除；两个动作都走这两列。唯一键按 seq 起头，服务不了按 run_id 的查询。
        Index("ix_agent_thread_messages_thread_id_run_id", "thread_id", "run_id"),
        {
            "comment": (
                "会话历史消息：一次会话里逐条消息的记录，用户回看读它；与 checkpointer 里的"
                "上下文记录相互独立。"
            )
        },
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4,
        comment="Python 服务生成的消息主键。",
    )
    thread_id: Mapped[UUID] = mapped_column(
        Uuid, nullable=False,
        comment="该消息所属的会话 agent_threads.thread_id；业务层维护的逻辑外键，库上无约束。",
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid, nullable=False,
        comment=(
            "这条消息属于哪一次运行；切分与组装都按它。运行标识取不到的组不写入，所以这一列"
            "不为空。"
        ),
    )
    seq: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment=(
            "会话内递增的顺序号，读的时候只按它排序。同一会话内唯一（唯一键不把运行 id 算"
            "进来）；重复收尾时沿用第一次写入的那一组顺序号。"
        ),
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="消息角色：question / answer / tool_call / tool_result / summary。",
    )
    text: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment=(
            "正文：提问、回答、摘要与工具结果的返回内容都放这里；工具调用行没有正文，它的内容"
            "在 tool_name 与 tool_arguments 里。"
        ),
    )
    tool_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
        comment="只有工具调用行有：被调用的工具名。",
    )
    tool_arguments: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment="只有工具调用行有：这次调用的参数，原样存模型给出的那一份。",
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment=(
            "工具调用行与工具结果行各自的配对键；两行靠它配对，不靠顺序（并发调用的返回顺序"
            "不保证）。"
        ),
    )
    failed: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True,
        comment="只有工具结果行有：这次调用是否失败。",
    )
    evidence: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment=(
            "只有工具结果行有：该次调用实际使用的范围与引用证据；引用项不含正文片段"
            "（工具正文已由本表的工具结果行承载，不必再存一份副本）。"
        ),
    )
    run_meta: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,
        comment=(
            "只有该运行的提问行有：当时的范围快照、是否完整作答、当时用的模型快照。"
        ),
    )
    memory_boundary_run_id: Mapped[UUID | None] = mapped_column(
        Uuid, nullable=True,
        comment=(
            "压缩边界标记：只有摘要行有，记的是被折掉那一段里最后一次提问的运行 id"
            "（「这一轮及其之前的轮次，模型只剩摘要」）。"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="这一行写入的时刻；整组替换时随新写入的那一份更新。",
    )


__all__ = ["AgentThreadMessageRecord"]
