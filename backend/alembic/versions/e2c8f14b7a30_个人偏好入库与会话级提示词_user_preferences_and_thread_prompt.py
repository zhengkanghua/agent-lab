"""新增账号级个人偏好表与会话级提示词快照列。

**只做结构变更，不删任何业务数据。** 存量会话的 ``system_prompt`` 是 ``NULL``，含义是
「这个会话用服务端内置默认提示词」——那正是它们建立时的真实情况，不需要回填。存量账号在
``user_preferences`` 里没有行，含义是「还没配过」，读出来用契约默认值。

**不建数据库外键。** ``user_preferences.user_id`` 是指向 ``users.id`` 的逻辑外键，删账号时
由 ``UserAdminService.delete_user`` 在同一事务里显式删除这一行（见 ADR 0028）。这一列同时
是主键，所以它自带唯一索引，不需要再为查询单独建。

**``agent_threads.system_prompt`` 与同表 ``scope`` 列的写入语义相反**（续聊时 scope 可改、
system_prompt 不可改），这不是漏写，理由见 ADR 0029。

Revision ID: e2c8f14b7a30
Revises: d4b7c1e93a58
"""

from alembic import op
import sqlalchemy as sa


revision = "e2c8f14b7a30"
down_revision = "d4b7c1e93a58"
branch_labels = None
depends_on = None

TABLE_COMMENT = "账号级个人偏好；一行一个账号，删账号时由业务层清理。"
COLUMN_COMMENTS = (
    ("user_id", "该偏好所属的 users.id；业务层维护的逻辑外键，库上无约束。删账号时由业务层清理。"),
    ("system_prompt", "自定义系统提示词；为空表示使用服务端内置默认提示词。"),
    ("document_limit", "检索默认返回的文档数；写入前由应用层按契约边界归一化。"),
    ("matches_per_document", "每篇文档默认保留的片段数；写入前由应用层按契约边界归一化。"),
)
THREAD_PROMPT_COMMENT = (
    "会话建立时从该账号个人偏好拍下的系统提示词快照；为空表示用服务端内置默认提示词。"
)


def upgrade() -> None:
    # 1、建表。user_id 是主键，所以「一个账号一行」由主键唯一性保证，不另加唯一约束。
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("document_limit", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("matches_per_document", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_preferences"),
        sa.CheckConstraint("document_limit >= 1", name="ck_user_preferences_document_limit_positive"),
        sa.CheckConstraint("matches_per_document >= 1", name="ck_user_preferences_matches_positive"),
        comment=TABLE_COMMENT,
    )
    for column, comment in COLUMN_COMMENTS:
        op.alter_column("user_preferences", column, comment=comment)

    # 2、会话表加提示词快照列。可空：存量会话没有快照，NULL 就是它们当时的真实语义。
    op.add_column(
        "agent_threads",
        sa.Column("system_prompt", sa.Text(), nullable=True, comment=THREAD_PROMPT_COMMENT),
    )


def downgrade() -> None:
    """删掉这一列与这张表。

    与本仓库既有惯例一致：结构回滚不替业务决定要不要保留数据，删之前请自行确认这份偏好
    与会话提示词快照确实不再需要。这里不加孤儿检查——``user_preferences`` 没有反向引用，
    删表不会像建外键那样因为库里有脏数据而失败。
    """

    op.drop_column("agent_threads", "system_prompt")
    op.drop_table("user_preferences")
