"""增加本地账号密码认证 add local password auth。

Revision ID: a6d9c2e4f781
Revises: d2e6f4a8b1c3
Create Date: 2026-08-17 17:00:00.000000

该 migration 新增内部用户与可撤销数据库登录 Token；不创建公开注册资料、OAuth、
密码重置、邮件验证或新闻所有权关系。已有新闻、来源和向量索引状态不受影响。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a6d9c2e4f781"
down_revision: str | Sequence[str] | None = "d2e6f4a8b1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建用户和数据库登录 Token 表及其授权查询索引。"""

    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="应用生成的登录用户 UUID 主键。",
        ),
        sa.Column(
            "email",
            sa.String(length=320),
            nullable=False,
            comment="大小写不敏感的登录邮箱；仅作账号标识，当前不用于发送邮件。",
        ),
        sa.Column(
            "hashed_password",
            sa.String(length=1024),
            nullable=False,
            comment="由 pwdlib 生成的密码 Hash；永不保存或返回明文密码。",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="账号是否允许登录和继续使用已有登录 Token。",
        ),
        sa.Column(
            "is_superuser",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否允许执行手动 Pipeline 等高权限操作。",
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="账号是否由管理员确认；CLI 创建的封闭账号直接标记为已确认。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="记录首次写入 PostgreSQL 的时间。",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="记录最后一次通过 ORM 更新的时间。",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="可登录 News RAG Platform 的内部人工账号。",
    )
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )

    op.create_table(
        "access_tokens",
        sa.Column(
            "token",
            sa.String(length=43),
            nullable=False,
            comment="FastAPI Users 生成并写入 HttpOnly Cookie 的高熵随机登录 Token。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Token 创建时间；DatabaseStrategy 据此判断会话是否过期。",
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            nullable=False,
            comment="该登录 Token 所属的 users.id；删除用户时级联撤销。",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token"),
        comment="浏览器账号密码登录产生的可撤销数据库访问 Token。",
    )
    op.create_index(
        "ix_access_tokens_created_at",
        "access_tokens",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_access_tokens_user_id",
        "access_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """先删除依赖 users 的登录 Token，再删除用户账号。"""

    op.drop_index("ix_access_tokens_user_id", table_name="access_tokens")
    op.drop_index("ix_access_tokens_created_at", table_name="access_tokens")
    op.drop_table("access_tokens")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
