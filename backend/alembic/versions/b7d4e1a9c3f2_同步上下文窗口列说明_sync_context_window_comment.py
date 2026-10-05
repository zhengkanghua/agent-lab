"""同步 ``llm_models.context_window`` 的列说明：它不再约束工具输出的长度。

**只改说明文字，不动类型、默认值、数据与约束。** 旧句说这个窗口同时决定「一轮里单次工具输出能放
多长」，而工具输出已经不按它截断了（见 ADR 0047）：Pydantic 的字段说明与前端提示当时就改掉了，
ORM 上的 ``comment=`` 漏了，于是库里那句一直停留在旧语义，谁去查 schema 都会被带偏。

**建表那次迁移（``d2f5a8c31b76``）不改**：它记录的是当时写下的东西，库里的现状由本迁移纠正。

为什么要落地，而不是「反正程序不读它」：``alembic check`` 是我们用来发现**真漂移**（缺列、类型
不对、少索引）的工具，它长期报这一条会让真问题被噪声盖住；而且下一次
``alembic revision --autogenerate`` 会把这条改动一起夹带进那个迁移里。

Revision ID: b7d4e1a9c3f2
Revises: a3f7c2e9b5d4
"""

from collections.abc import Sequence

from alembic import op


revision: str = "b7d4e1a9c3f2"
down_revision: str | Sequence[str] | None = "a3f7c2e9b5d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 目标值：与 ORM 上的注解逐字一致。改动这里必须同时改模型，反之亦然——由
# ``tests/test_llm_context_window_comment_migration.py`` 钉住，所以两边不会再静默分叉。
CONTEXT_WINDOW_COMMENT = (
    "上游模型的上下文窗口 token 数，必填；历史压缩什么时候触发按它的比例算。"
    "填小了会提前压缩，模型更早只剩下摘要。"
)

# 回滚目标：把库还原成本迁移之前的样子，即建表那次迁移写下的旧措辞。
PREVIOUS_CONTEXT_WINDOW_COMMENT = (
    "上游模型的上下文窗口 token 数，必填；压缩什么时候触发按它的比例算，"
    "一轮里单次工具输出能放多长也取它的比例。填小了会提前压缩并截断工具输出。"
)


def upgrade() -> None:
    """把新说明写进库。"""

    op.alter_column("llm_models", "context_window", comment=CONTEXT_WINDOW_COMMENT)


def downgrade() -> None:
    """还原：改回建表当时的措辞。

    它只碰这一列，不整表刷新说明——回滚不该顺手改别人负责的文字。
    """

    op.alter_column("llm_models", "context_window", comment=PREVIOUS_CONTEXT_WINDOW_COMMENT)
