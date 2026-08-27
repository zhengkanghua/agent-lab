"""增加文档向量索引状态 add document vector index state

Revision ID: 7f21b2f64718
Revises: 4c9da5fcae18
Create Date: 2026-08-13 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "7f21b2f64718"
down_revision: str | Sequence[str] | None = "4c9da5fcae18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为 documents 增加可审计的 Qdrant 索引状态字段。

    这些字段只记录一篇业务文档的索引版本和最近处理结果，不保存 Chunk 或
    Embedding。现有行从 revision 1 开始；后续独立 data migration 会把缺少成功快照的
    历史状态重新排队，索引 Service 成功写入后再填写 indexed 字段。
    """

    op.add_column(
        "documents",
        sa.Column(
            "index_revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="需要写入 Qdrant 的文档版本号；任何可索引字段变化都会递增。",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "indexed_revision",
            sa.Integer(),
            nullable=True,
            comment="Qdrant 最近一次完整成功写入对应的文档版本号。",
        ),
    )
    # 约束必须在两个 revision 列都存在后创建；否则 PostgreSQL 无法解析
    # indexed_revision。数据库约束继续保护绕过 ORM 的直接 SQL 写入。
    op.create_check_constraint(
        "ck_documents_index_revision_positive",
        "documents",
        "index_revision >= 1",
    )
    op.create_check_constraint(
        "ck_documents_indexed_revision_positive",
        "documents",
        "indexed_revision IS NULL OR indexed_revision >= 1",
    )
    op.add_column(
        "documents",
        sa.Column(
            "indexed_content_hash",
            sa.String(length=64),
            nullable=True,
            comment="Qdrant 最近一次完整成功写入对应的规范正文 SHA-256。",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "indexed_schema_version",
            sa.String(length=32),
            nullable=True,
            comment="Qdrant 最近一次成功写入使用的向量索引 Schema 版本。",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "processing_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="当前一次向量索引 Worker 开始处理的时间。",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="当前文档版本最近一次完整写入 Qdrant 的时间。",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "last_processing_error",
            sa.Text(),
            nullable=True,
            comment="最近一次索引失败的脱敏、限长错误说明，不保存密钥或完整正文。",
        ),
    )


def downgrade() -> None:
    """移除文档索引状态字段，不影响 Qdrant 中已经存在的 Point。"""

    op.drop_constraint(
        "ck_documents_indexed_revision_positive",
        "documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_documents_index_revision_positive",
        "documents",
        type_="check",
    )
    op.drop_column("documents", "last_processing_error")
    op.drop_column("documents", "indexed_at")
    op.drop_column("documents", "processing_started_at")
    op.drop_column("documents", "indexed_schema_version")
    op.drop_column("documents", "indexed_content_hash")
    op.drop_column("documents", "indexed_revision")
    op.drop_column("documents", "index_revision")
