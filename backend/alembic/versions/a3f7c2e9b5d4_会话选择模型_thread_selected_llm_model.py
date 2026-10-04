"""会话记住用户选的模型；为空表示用默认模型。

用户在会话里挑一个模型（见 ``docs/adr/0046-model-catalog-and-user-model-choice.md``），这个选择
存在会话行上，和同表的 ``scope``（会话知识库选择）是同一类东西：**续聊时可改，改选只影响下一次
运行**。为空表示没挑过，运行时用目录里标着默认的那一个。

三个刻意的取舍，都在这一列上：

1. **可空**。非空会让「没挑过」没有表示法，而「没挑就用默认」正是用户故事 4 要的行为。
2. **不建外键约束**（业务层维护的逻辑外键，见根 AGENTS.md 的「业务与数据约束」第 3 条与
   ADR 0028），列说明里写明了这一点；``llm_models`` 那一侧也没有删除入口，条目只会被停用。
3. **不索引**。读取路径永远是「按主键取一行会话」，没有「按模型找会话」这种查询；
   停用一条模型时不改这里的数据（只由选择器如实提示重选），所以也不需要按它扫描。

存量行全部为 ``NULL``，即「没选过模型」，运行时用默认模型——本次升级同时清空既有会话
（见 spec 0003 的「补充说明」），所以不存在需要猜一个历史模型名的行。

表结构与 ORM 逐列对齐（含列说明）：``agent_lab.models.agent_thread.AgentThreadRecord``。真库上
那一步的验收靠 ``alembic check``——它会逐列比对库与模型。

Revision ID: a3f7c2e9b5d4
Revises: d2f5a8c31b76
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a3f7c2e9b5d4"
down_revision: str | Sequence[str] | None = "d2f5a8c31b76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """给 ``agent_threads`` 加一列「当前选择的模型」；没有存量数据需要回填。

    列可空、没有 ``server_default``：存量行读出来是 ``NULL``，语义就是「没选过模型」。
    """

    op.add_column(
        "agent_threads",
        sa.Column(
            "llm_model_id",
            sa.Uuid(),
            nullable=True,
            comment=(
                "会话当前选择的可用模型 id；为空表示用默认模型。业务层维护的逻辑外键"
                "（指向 llm_models.id），库上无约束。条目后来改名或停用都不改写这一列，"
                "选择器读到它在目录里已不可用时如实提示重选。"
            ),
        ),
    )


def downgrade() -> None:
    """删掉这一列。

    回滚的代价：用户在这个会话里挑的模型丢掉，下一次提问回到默认模型（与升级前一致）。
    ``llm_models`` 那边一字不动。
    """

    op.drop_column("agent_threads", "llm_model_id")
