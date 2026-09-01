"""增加 agent 会话归属 add agent thread ownership。

Revision ID: c3f8a1b6e492
Revises: b7e1a4c9d203
Create Date: 2026-09-01 10:00:00.000000

该 migration 新增 agent_threads 表，用来记录「某个 Agent 会话属于哪个账号」以及会话列表要显示的
标题和时间。它不创建、不修改也不删除 LangGraph checkpointer 的四张 checkpoint* 表——那些表由
`agent-lab init-checkpointer` 负责，且被 alembic/env.py 的 include_object 排除在自动比对之外
（见 docs/adr/0004-checkpointer-tables-outside-alembic.md）。

本 migration **不回填**已有会话的归属。库里可能存在联调期产生的无主 thread（checkpointer 里有历史、
本表里没有归属记录），它们由 `agent-lab prune-orphan-threads` 清理，不在这里删：迁移文件按约定是
「写下来就不再变」的历史记录，里面调第三方库的删除逻辑，回滚到旧迁移时行为已经不是当初那个了。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c3f8a1b6e492"
down_revision: str | Sequence[str] | None = "b7e1a4c9d203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建会话归属表及其列表查询索引。"""

    op.create_table(
        "agent_threads",
        sa.Column(
            "thread_id",
            sa.Uuid(),
            nullable=False,
            comment="会话 id，与 LangGraph checkpointer 的 thread_id 同值；由服务端生成。",
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            nullable=False,
            comment="该会话所属的 users.id；删除账号时级联删除归属记录。",
        ),
        sa.Column(
            "title",
            sa.String(length=60),
            nullable=False,
            comment="会话标题，取首条提问截断而来；创建后不再改写。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="会话创建时间，即第一次提问被受理的时刻。",
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="最后一次在本会话提问的时刻；会话列表的排序键。",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("thread_id"),
        comment="Agent 会话的账号归属与列表元信息；不含消息内容。",
    )
    # 复合且第二列降序，对应「我的 + 按最近活跃倒序 + 分页」这一个固定查询形状。
    op.create_index(
        "ix_agent_threads_user_id_last_active_at",
        "agent_threads",
        ["user_id", sa.text("last_active_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """删除会话归属表。

    注意回滚的代价：归属记录没了，但 checkpointer 里的会话历史还在，于是**所有**会话都变成无主
    thread——查不到、也没法从网页删掉。重新 upgrade 不会把归属找回来（那些信息只存在于本表）。
    回滚后若不打算再升回来，用 `agent-lab prune-orphan-threads` 把残留历史清掉。

    这不是缺陷，是「归属只存在业务表里」这个设计的必然结果；写在这里是为了让回滚的人事先知道。
    """

    op.drop_index(
        "ix_agent_threads_user_id_last_active_at",
        table_name="agent_threads",
    )
    op.drop_table("agent_threads")
