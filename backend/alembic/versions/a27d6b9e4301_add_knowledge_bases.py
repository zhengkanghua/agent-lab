"""建立 KnowledgeBase 配置与稳定的 news 初始库。

本批仅增加配置表。Source、Document 的归属和索引契约在后续迁移中成组接入，
不修改旧迁移，也不清理账号、会话、任务或现有知识数据。
"""

from alembic import op
import sqlalchemy as sa
from uuid import UUID


revision = "a27d6b9e4301"
down_revision = "f1a8c3d9e602"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建配置表并初始化稳定键；news 的 UUID 必须与应用默认值一致。"""

    table = op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False, comment="不可修改的稳定业务键。"),
        sa.Column("name", sa.String(255), nullable=False, comment="知识库展示名称。"),
        sa.Column("description", sa.Text(), nullable=True, comment="知识库说明。"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否启用。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="记录首次写入 PostgreSQL 的时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="记录最后一次通过 ORM 更新的时间。"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_knowledge_bases_key"),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'", name="ck_knowledge_bases_key_format"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_knowledge_bases_name_not_blank"),
        comment="逻辑知识库配置，不代表独立数据库或向量 Collection。",
    )
    op.execute(
        table.insert().values(
            # 应用层在兼容旧接口时按这个 UUID 限定 news；迁移必须写入同一身份。
            id=UUID("10000000-0000-4000-8000-000000000010"),
            key="news",
            name="新闻",
            description="新闻检索的兼容默认知识库。",
            is_active=True,
        )
    )


def downgrade() -> None:
    """移除本批新增的知识库配置表。"""

    op.drop_table("knowledge_bases")
