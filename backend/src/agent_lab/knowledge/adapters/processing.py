"""处理记录的短事务适配器；原件、解析器与向量服务均留在事务外。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.knowledge.processing.contracts import DocumentPreview
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, ProcessingClaim, SourceIntake
from agent_lab.knowledge.storage import ObjectReference
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord
from agent_lab.knowledge.adapters.pending_work import pending_query
from agent_lab.knowledge.task_intake import ensure_document_processing


def new_processing_record(intake: SourceIntake, *, requires_review: bool = False) -> DocumentProcessingRecord:
    """由接收事务保存；上传和来源入口可把身份、意图与自己的事务一起提交。"""
    return DocumentProcessingRecord(
        id=intake.id, document_id=intake.document_id, source_kind=intake.source_kind,
        state="received", source_object_key=intake.reference.key,
        source_sha256=intake.reference.sha256, source_size=intake.reference.size,
        source_mime_type=intake.mime_type, source_metadata=dict(intake.metadata),
        candidate_revision=1, draft_revision=0, requires_review=requires_review,
        issue_codes=[], created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )


def _not_deleting():
    return exists().where(
        DocumentRecord.id == DocumentProcessingRecord.document_id,
        DocumentRecord.usage_status != "deleting",
        ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
    )


def _claim_matches(claim: ProcessingClaim):
    return (
        (DocumentProcessingRecord.id == claim.id)
        & (DocumentProcessingRecord.candidate_revision == claim.candidate_revision)
        & (DocumentProcessingRecord.claim_token == claim.claim_token)
        & (DocumentProcessingRecord.state == "processing")
        & _not_deleting()
    )


class PostgresProcessingRepository:
    def __init__(self, session):
        self._session = session

    async def create_intent(self, intake: SourceIntake):
        document = await self._session.scalar(select(DocumentRecord).where(
            DocumentRecord.id == intake.document_id,
        ).with_for_update())
        if document is None:
            raise ProcessingApplicationError("document_processing_not_found")
        if document.usage_status == "deleting" or await self._session.get(DocumentDeletionRecord, document.id):
            raise ProcessingApplicationError("document_deletion_pending")
        self._session.add(new_processing_record(intake, requires_review=document.usage_status == "rejected"))
        await self._session.flush()
        document.latest_processing_id = intake.id
        document.management_revision += 1
        await self._session.commit()

    async def mark_stored(self, processing_id: UUID, reference: ObjectReference, *, queue_processing: bool = True) -> bool:
        result = await self._session.execute(update(DocumentProcessingRecord).where(
            DocumentProcessingRecord.id == processing_id,
            DocumentProcessingRecord.state.in_(("received", "receiving_failed")),
            DocumentProcessingRecord.source_stored_at.is_(None),
            DocumentProcessingRecord.source_object_key == reference.key,
            DocumentProcessingRecord.source_sha256 == reference.sha256,
            DocumentProcessingRecord.source_size == reference.size,
            _not_deleting(),
        ).values(
            source_object_version=reference.version_id, source_stored_at=datetime.now(UTC),
            state="pending" if queue_processing else "stored", error_code=None, updated_at=datetime.now(UTC),
        ))
        if result.rowcount == 1 and queue_processing:
            await ensure_document_processing(self._session)
        await self._session.commit()
        return result.rowcount == 1

    async def get_receiving_intake(self, processing_id: UUID) -> SourceIntake | None:
        record = await self._session.scalar(select(DocumentProcessingRecord).where(
            DocumentProcessingRecord.id == processing_id,
            DocumentProcessingRecord.state.in_(("received", "receiving_failed")),
            DocumentProcessingRecord.source_stored_at.is_(None), _not_deleting(),
        ))
        if record is None:
            return None
        return SourceIntake(
            record.id, record.document_id, record.source_kind,
            ObjectReference(record.source_object_key, record.source_size, record.source_sha256, record.source_object_version),
            record.source_mime_type, dict(record.source_metadata),
        )

    async def mark_receiving_failure(self, processing_id: UUID, code: str):
        await self._session.execute(update(DocumentProcessingRecord).where(
            DocumentProcessingRecord.id == processing_id,
            DocumentProcessingRecord.state == "received",
            DocumentProcessingRecord.source_stored_at.is_(None),
        ).values(state="receiving_failed", error_code=code, updated_at=datetime.now(UTC)))
        await self._session.commit()

    async def claim(self, processing_id: UUID | None = None) -> ProcessingClaim | None:
        statement = pending_query().order_by(DocumentProcessingRecord.created_at, DocumentProcessingRecord.id).limit(1).with_for_update(skip_locked=True)
        if processing_id is not None:
            statement = statement.where(DocumentProcessingRecord.id == processing_id)
        record = await self._session.scalar(statement)
        if record is None:
            await self._session.rollback()
            return None
        record.state = "processing"
        record.claim_token = str(uuid4())
        record.claimed_at = datetime.now(UTC)
        snapshot = ProcessingClaim(
            id=record.id, document_id=record.document_id, candidate_revision=record.candidate_revision,
            claim_token=record.claim_token, source_object_key=record.source_object_key,
            source_object_version=record.source_object_version, source_sha256=record.source_sha256,
            source_size=record.source_size, source_mime_type=record.source_mime_type,
            title=record.source_metadata.get("title", "Document"), requires_review=record.requires_review,
            draft_text=record.draft_text, draft_mime_type=record.draft_mime_type,
        )
        await self._session.commit()
        return snapshot

    async def save_preview(self, claim: ProcessingClaim, preview: DocumentPreview, *, state: str) -> bool:
        result = await self._session.execute(update(DocumentProcessingRecord).where(_claim_matches(claim)).values(
            parser_id=preview.document.parser, parsed_document=preview.document.model_dump(mode="json"),
            chunk_result=preview.chunk_result.model_dump(mode="json"), preview_fingerprint=preview.fingerprint,
            issue_codes=[issue.code for issue in preview.issues], error_code=None, state=state,
            claim_token=None, claimed_at=None, updated_at=datetime.now(UTC),
        ))
        await self._session.commit()
        return result.rowcount == 1

    async def save_failure(self, claim: ProcessingClaim, code: str, *, state: str) -> bool:
        result = await self._session.execute(update(DocumentProcessingRecord).where(_claim_matches(claim)).values(
            state=state, error_code=code, claim_token=None, claimed_at=None, updated_at=datetime.now(UTC),
        ))
        await self._session.commit()
        return result.rowcount == 1

    async def get_preview(self, processing_id: UUID):
        record = await self._session.get(DocumentProcessingRecord, processing_id)
        if record is None or record.parsed_document is None or record.chunk_result is None:
            return None
        return DocumentPreview.model_validate({"document": record.parsed_document, "chunk_result": record.chunk_result})

    async def requeue_computations(self, *, started_before: datetime) -> int:
        """只回收无远端写入的解析计算；索引准备和接收未决写入不能按超时抢占。"""
        result = await self._session.execute(update(DocumentProcessingRecord).where(
            DocumentProcessingRecord.state == "processing",
            DocumentProcessingRecord.claimed_at < started_before,
            _not_deleting(),
        ).values(state="pending", claim_token=None, claimed_at=None, updated_at=datetime.now(UTC)))
        if result.rowcount:
            await ensure_document_processing(self._session)
        await self._session.commit()
        return result.rowcount


@asynccontextmanager
async def postgres_processing_work(session_factory):
    try:
        async with session_factory() as session:
            yield PostgresProcessingRepository(session)
    except SQLAlchemyError:
        raise ProcessingApplicationError("document_processing_storage_unavailable") from None
