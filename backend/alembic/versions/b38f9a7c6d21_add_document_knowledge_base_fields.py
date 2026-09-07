"""将 Source/Document 归属迁移到 KnowledgeBase，并放宽通用文档字段。"""

from alembic import op
import sqlalchemy as sa


revision = "b38f9a7c6d21"
down_revision = "a27d6b9e4301"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """先回填稳定 news 库，再收紧 Document 外键，避免现有知识数据失联。"""

    op.add_column("sources", sa.Column("knowledge_base_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_sources_knowledge_base_id", "sources", "knowledge_bases",
        ["knowledge_base_id"], ["id"], ondelete="RESTRICT",
    )
    op.add_column("documents", sa.Column("knowledge_base_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_documents_knowledge_base_id", "documents", "knowledge_bases",
        ["knowledge_base_id"], ["id"], ondelete="RESTRICT",
    )
    op.add_column("documents", sa.Column("mime_type", sa.String(127), nullable=True))
    op.execute(sa.text("""
        UPDATE sources
        SET knowledge_base_id = (SELECT id FROM knowledge_bases WHERE key = 'news')
        WHERE knowledge_base_id IS NULL
    """))
    op.execute(sa.text("""
        UPDATE documents
        SET knowledge_base_id = (SELECT id FROM knowledge_bases WHERE key = 'news'),
            mime_type = 'text/plain'
        WHERE knowledge_base_id IS NULL OR mime_type IS NULL
    """))
    op.alter_column("documents", "knowledge_base_id", nullable=False)
    op.alter_column("documents", "mime_type", nullable=False, server_default="text/plain")
    op.alter_column("documents", "source_id", nullable=True)
    op.alter_column("documents", "external_id", nullable=True)
    op.alter_column("documents", "url", nullable=True)
    op.drop_constraint("ck_documents_document_type", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_document_type", "documents",
        "document_type IN ('article', 'press_release', 'economic_release', 'filing', 'research_report', 'policy_document', 'other')",
    )


def downgrade() -> None:
    """降级前不伪造丢失的 Source/Document 关系，交由发布流程人工确认。"""

    op.drop_constraint("ck_documents_document_type", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_document_type", "documents",
        "document_type IN ('article', 'press_release', 'economic_release', 'filing', 'research_report', 'policy_document')",
    )
    op.alter_column("documents", "url", nullable=False)
    op.alter_column("documents", "external_id", nullable=False)
    op.alter_column("documents", "source_id", nullable=False)
    op.drop_column("documents", "mime_type")
    op.drop_constraint("fk_documents_knowledge_base_id", "documents", type_="foreignkey")
    op.drop_column("documents", "knowledge_base_id")
    op.drop_constraint("fk_sources_knowledge_base_id", "sources", type_="foreignkey")
    op.drop_column("sources", "knowledge_base_id")
