"""删除会话历史消息表上的压缩分界标记列。

**只删这一列，不碰别的列、不碰索引。** 它是「模型只剩摘要」那条分界线的唯一载体：界面上那条
分界线、回放接口里那个字段、写入端往摘要消息上贴的两个字段全部随它一起撤掉，所以库里再留着它
就是一份没有任何读者的数据（见 ADR 0049）。

**代价是不可逆地丢值**：`downgrade` 只把列结构加回来，原来那些 `role="summary"` 行上的值恢复
不了。已经压缩过的会话再也推不出「模型只剩摘要」这件事——这一点老板已确认接受。

**摘要行本身不在这里删**：`agent_thread_messages` 里存量的 `role="summary"` 行仍在，读侧照旧
跳过它们（不跳过就会把摘要正文当成一轮问答渲染出来）。写入端从这次改动起不再产生摘要行，因此
不需要数据迁移。

**列说明只在这里写一次**：ORM 上那一列已经删掉，所以回滚要写回的说明只能来自建表那次迁移
（``e9b3c7a41d58``）的原文，逐字照抄，由
``tests/test_drop_memory_boundary_migration.py`` 钉住。

Revision ID: c5d8e2a47f91
Revises: b7d4e1a9c3f2
"""

# 这行标记让部署脚本把这条迁移推迟到旧容器退出之后再执行。
POST_DEPLOY = True

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c5d8e2a47f91"
down_revision: str | Sequence[str] | None = "b7d4e1a9c3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 回滚目标：建表那次迁移写下的原文，与当时 ORM 上的注解逐字一致。
MEMORY_BOUNDARY_COMMENT = (
    "压缩边界标记：只有摘要行有，记的是被折掉那一段里最后一次提问的运行 id"
    "（「这一轮及其之前的轮次，模型只剩摘要」）。"
)


def upgrade() -> None:
    """删掉压缩分界标记列。"""

    op.drop_column("agent_thread_messages", "memory_boundary_run_id")


def downgrade() -> None:
    """把列结构加回来，值不恢复。

    回滚后的库对旧代码是完整可用的：旧代码会往这个列写值，新写到的那几行有值、存量行是空的
    ——分界线因此只在回滚之后发生的压缩上出现，比改动前少，不会画错位置。
    """

    op.add_column(
        "agent_thread_messages",
        sa.Column(
            "memory_boundary_run_id",
            sa.Uuid(),
            nullable=True,
            comment=MEMORY_BOUNDARY_COMMENT,
        ),
    )
