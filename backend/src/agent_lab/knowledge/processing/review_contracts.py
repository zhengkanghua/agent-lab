"""管理查询的项目快照；不暴露 ORM、S3 地址、凭据或第三方解析对象。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from agent_lab.knowledge.processing.contracts import DocumentPreview, ProcessingValue


class ManagedDocument(ProcessingValue):
    document_id: UUID
    knowledge_base_id: UUID
    knowledge_base_name: str
    knowledge_base_active: bool
    title: str
    source_kind: Literal["file", "freshrss"]
    upload_filename: str | None
    usage_status: str
    revision: int
    management_revision: int
    current_version_id: UUID | None
    latest_processing_id: UUID | None
    draft_processing_id: UUID | None
    processing_state: str | None
    error_code: str | None
    deletion_pending: bool
    updated_at: datetime


class ProcessingSummary(ProcessingValue):
    processing_id: UUID
    document_id: UUID
    source_kind: str
    state: str
    title: str
    candidate_revision: int
    requires_review: bool
    preview_fingerprint: str | None
    error_code: str | None
    issue_codes: tuple[str, ...]
    source_stored: bool
    source_sha256: str | None
    created_at: datetime
    updated_at: datetime


class ProcessingDetail(ProcessingSummary):
    draft_text: str | None
    text_format: Literal["markdown", "plain"]
    preview: DocumentPreview | None


class ReviewDetail(ProcessingValue):
    document: ManagedDocument
    candidate: ProcessingDetail
    latest_source: ProcessingSummary | None
    draft: ProcessingSummary | None


class VersionSummary(ProcessingValue):
    version_id: UUID
    processing_id: UUID
    revision: int
    title: str
    content_hash: str
    created_at: datetime


class VersionDetail(VersionSummary):
    preview: DocumentPreview
    metadata: dict
    processing_spec: dict


class ReviewDecision(ProcessingValue):
    review_id: UUID
    processing_id: UUID
    candidate_revision: int
    decision: str
    decision_source: str
    actor_id: UUID | None
    conclusion: str | None
    preview_fingerprint: str | None
    content_snapshot: dict
    created_at: datetime
