"""会话记录新增「被排空、等接手」的标记列。

**只做结构变更，不删任何业务数据。** 存量会话的这列是 ``NULL``，含义是「当前没有排空待接手的
运行」——那正是它们建立以来的真实情况，不需要回填。

**为什么这列也挂在 ``agent_threads`` 上**：它与「在途运行的 id」「停止请求时刻」是同一种事实
（这个会话的运行协调状态），挂在使用它的那张表上就够；会话与在途运行是一对一。理由与代价见
ADR 0037 与 ADR 0040。

**它的生命周期与其他两列不同**：被排空的进程写下它，接手者在抢所有权的那次条件写入里把它
消费掉（同一条语句里判非空、置空），所以它不是「靠下一次占位清掉」的那一类；带这个标记的
会话不接受新提问（占位那条条件写入的失活分支要排除带标记的会话）。

Revision ID: f2b9c7d41a08
Revises: e1a7c4b93d62
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f2b9c7d41a08"
down_revision: str | Sequence[str] | None = "e1a7c4b93d62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRAINED_COMMENT = (
    "被排空的时刻；非空表示这个会话的在途运行已停在可交接的边界、等别的进程接手。业务层"
    "维护的排空标记：被排空的进程写下，接手者在抢占所有权的那次条件写入里消费（置空）；"
    "带标记的会话不接受新提问（占位路径的失活分支排除它），见 ADR 0040。"
)


def upgrade() -> None:
    """新增可空的排空标记列。"""

    op.add_column(
        "agent_threads",
        sa.Column("drained_at", sa.DateTime(timezone=True), nullable=True, comment=DRAINED_COMMENT),
    )


def downgrade() -> None:
    """删掉标记列。

    回滚的代价：已经写下「等接手」标记的运行会失去标记，接手者再也扫不到它；那个会话会一直
    占着在途位、直到失活阈值把它解锁（用户看到的是未写完的内容）。旧代码本来也不认这个标记，
    所以回滚后行为与改动前一致，不额外制造新语义。
    """

    op.drop_column("agent_threads", "drained_at")
