"""上传 Document 的短事务适配器；替换沿用 revision 状态机，删除复用持久待办。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.domain.enums import DocumentType, ProcessingStatus
from agent_lab.knowledge.adapters.postgres import PostgresKnowledgeBaseRepository
from agent_lab.knowledge.domain import require_active_knowledge_base
from agent_lab.knowledge.files import FileDocument, FileDocumentError, TextFile
from agent_lab.models.document import DocumentRecord
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository, _deletion_snapshot


def _processing_error(value: str | None) -> str | None:
    """仅根据已保存的异常类型分类，不把异常正文回传浏览器。"""
    if not value:
        return None
    name = value.partition(":")[0]
    if "Embedding" in name or "Ollama" in name:
        return "文本向量化失败，请检查模型服务后重试。"
    if "Qdrant" in name:
        return "索引存储失败，请检查索引服务后重试。"
    if name == "ValueError":
        return "文本切分或索引数据无效，请核对文件后重试。"
    return "索引处理失败，请重试。"


def _view(document, knowledge_base, deletion=None) -> FileDocument:
    return FileDocument(
        document_id=document.id, knowledge_base_id=document.knowledge_base_id,
        knowledge_base_name=knowledge_base.name, knowledge_base_active=knowledge_base.is_active,
        upload_filename=document.upload_filename, title=document.title, mime_type=document.mime_type,
        content_hash=document.content_hash, revision=document.index_revision,
        updated_at=document.updated_at, processing_status=document.processing_status,
        processing_error=_processing_error(document.last_processing_error),
        deletion_pending=deletion is not None,
        deletion_error=(
            "索引已删除，文档删除尚未确认，请继续删除。" if deletion.qdrant_deleted
            else "索引删除尚未确认，请核实写入状态后继续删除。"
        ) if deletion is not None and deletion.error_type else None,
    )


class PostgresFileDocumentRepository:
    def __init__(self, session) -> None:
        self._session = session
        self._deletions = DocumentRetentionRepository(session)

    async def list(self, *, knowledge_base_id: UUID | None, offset: int, limit: int):
        statement = (
            select(DocumentRecord, KnowledgeBaseRecord, DocumentDeletionRecord)
            .join(KnowledgeBaseRecord, DocumentRecord.knowledge_base_id == KnowledgeBaseRecord.id)
            .outerjoin(DocumentDeletionRecord, DocumentDeletionRecord.document_id == DocumentRecord.id)
            .where(DocumentRecord.upload_filename.is_not(None))
            .order_by(DocumentRecord.updated_at.desc(), DocumentRecord.id)
            .offset(offset).limit(limit)
        )
        if knowledge_base_id is not None:
            statement = statement.where(DocumentRecord.knowledge_base_id == knowledge_base_id)
        return [_view(*row) for row in (await self._session.execute(statement)).all()]

    async def _active(self, knowledge_base_id):
        return require_active_knowledge_base(
            await PostgresKnowledgeBaseRepository(self._session).get_for_update(knowledge_base_id)
        )

    async def create(self, file: TextFile, knowledge_base_id: UUID):
        knowledge_base = await self._active(knowledge_base_id)
        now = datetime.now(UTC)
        document = DocumentRecord(
            id=uuid4(), knowledge_base_id=knowledge_base_id,
            source_id=None, external_id=None, source=None, url=None,
            document_type=DocumentType.OTHER, mime_type=file.mime_type,
            title=file.title, upload_filename=file.filename,
            content_text=file.content_text, content_hash=file.content_hash,
            published_at=None, source_updated_at=None, authors=[], labels=[], image_urls=[],
            processing_status=ProcessingStatus.PENDING, index_revision=1,
            created_at=now, updated_at=now,
        )
        self._session.add(document)
        await self._session.flush()
        view = _view(document, knowledge_base)
        await self._session.commit()
        return view

    async def _locked_upload(self, document_id, revision, *, allow_deletion=False):
        document = await self._session.scalar(select(DocumentRecord).where(
            DocumentRecord.id == document_id,
        ).with_for_update())
        if document is None:
            raise FileDocumentError("file_document_not_found")
        if document.upload_filename is None or document.source_id is not None:
            raise FileDocumentError("file_not_uploaded")
        if document.index_revision != revision:
            raise FileDocumentError("file_revision_conflict")
        deletion = await self._session.get(DocumentDeletionRecord, document_id)
        if deletion is not None and not allow_deletion:
            raise FileDocumentError("file_deletion_pending")
        return document, deletion

    async def replace(self, document_id: UUID, file: TextFile, revision: int):
        document, _ = await self._locked_upload(document_id, revision)
        knowledge_base = await self._active(document.knowledge_base_id)
        changed = (document.content_hash, document.title, document.mime_type, document.upload_filename) != (
            file.content_hash, file.title, file.mime_type, file.filename,
        )
        if changed:
            document.content_text = file.content_text
            document.content_hash = file.content_hash
            document.title = file.title
            document.mime_type = file.mime_type
            document.upload_filename = file.filename
            document.index_revision += 1
            document.updated_at = datetime.now(UTC)
            if document.processing_status != ProcessingStatus.PROCESSING:
                document.processing_status = ProcessingStatus.PENDING
                document.processing_started_at = None
                document.last_processing_error = None
        view = _view(document, knowledge_base)
        await self._session.commit()
        return view

    async def retry(self, document_id: UUID, revision: int):
        document, _ = await self._locked_upload(document_id, revision)
        knowledge_base = await self._active(document.knowledge_base_id)
        if document.processing_status == ProcessingStatus.PROCESSING:
            raise FileDocumentError("file_processing_busy")
        if document.processing_status == ProcessingStatus.FAILED:
            document.processing_status = ProcessingStatus.PENDING
            document.last_processing_error = None
            document.updated_at = datetime.now(UTC)
        view = _view(document, knowledge_base)
        await self._session.commit()
        return view

    async def prepare_deletion(self, document_id: UUID, revision: int):
        """人工删除可覆盖任何处理状态；仍须版本一致、明确上传身份和写资源互斥。"""
        document, deletion = await self._locked_upload(document_id, revision, allow_deletion=True)
        if deletion is not None:
            snapshot = _deletion_snapshot(deletion)
            await self._deletions.verify([snapshot])
            return snapshot
        deletion = DocumentDeletionRecord(
            document_id=document_id, revision=revision, cutoff_date=None,
            retention_date=document.published_at or document.created_at,
            qdrant_deleted=False,
        )
        self._session.add(deletion)
        await self._session.commit()
        return _deletion_snapshot(deletion)

    async def mark_qdrant_deleted(self, records):
        await self._deletions.mark_qdrant_deleted(records)

    async def finish(self, records):
        return await self._deletions.finish(records)

    async def record_error(self, records, error_type):
        await self._deletions.record_error(records, error_type)


@asynccontextmanager
async def postgres_file_work(session_factory):
    try:
        async with session_factory() as session:
            yield PostgresFileDocumentRepository(session)
    except SQLAlchemyError:
        raise FileDocumentError("file_storage_unavailable") from None
