"""上传资料的纯数据与端口；文件名只标识展示信息，Document ID 才是替换目标。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.document_contracts import DocumentDeletion
from agent_lab.knowledge.processing.lifecycle import SourceIntake

MAX_FILE_BYTES = 2 * 1024 * 1024
FILE_MIME_TYPES = {".txt": "text/plain", ".md": "text/markdown"}


class FileDocumentError(Exception):
    """稳定业务错误码；HTTP 文案和状态码由错误契约层统一解释。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TextFile:
    """只完成入口格式和体积校验的原件；正文解码属于持久接收之后的处理。"""

    filename: str
    title: str
    mime_type: str
    raw_bytes: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class FileDocument:
    """文件管理列表的当前快照，不包含全文或基础设施对象。"""

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
    management_revision: int = 1
    processing_id: UUID | None = None
    candidate_revision: int | None = None
    candidate_state: str | None = None
    candidate_error: str | None = None
    current_version_id: UUID | None = None
    usage_status: str = "active"


class FileDocumentRepository(Protocol):
    async def list(self, *, knowledge_base_id: UUID | None, offset: int, limit: int) -> list[FileDocument]: ...
    async def create(self, file: TextFile, knowledge_base_id: UUID, intake: SourceIntake) -> FileDocument: ...
    async def replace(self, document_id: UUID, file: TextFile, revision: int,
                      management_revision: int, intake: SourceIntake) -> FileDocument: ...
    async def retry(self, document_id: UUID, revision: int, management_revision: int) -> FileDocument: ...
    async def prepare_deletion(self, document_id: UUID, revision: int) -> DocumentDeletion: ...
    async def mark_qdrant_deleted(self, records: list[DocumentDeletion]) -> None: ...
    async def finish(self, records: list[DocumentDeletion]) -> int: ...
    async def record_error(self, records: list[DocumentDeletion], error_type: str) -> None: ...


FileDocumentWork = Callable[[], AbstractAsyncContextManager[FileDocumentRepository]]
