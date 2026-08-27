"""增加来源增量同步检查点 add source sync checkpoint。

Revision ID: d2e6f4a8b1c3
Revises: 8c31d42d965a
Create Date: 2026-08-14 16:00:00.000000

该 migration 只为 ``sources`` 增加 FreshRSS 来源级 continuation 状态，不创建
Chunk、Embedding 或 pipeline runs 表。游标和推进时间允许为空，以兼容已存在的来源；
首次阶段 6 同步会在该来源的一页新闻成功提交后填入游标。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d2e6f4a8b1c3"
down_revision: str | Sequence[str] | None = "8c31d42d965a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为每个来源增加可回滚的 FreshRSS continuation 游标。

    两列都可空，因为历史来源尚未执行阶段 6 增量同步。游标的写入由应用在同一
    PostgreSQL 事务中与来源/文档 upsert 一起完成；migration 本身不回写任何新闻状态。
    """

    op.add_column(
        "sources",
        sa.Column(
            "sync_checkpoint",
            sa.String(length=128),
            nullable=True,
            comment=(
                "该来源最近一次成功持久化 FreshRSS 分页的 continuation 游标；"
                "仅用于可靠增量同步，不进入文档索引或向量。"
            ),
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "sync_checkpoint_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="该来源增量同步游标最近一次成功推进的时间；未同步时为空。",
        ),
    )


def downgrade() -> None:
    """移除来源同步游标，不删除已保存的 sources/documents 业务事实。"""

    op.drop_column("sources", "sync_checkpoint_updated_at")
    op.drop_column("sources", "sync_checkpoint")
