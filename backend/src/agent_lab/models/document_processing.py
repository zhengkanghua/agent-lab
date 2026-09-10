"""文档原件、候选处理和人工审核的持久记录。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent_lab.db.base import Base, TimestampMixin


class DocumentProcessingRecord(TimestampMixin, Base):
    """一份原始资料或人工候选的一次可恢复处理记录。"""

    __tablename__ = "document_processing_records"
    __table_args__ = (
        Index("ix_document_processing_records_state", "state", "updated_at"),
        Index("ix_document_processing_records_document", "document_id", "updated_at"),
        {"comment": "文档原件接收、解析预览、索引候选与失败恢复记录。"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False,
        comment="所属 Document；同一 Document 可保留多个来源和采用记录。",
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, comment="file、freshrss 或 manual。")
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="received", comment="处理阶段。")
    source_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True, comment="S3 私有原件对象键。")
    source_object_version: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="S3 对象版本标识。")
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="原始字节 SHA-256。")
    source_size: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="原始字节数。")
    source_mime_type: Mapped[str | None] = mapped_column(String(127), nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    source_stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    draft_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_mime_type: Mapped[str | None] = mapped_column(String(127), nullable=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    parser_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parsed_document: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chunk_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    preview_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issue_codes: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    index_instance_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, unique=True)
    index_target: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="采用决定冻结的索引写入目标。")
    index_prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    index_cleanup_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    index_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_source_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    latest_source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DocumentVersion(TimestampMixin, Base):
    """已经完成采用的不可变正文和 Chunk 快照。"""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision", name="uq_document_versions_document_revision"),
        {"comment": "文档已采用版本的不可变正文、结构与 Chunk 快照。"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    processing_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("document_processing_records.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parsed_document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    chunk_result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processing_spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_object_version: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    index_instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentReviewRecord(TimestampMixin, Base):
    """审核决定的追加记录；草稿编辑不会覆盖历史结论。"""

    __tablename__ = "document_review_records"
    __table_args__ = (
        Index("ix_document_review_records_document", "document_id", "created_at"),
        CheckConstraint("decision IN ('adopt', 'reject', 'retry')", name="ck_document_review_decision"),
        {"comment": "文档候选采用、拒绝及自动采用审核结论。"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    processing_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("document_processing_records.id", ondelete="RESTRICT"), nullable=False)
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, comment="adopt、reject 或 retry。")
    decision_source: Mapped[str] = mapped_column(String(32), nullable=False, comment="automatic 或 manual。")
    actor_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"),
        comment="作出决定时的正文与格式；之后修正草稿不会改写该结论依据。")
