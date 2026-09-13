"""导入端口的 PostgreSQL 实现，复用原有幂等和 checkpoint 条件更新。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import exists, select, update

from agent_lab.knowledge.adapters.postgres import PostgresKnowledgeBaseRepository
from agent_lab.knowledge.adapters.processing import new_processing_record
from agent_lab.knowledge.document_contracts import ImportSourceState
from agent_lab.knowledge.processing.indexing import IndexMetadata
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, SourceIntake, SourceReception
from agent_lab.knowledge.storage import ObjectReference
from agent_lab.domain.enums import ProcessingStatus
from agent_lab.domain.write_scope import DocumentDeletionPendingError
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord
from agent_lab.models.write_operation import DocumentDeletionRecord
from agent_lab.repositories.source_repository import SourceRepository
from agent_lab.knowledge.task_intake import ensure_document_processing


def _state(record):
    return ImportSourceState(record.id, record.knowledge_base_id, record.sync_checkpoint) if record else None


class PostgresImportSourceRepository:
    def __init__(self, session):
        self._session = session
        self._repository = SourceRepository(session)

    async def get_for_update(self, source_id):
        return _state(await self._repository.get_for_update(source_id))

    async def upsert(self, source):
        record = await self._repository.upsert(source)
        if record.knowledge_base_id is not None:
            await PostgresImportDocumentRepository(self._session).refresh_source_metadata(record, source)
        return _state(record)

    async def update_sync_checkpoint(self, **values):
        return await self._repository.update_sync_checkpoint(**values)


class PostgresImportDocumentRepository:
    def __init__(self, session):
        self._session = session

    @staticmethod
    def _reference(record):
        return ObjectReference(record.source_object_key, record.source_size, record.source_sha256, record.source_object_version)

    async def prepare(self, incoming, *, source_id, knowledge_base_id):
        """按 Source/外部 ID 保持 Document 身份；只修改候选指向，不覆盖正式正文。"""
        document = await self._session.scalar(select(DocumentRecord).where(
            DocumentRecord.source_id == source_id, DocumentRecord.external_id == incoming.external_id,
        ).with_for_update())
        created = document is None
        if document is None:
            document = DocumentRecord(
                id=uuid4(), source_id=source_id, knowledge_base_id=knowledge_base_id, external_id=incoming.external_id,
                document_type=incoming.document_type, title=incoming.title, url=str(incoming.url), mime_type=incoming.mime_type,
                content_text=None, content_hash=None, authors=[], labels=[], image_urls=[],
                processing_status=ProcessingStatus.PENDING, index_revision=1, management_revision=1, usage_status="active",
            )
            self._session.add(document)
            await self._session.flush()
        if document.knowledge_base_id != knowledge_base_id:
            raise ProcessingApplicationError("document_processing_conflict")
        if document.usage_status == "deleting" or await self._session.get(DocumentDeletionRecord, document.id):
            raise DocumentDeletionPendingError()
        metadata = IndexMetadata(
            document_type=incoming.document_type, source_id=source_id, source_provider=incoming.source.provider,
            source_name=incoming.source.name, source_external_id=incoming.source.external_id,
            document_external_id=incoming.external_id, url=str(incoming.url), authors=incoming.authors,
            labels=incoming.labels, image_urls=tuple(str(image.url) for image in incoming.images),
            published_at=incoming.published_at, source_updated_at=incoming.source_updated_at,
        ).model_dump(mode="json") | {"title": incoming.title}
        latest = await self._session.get(DocumentProcessingRecord, document.latest_processing_id) if document.latest_processing_id else None
        same_bytes = latest is not None and (latest.source_sha256, latest.source_size, latest.source_mime_type) == (
            sha256(incoming.raw_bytes).hexdigest(), len(incoming.raw_bytes), incoming.mime_type,
        )
        if same_bytes and latest.source_metadata == metadata:
            return SourceReception(SourceIntake(latest.id, document.id, "freshrss", self._reference(latest),
                                                incoming.mime_type, metadata), latest.source_stored_at is not None)
        intake = SourceIntake.prepare(document_id=document.id, source_kind="freshrss", data=incoming.raw_bytes,
                                      mime_type=incoming.mime_type, metadata=metadata)
        if same_bytes and latest.source_stored_at is not None:
            intake = SourceIntake(intake.id, document.id, "freshrss", self._reference(latest), incoming.mime_type, metadata)
        record = new_processing_record(intake, requires_review=document.manual_review_required or document.usage_status == "rejected"
                                       or document.draft_processing_id is not None)
        if same_bytes and latest.source_stored_at is not None:
            record.state, record.source_stored_at = "stored", latest.source_stored_at
            record.source_object_version = latest.source_object_version
        self._session.add(record)
        await self._session.flush()
        document.latest_processing_id = record.id
        if not created:
            document.management_revision += 1
        document.updated_at = datetime.now(UTC)
        return SourceReception(intake, record.source_stored_at is not None)

    async def confirm_receptions(self, processing_ids, *, source_id, knowledge_base_id):
        """与 checkpoint 同事务排队；原件、归属与删除边界任一不符就不推进。"""
        if not processing_ids:
            return
        rows = list((await self._session.execute(select(DocumentProcessingRecord, DocumentRecord)
            .join(DocumentRecord, DocumentRecord.id == DocumentProcessingRecord.document_id).where(
                DocumentProcessingRecord.id.in_(processing_ids), DocumentRecord.source_id == source_id,
                DocumentRecord.knowledge_base_id == knowledge_base_id,
            ).order_by(DocumentRecord.id).with_for_update(of=DocumentRecord))).all())
        if len(rows) != len(set(processing_ids)) or any(record.source_stored_at is None for record, _ in rows):
            raise ProcessingApplicationError("document_source_storage_failed")
        for record, document in rows:
            if document.usage_status == "deleting" or await self._session.get(DocumentDeletionRecord, document.id):
                raise DocumentDeletionPendingError()
            if record.state == "stored":
                record.state = "pending"
        await ensure_document_processing(self._session)

    async def refresh_source_metadata(self, source_record, source):
        """来源改名也产生候选；原件和人工版本保留，只有采用才更新正式元数据。"""
        target = await PostgresKnowledgeBaseRepository(self._session).get_for_update(source_record.knowledge_base_id)
        if target is None or not target.is_active:
            return
        rows = (await self._session.execute(select(DocumentRecord, DocumentProcessingRecord)
            .join(DocumentProcessingRecord, DocumentProcessingRecord.id == DocumentRecord.latest_processing_id).where(
                DocumentRecord.source_id == source_record.id, DocumentRecord.usage_status != "deleting",
                ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
                DocumentProcessingRecord.source_metadata["source_name"].astext.is_distinct_from(source.name),
            ).order_by(DocumentRecord.id).with_for_update(of=DocumentRecord))).all()
        for document, latest in rows:
            metadata = dict(latest.source_metadata) | {"source_name": source.name}
            intake = SourceIntake(uuid4(), document.id, "freshrss", self._reference(latest), latest.source_mime_type, metadata)
            record = new_processing_record(intake, requires_review=document.manual_review_required or document.usage_status == "rejected"
                                           or document.draft_processing_id is not None)
            record.source_stored_at, record.source_object_version = latest.source_stored_at, latest.source_object_version
            record.state = "pending" if latest.source_stored_at is not None and latest.state != "stored" else latest.state
            if record.state not in {"pending", "stored", "received", "receiving_failed"}:
                record.state = "received"
            self._session.add(record)
            await self._session.flush()
            document.latest_processing_id = record.id
            document.management_revision += 1
        if rows:
            await ensure_document_processing(self._session)


class PostgresImportUnitOfWork:
    def __init__(self, session):
        self._session = session
        self.sources = PostgresImportSourceRepository(session)
        self.documents = PostgresImportDocumentRepository(session)
        self.knowledge_bases = PostgresKnowledgeBaseRepository(session)

    async def commit(self):
        await self._session.commit()

    async def rollback(self):
        await self._session.rollback()


@asynccontextmanager
async def postgres_import_work(session_factory):
    async with session_factory() as session:
        yield PostgresImportUnitOfWork(session)
