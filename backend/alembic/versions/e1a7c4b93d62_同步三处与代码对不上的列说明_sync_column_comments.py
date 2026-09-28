"""把三处「代码里有说明、库里对不上」的列说明同步进库。

**只改说明文字，不动类型、默认值、数据与约束。** 三处对不上的来源不同，但都是「说明写了，却没同步进库」：

1. ``user_preferences.created_at`` / ``updated_at``：这两列从 ``TimestampMixin`` 继承说明，而建表那次迁移
   （``e2c8f14b7a30``）只补了它自己列出的那几列，漏了这两句 —— 库里这两列**没有**说明。
2. ``users.is_environment_admin``：说明在代码里改过。旧句是「网页不可降级」，而取消降级之后
   （见 ADR 0038）真正被挡住的是停用与注销 —— 这句话今天描述的保护已经不存在，谁去查 schema 都会被带偏。

为什么要落地，而不是「反正程序不读它」：``alembic check`` 是我们用来发现**真漂移**（缺列、类型不对、少索引）
的工具，它长期报这三条会让真问题被噪声盖住；而且下一次 ``alembic revision --autogenerate`` 会把这三条修改
一起夹带进那个迁移里（上一份迁移大概就是这么漏的：有人 autogenerate 完手工删了一部分）。

Revision ID: e1a7c4b93d62
Revises: d5f8a2c7b9e1
"""

from collections.abc import Sequence

from alembic import op


revision: str = "e1a7c4b93d62"
down_revision: str | Sequence[str] | None = "d5f8a2c7b9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 目标值：与 ORM 上的注解逐字一致。改动这里必须同时改模型，反之亦然——由
# ``tests/test_column_comments_migration.py`` 钉住，所以两边不会再静默分叉。
CREATED_AT_COMMENT = "记录首次写入 PostgreSQL 的时间。"
UPDATED_AT_COMMENT = "记录最后一次通过 ORM 更新的时间。"
ENVIRONMENT_ADMIN_COMMENT = (
    "是否由 AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD 托管；全库最多一行，网页不可停用或注销。"
)

# 回滚目标：把库还原成本迁移之前的样子。两列当时没有说明（``None`` 表示清掉说明），第三列是旧措辞。
PREVIOUS_ENVIRONMENT_ADMIN_COMMENT = (
    "是否由 AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD 托管；全库最多一行，网页不可降级。"
)


def upgrade() -> None:
    """把三句说明写进库。"""

    op.alter_column("user_preferences", "created_at", comment=CREATED_AT_COMMENT)
    op.alter_column("user_preferences", "updated_at", comment=UPDATED_AT_COMMENT)
    op.alter_column("users", "is_environment_admin", comment=ENVIRONMENT_ADMIN_COMMENT)


def downgrade() -> None:
    """还原：两列清掉说明，第三列改回旧措辞。

    它只碰这三列，不整表刷新说明 —— 回滚不该顺手改别人负责的文字。
    """

    op.alter_column("user_preferences", "created_at", comment=None)
    op.alter_column("user_preferences", "updated_at", comment=None)
    op.alter_column("users", "is_environment_admin", comment=PREVIOUS_ENVIRONMENT_ADMIN_COMMENT)
