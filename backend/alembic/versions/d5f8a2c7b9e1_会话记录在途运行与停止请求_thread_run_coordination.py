"""会话记录新增在途运行与停止请求两列，并把最后活跃时间的语义扩成「最后活动时刻」。

**只做结构变更，不删任何业务数据。** 存量会话的两列都是 ``NULL``，含义是「当前没有运行在跑、
无人要求停止」——那正是它们建立以来的真实情况，不需要回填。

**为什么这两列挂在 ``agent_threads`` 上而不是单独建一张运行表**：会话与在途运行是一对一（同一
个会话同时最多一次运行），挂在使用它的那张表上就够；单独建表会绕开现有的清理路径——删会话与注
销账号本来就会删 ``agent_threads`` 的行，新列跟着走，不必新增任何连带删除代码（见 ADR 0028）。

**``last_active_at`` 的语义在这一版被扩大**：它原本只记「最后一次提问时刻」，现在还要由运行驱动
者在运行过程中按固定节奏续期，作为「这次运行还活着吗」的失活判定依据。所以列注释一并改成「最后
活动时刻」；决策与代价见 ADR 0037，原来的写入时机说明在 ADR 0010 的后果一节。

Revision ID: d5f8a2c7b9e1
Revises: c1f4a7d92e60
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d5f8a2c7b9e1"
down_revision: str | Sequence[str] | None = "c1f4a7d92e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LAST_ACTIVE_COMMENT = (
    "最后活动时刻：提问受理时写一次，运行驱动者随后按固定节奏续期；会话列表的排序键，"
    "也用于判定在途运行是否已经失活。"
)
PREVIOUS_LAST_ACTIVE_COMMENT = "最后一次在本会话提问的时刻；会话列表的排序键。"
ACTIVE_RUN_COMMENT = (
    "在途运行的 id；为空表示这个会话当前没有运行在跑。同一个会话正在跑时，再次提交会被"
    "拒绝（409 agent_run_in_progress）。超过失活阈值未被续期视为已中断，允许下一次占位。"
)
STOP_REQUESTED_COMMENT = (
    "最近一次停止请求的时刻；为空表示无人要求停止。这是会话语义、不指向某次运行：写入端"
    "只在对得上 active_run_id 时才写，读取端靠下一次占位时一并清掉，见 ADR 0037。"
)


def upgrade() -> None:
    """新增两列，并刷新 ``last_active_at`` 的列说明。"""

    op.add_column(
        "agent_threads",
        sa.Column("active_run_id", sa.Uuid(), nullable=True, comment=ACTIVE_RUN_COMMENT),
    )
    op.add_column(
        "agent_threads",
        sa.Column(
            "stop_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=STOP_REQUESTED_COMMENT,
        ),
    )
    # 只改说明、不动类型与约束：这一列现在既记提问时刻，也记运行续期时刻。
    op.alter_column("agent_threads", "last_active_at", comment=LAST_ACTIVE_COMMENT)


def downgrade() -> None:
    """删掉两列，并把列说明改回旧语义。

    回滚的代价：在途运行与停止请求的记录随列一起没掉。已经跑着的运行不会因此停下——它只是在收尾
    时找不到自己占的位（条件写的是 ``active_run_id``），于是既不会释放什么、也不会误伤别人；停止
    请求则全部失效，直到重新升上来。这不是缺陷，而是「运行协调状态只存在这两列里」这个设计的必然
    结果（见 ADR 0037）。
    """

    op.alter_column("agent_threads", "last_active_at", comment=PREVIOUS_LAST_ACTIVE_COMMENT)
    op.drop_column("agent_threads", "stop_requested_at")
    op.drop_column("agent_threads", "active_run_id")
