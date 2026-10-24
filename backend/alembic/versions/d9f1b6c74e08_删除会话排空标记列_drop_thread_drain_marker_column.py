"""删除会话表上的「被排空时刻」列。

**只删这一列，不碰别的列、不碰索引。** 排空改由收到退出信号的那个进程自己收尾之后（见 ADR
0051），这一列没有读者也没有写者：曾经被排空的进程写下它、由别的进程扫到并认领，那条接手路径
已整条撤掉，留下它只是每次发布都要维护的一段结构。

**连带删掉的是占位条件里那条排除**：带标记的会话过去连失活分支也不放行，理由是「两个进程会写
同一份图状态」；取消接手后不存在第二个写者，那个理由与条件一起消失。占位条件里的失活分支本身
原样留住——它防的是孤儿会话，与接手无关。

**代价是不可逆地丢值**：`downgrade` 只把列结构加回来，原来非空的那些时刻恢复不了，已经排空过的
会话不会回到「等接手」；空值的含义正是「没人排空过」，与回滚前的行为一致。

**列说明只在这里写一次**：ORM 上那一列已经删掉，所以回滚要写回的说明只能来自加列那次迁移
（``f2b9c7d41a08``）的原文，逐字照抄，由
``tests/test_thread_drain_column_drop_migration.py`` 钉住。

Revision ID: d9f1b6c74e08
Revises: a3c8e5d719bf
"""

# 这行标记让部署脚本把这条迁移推迟到旧容器退出之后再执行。
POST_DEPLOY = True

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d9f1b6c74e08"
down_revision: str | Sequence[str] | None = "a3c8e5d719bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 回滚目标：加列那次迁移（``f2b9c7d41a08``）写下的原文，与当时 ORM 上的注解逐字一致。
DRAINED_COMMENT = (
    "被排空的时刻；非空表示这个会话的在途运行已停在可交接的边界、等别的进程接手。业务层"
    "维护的排空标记：被排空的进程写下，接手者在抢占所有权的那次条件写入里消费（置空）；"
    "带标记的会话不接受新提问（占位路径的失活分支排除它），见 ADR 0040。"
)


def upgrade() -> None:
    """删掉排空标记列。"""

    op.drop_column("agent_threads", "drained_at")


def downgrade() -> None:
    """把列结构加回来，值不恢复。

    回滚后的库对旧代码是结构可用的：旧代码会重新往这一列写，而存量行只是空着——空值的含义是
    「没人排空过」，所以回滚之后发生的排空照常被标记，改动前已经排空过的会话则不会。
    """

    op.add_column(
        "agent_threads",
        sa.Column(
            "drained_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=DRAINED_COMMENT,
        ),
    )
