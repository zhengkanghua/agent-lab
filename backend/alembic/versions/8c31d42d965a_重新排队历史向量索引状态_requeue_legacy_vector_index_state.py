"""重新排队历史向量索引状态 requeue legacy vector index state

Revision ID: 8c31d42d965a
Revises: 7f21b2f64718
Create Date: 2026-08-13 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "8c31d42d965a"
down_revision: str | Sequence[str] | None = "7f21b2f64718"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """把缺少阶段 2 成功快照的历史文档统一重新排队。

    migration 之前的 ``indexed``、``failed`` 或 ``processing`` 不能证明当前 Qdrant
    Collection 已经保存完整 Point，因为它们没有 ``indexed_revision``、正文 hash 和
    Schema 版本。这里仅重置索引状态与旧错误，不修改新闻正文、来源或业务时间；后续
    ``DocumentIndexingService`` 会按当前 revision 幂等写入 Qdrant。
    """

    op.execute(
        sa.text(
            "UPDATE documents "
            "SET processing_status = 'pending', "
            "processing_started_at = NULL, "
            "last_processing_error = NULL "
            "WHERE indexed_revision IS NULL"
        )
    )


def downgrade() -> None:
    """不恢复无法证明来源的历史状态。

    data migration 不保存迁移前状态快照，回滚时猜测原值会制造错误事实，因此此处
    有意不执行 UPDATE；结构 migration 仍可继续独立回滚新增列。
    """
