"""文件管理的公开契约；状态来自现有索引流程，不把保存成功当成索引成功。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
    content_hash: str
    revision: int
    updated_at: datetime
    processing_status: ProcessingStatus
    processing_error: str | None
    deletion_pending: bool
    deletion_error: str | None

    model_config = ConfigDict(from_attributes=True)


class FileDocumentListResponse(BaseModel):
    items: list[FileDocumentResponse]
    has_more: bool
    max_file_bytes: int = MAX_FILE_BYTES


class FileRevisionRequest(BaseModel):
    revision: int = Field(ge=1, strict=True, description="页面最近读取的文档业务版本。")
    model_config = ConfigDict(extra="forbid")
