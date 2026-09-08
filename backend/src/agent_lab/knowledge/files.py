"""上传资料的纯数据与端口；文件名只标识展示信息，Document ID 才是替换目标。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.document_contracts import DocumentDeletion

MAX_FILE_BYTES = 2 * 1024 * 1024
FILE_MIME_TYPES = {".txt": "text/plain", ".md": "text/markdown"}


class FileDocumentError(Exception):
    """稳定业务错误码；HTTP 文案和状态码由错误契约层统一解释。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TextFile:
    filename: str
    title: str
    mime_type: str
    content_text: str
    content_hash: str


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
    content_hash: str
    revision: int
    updated_at: datetime
    processing_status: ProcessingStatus
    processing_error: str | None
    deletion_pending: bool
    deletion_error: str | None


class FileDocumentRepository(Protocol):
    async def list(self, *, knowledge_base_id: UUID | None, offset: int, limit: int) -> list[FileDocument]: ...
    async def create(self, file: TextFile, knowledge_base_id: UUID) -> FileDocument: ...
    async def replace(self, document_id: UUID, file: TextFile, revision: int) -> FileDocument: ...
    async def retry(self, document_id: UUID, revision: int) -> FileDocument: ...
    async def prepare_deletion(self, document_id: UUID, revision: int) -> DocumentDeletion: ...
    async def mark_qdrant_deleted(self, records: list[DocumentDeletion]) -> None: ...
    async def finish(self, records: list[DocumentDeletion]) -> int: ...
    async def record_error(self, records: list[DocumentDeletion], error_type: str) -> None: ...


FileDocumentWork = Callable[[], AbstractAsyncContextManager[FileDocumentRepository]]
