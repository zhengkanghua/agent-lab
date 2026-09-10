"""文件管理区分正式版本与候选状态，保存成功仅表示原件与待办已持久化。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.files import MAX_FILE_BYTES


class FileDocumentResponse(BaseModel):
    document_id: UUID
    knowledge_base_id: UUID
    knowledge_base_name: str
    knowledge_base_active: bool
    upload_filename: str
    title: str
    mime_type: str
    content_hash: str | None
    revision: int
    updated_at: datetime
    processing_status: ProcessingStatus
    processing_error: str | None
    deletion_pending: bool
    deletion_error: str | None
    management_revision: int
    processing_id: UUID | None
    candidate_revision: int | None
    candidate_state: str | None
    candidate_error: str | None
    current_version_id: UUID | None
    usage_status: str

    model_config = ConfigDict(from_attributes=True)


class FileDocumentListResponse(BaseModel):
    items: list[FileDocumentResponse]
    has_more: bool
    max_file_bytes: int = MAX_FILE_BYTES
