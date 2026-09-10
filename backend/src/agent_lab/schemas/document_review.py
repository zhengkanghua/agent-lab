"""统一管理与人工审核的 HTTP 输入；修订号防止覆盖较新的管理决定。"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent_lab.knowledge.processing.review_contracts import ManagedDocument, ProcessingSummary, ReviewDecision, VersionSummary


class ManagementRevisionRequest(BaseModel):
    management_revision: int = Field(ge=1, strict=True)
    model_config = ConfigDict(extra="forbid")


class StartReviewRequest(ManagementRevisionRequest):
    processing_id: UUID | None = None


class ReviewTargetRequest(ManagementRevisionRequest):
    candidate_revision: int = Field(ge=1, strict=True)


class SaveDraftRequest(ReviewTargetRequest):
    title: str
    text: str


class ReviewDecisionRequest(ReviewTargetRequest):
    conclusion: str | None = Field(default=None, max_length=2000)


class AdoptRequest(ReviewDecisionRequest):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ManagedDocumentList(BaseModel):
    items: list[ManagedDocument]
    has_more: bool


class ProcessingList(BaseModel):
    items: list[ProcessingSummary]
    has_more: bool


class VersionList(BaseModel):
    items: list[VersionSummary]
    has_more: bool


class ReviewDecisionList(BaseModel):
    items: list[ReviewDecision]
    has_more: bool
