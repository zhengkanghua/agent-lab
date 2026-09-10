"""增加原件、处理候选、已采用版本与审核记录。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f7c1d2e3a4b5"
down_revision = "e74b9a310c65"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_bases", sa.Column("visibility_revision", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("documents", "content_text", nullable=True,
                    comment="当前已采用正文；首次采用前为空。")
    op.alter_column("documents", "content_hash", nullable=True,
                    comment="当前已采用正文的 SHA-256；首次采用前为空。")
    op.add_column("documents", sa.Column("management_revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("documents", sa.Column("latest_processing_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("draft_processing_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("current_index_instance_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("manual_review_required", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("documents", sa.Column("current_version_id", sa.Uuid(), nullable=True,
        comment="当前正式可见的 DocumentVersion 身份。"))
    op.add_column("documents", sa.Column("usage_status", sa.String(32), nullable=False,
        server_default="active", comment="active、rejected 或 deleting。"))

    op.create_table(
        "document_processing_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="received"),
        sa.Column("source_object_key", sa.String(1024), nullable=True),
        sa.Column("source_object_version", sa.String(512), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("source_size", sa.Integer(), nullable=True),
        sa.Column("source_mime_type", sa.String(127), nullable=True),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_stored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("draft_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draft_text", sa.Text(), nullable=True),
        sa.Column("draft_mime_type", sa.String(127), nullable=True),
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("parser_id", sa.String(128), nullable=True),
        sa.Column("parsed_document", postgresql.JSONB(), nullable=True),
        sa.Column("chunk_result", postgresql.JSONB(), nullable=True),
        sa.Column("preview_fingerprint", sa.String(64), nullable=True),
        sa.Column("issue_codes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("index_instance_id", sa.Uuid(), nullable=True, unique=True),
        sa.Column("index_target", postgresql.JSONB(), nullable=True),
        sa.Column("index_prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("index_cleanup_pending", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("index_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_source_object_key", sa.String(1024), nullable=True),
        sa.Column("latest_source_sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_document_processing_records_state", "document_processing_records", ["state", "updated_at"])
    op.create_index("ix_document_processing_records_document", "document_processing_records", ["document_id", "updated_at"])
    op.create_foreign_key(
        "fk_documents_latest_processing_id", "documents", "document_processing_records",
        ["latest_processing_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_documents_draft_processing_id", "documents", "document_processing_records",
        ["draft_processing_id"], ["id"], ondelete="SET NULL",
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processing_id", sa.Uuid(), sa.ForeignKey("document_processing_records.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(127), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parsed_document", postgresql.JSONB(), nullable=False),
        sa.Column("chunk_result", postgresql.JSONB(), nullable=False),
        sa.Column("processing_spec", postgresql.JSONB(), nullable=False),
        sa.Column("source_object_key", sa.String(1024), nullable=True),
        sa.Column("source_object_version", sa.String(512), nullable=True),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("source_size", sa.Integer(), nullable=True),
        sa.Column("metadata_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("index_instance_id", sa.String(128), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "revision", name="uq_document_versions_document_revision"),
    )
    op.create_foreign_key(
        "fk_documents_current_version_id", "documents", "document_versions",
        ["current_version_id"], ["id"], ondelete="SET NULL",
    )

    op.create_table(
        "document_review_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processing_id", sa.Uuid(), sa.ForeignKey("document_processing_records.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("decision_source", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("preview_fingerprint", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("decision IN ('adopt', 'reject', 'retry')", name="ck_document_review_decision"),
    )
    op.create_index("ix_document_review_records_document", "document_review_records", ["document_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_document_review_records_document", table_name="document_review_records")
    op.drop_table("document_review_records")
    op.drop_constraint("fk_documents_current_version_id", "documents", type_="foreignkey")
    op.drop_table("document_versions")
    op.drop_index("ix_document_processing_records_document", table_name="document_processing_records")
    op.drop_index("ix_document_processing_records_state", table_name="document_processing_records")
    op.drop_constraint("fk_documents_latest_processing_id", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_draft_processing_id", "documents", type_="foreignkey")
    op.drop_table("document_processing_records")
    op.drop_column("documents", "usage_status")
    op.drop_column("documents", "current_version_id")
    op.drop_column("documents", "latest_processing_id")
    op.drop_column("documents", "draft_processing_id")
    op.drop_column("documents", "current_index_instance_id")
    op.drop_column("documents", "manual_review_required")
    op.drop_column("documents", "management_revision")
    # 降级前先处理尚未采用的资料；不以空字符串伪造旧 schema 所要求的正文。
    op.alter_column("documents", "content_text", nullable=False)
    op.alter_column("documents", "content_hash", nullable=False)
    op.drop_column("knowledge_bases", "visibility_revision")
