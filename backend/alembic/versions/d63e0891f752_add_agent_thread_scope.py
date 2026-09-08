"""持久化会话知识库选择；消息和引用继续归 checkpointer。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d63e0891f752"
down_revision = "c49a70d2e831"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 既有会话原来固定 news，迁移保留限制；新建会话默认所有启用知识库。
    op.add_column("agent_threads", sa.Column(
        "scope", postgresql.JSONB(), nullable=False,
        server_default=sa.text("'{\"mode\": \"selected\", \"knowledge_base_ids\": [\"10000000-0000-4000-8000-000000000010\"]}'::jsonb"),
        comment="用户选择的知识库范围，独立于可能压缩的消息历史。",
    ))
    op.alter_column("agent_threads", "scope", server_default=sa.text("'{\"mode\": \"all\"}'::jsonb"))


def downgrade() -> None:
    op.drop_column("agent_threads", "scope")
