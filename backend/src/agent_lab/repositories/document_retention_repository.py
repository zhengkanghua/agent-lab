"""完整文档删除和保留期选择；冻结原件清单后停止使用，逐项确认再清除历史。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select, tuple_, update
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.document_contracts import DocumentDeletion, RetentionCandidate
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.knowledge.storage import ObjectReference
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentReviewRecord, DocumentVersion
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord


def _deletion_snapshot(record: DocumentDeletionRecord) -> DocumentDeletion:
    return DocumentDeletion(record.document_id, record.revision, record.cutoff_date, record.retention_date,
                            record.qdrant_deleted, record.management_revision,
                            tuple(ObjectReference(item["key"], item["size"], item["sha256"], item["version_id"])
                                  for item in record.objects if not item["deleted"]))


def _eligible():
    """历史的待审核、失败和拒绝记录同样受保护，不能随旧正式版本到期被清除。"""
    return (
        (DocumentRecord.usage_status == "active")
        & DocumentRecord.current_version_id.is_not(None)
        & DocumentRecord.current_index_instance_id.is_not(None)
        & (DocumentRecord.processing_status == ProcessingStatus.INDEXED)
        & (DocumentRecord.indexed_revision == DocumentRecord.index_revision)
        & DocumentRecord.draft_processing_id.is_(None)
        & ~exists().where(DocumentProcessingRecord.document_id == DocumentRecord.id,
                          DocumentProcessingRecord.state.not_in(("adopted", "superseded", "rebuilt")))
    )


class DocumentRetentionRepository:
    def __init__(self, session) -> None:
        self._session = session

    async def candidates(self, cutoff: datetime, after: RetentionCandidate | None, limit: int, *, knowledge_base_ids: tuple[UUID, ...]):
        retention_date = func.coalesce(DocumentRecord.published_at, DocumentRecord.created_at)
        statement = select(DocumentRecord.id, DocumentRecord.index_revision, retention_date).where(
            _eligible(), DocumentRecord.knowledge_base_id.in_(knowledge_base_ids), retention_date < cutoff,
            ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
        )
        if after is not None:
            statement = statement.where(tuple_(retention_date, DocumentRecord.id) > tuple_(after.retention_date, after.document_id))
        rows = (await self._session.execute(statement.order_by(retention_date, DocumentRecord.id).limit(limit))).all()
        await self._session.rollback()
        return [RetentionCandidate(*row) for row in rows]

    async def pending(self, after: UUID | None, limit: int, *, knowledge_base_ids: tuple[UUID, ...]):
        statement = select(DocumentDeletionRecord).join(DocumentRecord, DocumentRecord.id == DocumentDeletionRecord.document_id).where(
            DocumentRecord.knowledge_base_id.in_(knowledge_base_ids), DocumentDeletionRecord.cutoff_date.is_not(None),
        ).order_by(DocumentDeletionRecord.document_id).limit(limit)
        if after is not None:
            statement = statement.where(DocumentDeletionRecord.document_id > after)
        result = [_deletion_snapshot(record) for record in (await self._session.scalars(statement)).all()]
        await self._session.rollback()
        return result

    async def _lock(self, document_id):
        kb_id = await self._session.scalar(select(DocumentRecord.knowledge_base_id).where(DocumentRecord.id == document_id))
        if kb_id is None:
            raise ProcessingApplicationError("document_processing_not_found")
        kb = await self._session.scalar(select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.id == kb_id).with_for_update())
        document = await self._session.scalar(select(DocumentRecord).where(DocumentRecord.id == document_id
        ).with_for_update().execution_options(populate_existing=True))
        if document is None:
            raise ProcessingApplicationError("document_processing_not_found")
        return kb, document

    async def _prepare(self, kb, document, cutoff):
        """记录已确认原件和未确认接收意图；后者可能已经完成 S3 写入。"""
        references = set()
        for model in (DocumentProcessingRecord, DocumentVersion):
            rows = await self._session.execute(select(model.source_object_key, model.source_object_version,
                                                       model.source_sha256, model.source_size).where(model.document_id == document.id))
            references.update(tuple(row) for row in rows if row[0] is not None)
        document.usage_status = "deleting"
        document.management_revision += 1
        document.updated_at = datetime.now(UTC)
        kb.visibility_revision += 1
        record = DocumentDeletionRecord(
            document_id=document.id, revision=document.index_revision, management_revision=document.management_revision,
            cutoff_date=cutoff, retention_date=document.published_at or document.created_at, qdrant_deleted=False,
            objects=[{"key": key, "version_id": version, "sha256": digest, "size": size, "deleted": False}
                     for key, version, digest, size in sorted(references, key=lambda row: (row[0], row[1] or ""))],
        )
        self._session.add(record)
        return record

    async def prepare(self, candidates: list[RetentionCandidate], cutoff: datetime):
        records = []
        for candidate in candidates:
            kb, document = await self._lock(candidate.document_id)
            valid = await self._session.scalar(select(DocumentRecord.id).where(
                DocumentRecord.id == candidate.document_id, DocumentRecord.index_revision == candidate.revision,
                _eligible(), func.coalesce(DocumentRecord.published_at, DocumentRecord.created_at) < cutoff,
                ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
            ))
            if valid is not None:
                records.append(await self._prepare(kb, document, cutoff))
        await self._session.commit()
        return [_deletion_snapshot(record) for record in records]

    async def prepare_explicit(self, document_id, *, revision, management_revision, require_file=False):
        kb, document = await self._lock(document_id)
        if document.index_revision != revision or document.management_revision != management_revision:
            raise ProcessingApplicationError("document_processing_conflict")
        if require_file and (document.source_id is not None or document.upload_filename is None):
            raise ProcessingApplicationError("document_not_uploaded")
        record = await self._session.get(DocumentDeletionRecord, document_id)
        if record is None:
            record = await self._prepare(kb, document, None)
        snapshot = _deletion_snapshot(record)
        await self._session.commit()
        return snapshot

    async def verify(self, records: list[DocumentDeletion]):
        for record in records:
            _, document = await self._lock(record.document_id)
            if (document.usage_status != "deleting" or document.index_revision != record.revision
                    or document.management_revision != record.management_revision):
                raise ProcessingApplicationError("document_deletion_conflict")
        await self._session.rollback()

    async def mark_qdrant_deleted(self, records: list[DocumentDeletion]):
        await self._session.execute(update(DocumentDeletionRecord).where(
            DocumentDeletionRecord.document_id.in_([record.document_id for record in records]),
        ).values(qdrant_deleted=True))
        await self._session.commit()

    async def mark_object_deleted(self, document_id, reference):
        record = await self._session.scalar(select(DocumentDeletionRecord).where(
            DocumentDeletionRecord.document_id == document_id,
        ).with_for_update())
        if record is None:
            raise ProcessingApplicationError("document_deletion_conflict")
        record.objects = [dict(item, deleted=True) if (item["key"], item["version_id"]) == (reference.key, reference.version_id)
                          else dict(item) for item in record.objects]
        await self._session.commit()

    async def finish(self, records: list[DocumentDeletion]) -> int:
        """所有远端步骤确认后才删除历史；任何数据库失败都保留整个文档和独立待办。"""
        count = 0
        for snapshot in records:
            kb, document = await self._lock(snapshot.document_id)
            record = await self._session.get(DocumentDeletionRecord, snapshot.document_id)
            if (record is None or not record.qdrant_deleted or any(not item["deleted"] for item in record.objects)
                    or document.usage_status != "deleting" or document.index_revision != snapshot.revision
                    or document.management_revision != snapshot.management_revision):
                raise ProcessingApplicationError("document_deletion_conflict")
            # 先断开当前指向，再按 RESTRICT 外键顺序删除结论、版本和候选。
            document.current_version_id = document.latest_processing_id = document.draft_processing_id = None
            await self._session.flush()
            for model in (DocumentReviewRecord, DocumentVersion, DocumentProcessingRecord):
                await self._session.execute(delete(model).where(model.document_id == document.id))
            await self._session.delete(document)
            await self._session.delete(record)
            kb.visibility_revision += 1
            count += 1
        await self._session.commit()
        return count

    async def record_error(self, records, error_type):
        await self._session.rollback()
        await self._session.execute(update(DocumentDeletionRecord).where(
            DocumentDeletionRecord.document_id.in_([record.document_id for record in records]),
        ).values(error_type=error_type))
        await self._session.commit()


@asynccontextmanager
async def postgres_deletion_work(session_factory):
    try:
        async with session_factory() as session:
            yield DocumentRetentionRepository(session)
    except SQLAlchemyError:
        raise ProcessingApplicationError("document_processing_storage_unavailable") from None
