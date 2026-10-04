"""建立「会话历史消息」表 add agent thread messages。

会话历史不再只活在 LangGraph checkpointer 的四张表里（那边是模型看到的上下文记录、会被压缩
破坏性重写）：用户能回看的那一份改由本表承载，一条消息一行，运行收尾时按（会话，运行）整组
替换写入（见 ``docs/adr/0044-session-history-in-own-table.md``）。本迁移**只建这一张表**，
写入侧随后由代码落地，存量会话按升级步骤清空、不回填。

表结构与 ORM 逐列对齐（含列说明）：``agent_lab.models.agent_thread_message.AgentThreadMessageRecord``。
真库上那一步的验收靠 ``alembic check``——它会逐列比对库与模型。

Revision ID: e9b3c7a41d58
Revises: c4a8f1d6b2e7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e9b3c7a41d58"
down_revision: str | Sequence[str] | None = "c4a8f1d6b2e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 ``agent_thread_messages`` 一张表与它的两个索引；没有存量数据需要迁移或回填。"""

    op.create_table(
        "agent_thread_messages",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="Python 服务生成的消息主键。",
        ),
        sa.Column(
            "thread_id",
            sa.Uuid(),
            nullable=False,
            comment="该消息所属的会话 agent_threads.thread_id；业务层维护的逻辑外键，库上无约束。",
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            nullable=False,
            comment=(
                "这条消息属于哪一次运行；切分与组装都按它。运行标识取不到的组不写入，所以这一列"
                "不为空。"
            ),
        ),
        sa.Column(
            "seq",
            sa.Integer(),
            nullable=False,
            comment=(
                "会话内递增的顺序号，读的时候只按它排序。同一会话内唯一（唯一键不把运行 id 算"
                "进来）；重复收尾时沿用第一次写入的那一组顺序号。"
            ),
        ),
        sa.Column(
            "role",
            sa.String(length=16),
            nullable=False,
            comment="消息角色：question / answer / tool_call / tool_result / summary。",
        ),
        sa.Column(
            "text",
            sa.Text(),
            nullable=True,
            comment=(
                "正文：提问、回答、摘要与工具结果的返回内容都放这里；工具调用行没有正文，它的内容"
                "在 tool_name 与 tool_arguments 里。"
            ),
        ),
        sa.Column(
            "tool_name",
            sa.String(length=128),
            nullable=True,
            comment="只有工具调用行有：被调用的工具名。",
        ),
        sa.Column(
            "tool_arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="只有工具调用行有：这次调用的参数，原样存模型给出的那一份。",
        ),
        sa.Column(
            "tool_call_id",
            sa.String(length=255),
            nullable=True,
            comment=(
                "工具调用行与工具结果行各自的配对键；两行靠它配对，不靠顺序（并发调用的返回顺序"
                "不保证）。"
            ),
        ),
        sa.Column(
            "failed",
            sa.Boolean(),
            nullable=True,
            comment="只有工具结果行有：这次调用是否失败。",
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "只有工具结果行有：该次调用实际使用的范围与引用证据；引用项不含正文片段"
                "（工具正文已由本表的工具结果行承载，不必再存一份副本）。"
            ),
        ),
        sa.Column(
            "run_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="只有该运行的提问行有：当时的范围快照、是否完整作答、当时用的模型快照。",
        ),
        sa.Column(
            "memory_boundary_run_id",
            sa.Uuid(),
            nullable=True,
            comment=(
                "压缩边界标记：只有摘要行有，记的是被折掉那一段里最后一次提问的运行 id"
                "（「这一轮及其之前的轮次，模型只剩摘要」）。"
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="这一行写入的时刻；整组替换时随新写入的那一份更新。",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "thread_id", "seq", name="uq_agent_thread_messages_thread_id_seq"
        ),
        comment=(
            "会话历史消息：一次会话里逐条消息的记录，用户回看读它；与 checkpointer 里的"
            "上下文记录相互独立。"
        ),
    )
    op.create_index(
        "ix_agent_thread_messages_thread_id_run_id",
        "agent_thread_messages",
        ["thread_id", "run_id"],
    )


def downgrade() -> None:
    """删除这张表。

    回滚的代价：用户能回看的会话历史（本表写入的那些轮次）一起消失，而 checkpointer 那边
    只剩模型看到的上下文记录——被压缩过的早期轮次本来就取不回来了。开发阶段的存量本来就是
    清空后重新开始的，所以这里不另做备份。
    """

    op.drop_index(
        "ix_agent_thread_messages_thread_id_run_id", table_name="agent_thread_messages"
    )
    op.drop_table("agent_thread_messages")
