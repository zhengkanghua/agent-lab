"""标记环境托管管理员 mark environment admin。

Revision ID: b7e1a4c9d203
Revises: a6d9c2e4f781
Create Date: 2026-08-18 01:00:00.000000

新增环境托管标记、权限一致性约束和“全库最多一个环境管理员”的 PostgreSQL 部分唯一
索引；既有账号默认不受环境托管，不修改密码、权限或登录 Token。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b7e1a4c9d203"
down_revision: str | Sequence[str] | None = "a6d9c2e4f781"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加环境管理员标记、权限约束和单行部分唯一索引。"""

    op.add_column(
        "users",
        sa.Column(
            "is_environment_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment=(
                "是否由 AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD 托管；"
                "全库最多一行，网页不可降级。"
            ),
        ),
    )
    op.create_check_constraint(
        "ck_users_environment_admin_privileges",
        "users",
        "NOT is_environment_admin OR (is_active AND is_superuser AND is_verified)",
    )
    op.create_index(
        "uq_users_single_environment_admin",
        "users",
        ["is_environment_admin"],
        unique=True,
        postgresql_where=sa.text("is_environment_admin"),
    )


def downgrade() -> None:
    """移除环境托管标记，不删除账号或登录 Token。"""

    op.drop_index("uq_users_single_environment_admin", table_name="users")
    op.drop_constraint(
        "ck_users_environment_admin_privileges",
        "users",
        type_="check",
    )
    op.drop_column("users", "is_environment_admin")
