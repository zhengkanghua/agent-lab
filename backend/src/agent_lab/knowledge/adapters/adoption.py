"""PostgreSQL 采用事务；锁定顺序为 KnowledgeBase、Document、候选。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.processing.contracts import DocumentPreview
from agent_lab.knowledge.processing.indexing import IndexMetadata, IndexTarget
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, ProcessingReceipt
from agent_lab.knowledge.storage import ObjectReference
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentReviewRecord, DocumentVersion
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord


async def lock_candidate(session, processing_id: UUID, *, require_active: bool = True):
    """先定位再锁定，写入之前重新核验全部状态，避免 Session 外使用 ORM。"""
    identity = (await session.execute(select(DocumentRecord.knowledge_base_id, DocumentRecord.id)
        .join(DocumentProcessingRecord, DocumentProcessingRecord.document_id == DocumentRecord.id)
        .where(DocumentProcessingRecord.id == processing_id))).one_or_none()
    if identity is None:
        raise ProcessingApplicationError("document_processing_not_found")
    knowledge_base = await session.scalar(select(KnowledgeBaseRecord).where(
        KnowledgeBaseRecord.id == identity.knowledge_base_id,
    ).with_for_update())
    document = await session.scalar(select(DocumentRecord).where(
        DocumentRecord.id == identity.id,
    ).with_for_update().execution_options(populate_existing=True))
    record = await session.scalar(select(DocumentProcessingRecord).where(
        DocumentProcessingRecord.id == processing_id,
    ).with_for_update().execution_options(populate_existing=True))
    if document is None or record is None:
        raise ProcessingApplicationError("document_processing_not_found")
    if document.usage_status == "deleting" or await session.get(DocumentDeletionRecord, document.id):
        raise ProcessingApplicationError("document_deletion_pending")
    if require_active and not knowledge_base.is_active:
        raise ProcessingApplicationError("document_knowledge_base_inactive")
    return knowledge_base, document, record


def require_revisions(document, record, *, management_revision: int, candidate_revision: int):
    if document.management_revision != management_revision or record.candidate_revision != candidate_revision:
        raise ProcessingApplicationError("document_processing_conflict")


class PostgresAdoptionRepository:
    def __init__(self, session):
        self._session = session

    def _freeze(self, knowledge_base, document, record, *, index_spec, manual, actor_id=None, conclusion=None):
        if record.state not in {"review", "ready"} or record.index_target is not None:
            raise ProcessingApplicationError("document_processing_busy")
        if record.parsed_document is None or record.chunk_result is None:
            raise ProcessingApplicationError("document_preview_required")
        preview = DocumentPreview.model_validate({"document": record.parsed_document, "chunk_result": record.chunk_result})
        actual = preview.chunk_result.specification
        if (actual.algorithm, actual.tokenizer, actual.tokenizer_revision, actual.max_tokens, preview.document.parser) != (
            index_spec["chunk_algorithm"], index_spec["tokenizer"], index_spec["tokenizer_revision"],
            index_spec["chunk_size"], index_spec["parser_id"],
        ):
            raise ProcessingApplicationError("document_index_spec_changed")
        if (not preview.document.title.strip() or not preview.chunk_result.chunks
                or any(chunk.token_count > actual.max_tokens for chunk in preview.chunk_result.chunks)):
            raise ProcessingApplicationError("document_preview_invalid")
        if preview.fingerprint != record.preview_fingerprint:
            raise ProcessingApplicationError("document_preview_stale")
        metadata = {key: value for key, value in record.source_metadata.items() if key in IndexMetadata.model_fields}
        metadata["upload_filename"] = record.source_metadata.get("filename")
        metadata.setdefault("source_id", document.source_id)
        metadata.setdefault("document_external_id", document.external_id)
        target = IndexTarget(
            processing_id=record.id, document_id=document.id, knowledge_base_id=document.knowledge_base_id,
            candidate_revision=record.candidate_revision, review_id=uuid4(), version_id=uuid4(),
            index_instance_id=uuid4(), base_version_id=document.current_version_id,
            preview=preview, metadata=IndexMetadata.model_validate(metadata), index_spec=index_spec,
            source=ObjectReference(record.source_object_key, record.source_size, record.source_sha256, record.source_object_version),
            manual=manual,
        )
        record.index_target = target.model_dump(mode="json")
        record.index_instance_id = target.index_instance_id
        record.state, record.error_code = "adopting", None
        document.management_revision += 1
        if manual:
            document.manual_review_required = True
        # 排除记录先于任何 Point 存在；查询通过该修订检测期间发生的变化。
        knowledge_base.visibility_revision += 1
        self._session.add(DocumentReviewRecord(
            id=target.review_id, document_id=document.id, processing_id=record.id,
            candidate_revision=record.candidate_revision, decision="adopt",
            decision_source="manual" if manual else "automatic", actor_id=actor_id,
            conclusion=conclusion, preview_fingerprint=preview.fingerprint,
            content_snapshot={"title": preview.document.title, "body": preview.document.body,
                              "text_format": preview.document.text_format, "content_hash": target.content_hash,
                              "source_sha256": target.source.sha256},
        ))
        return target

    async def freeze(self, processing_id, *, candidate_revision, management_revision,
                     fingerprint, actor_id, conclusion, index_spec):
        knowledge_base, document, record = await lock_candidate(self._session, processing_id)
        require_revisions(document, record, management_revision=management_revision, candidate_revision=candidate_revision)
        if record.preview_fingerprint != fingerprint:
            raise ProcessingApplicationError("document_preview_stale")
        target = self._freeze(knowledge_base, document, record, index_spec=index_spec,
                              manual=True, actor_id=actor_id, conclusion=conclusion)
        await self._session.commit()
        return ProcessingReceipt(processing_id, document.id, "adopting", target.source.sha256, candidate_revision)

    async def claim_next(self, index_spec, processing_id=None):
        statement = select(DocumentProcessingRecord.id).join(
            DocumentRecord, DocumentRecord.id == DocumentProcessingRecord.document_id,
        ).join(KnowledgeBaseRecord, KnowledgeBaseRecord.id == DocumentRecord.knowledge_base_id).where(
            DocumentProcessingRecord.state.in_(("ready", "adopting")),
            DocumentRecord.usage_status != "deleting", KnowledgeBaseRecord.is_active.is_(True),
            ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
        ).order_by(DocumentProcessingRecord.updated_at, DocumentProcessingRecord.id).limit(1)
        if processing_id is not None:
            statement = statement.where(DocumentProcessingRecord.id == processing_id)
        identity = await self._session.scalar(statement)
        if identity is None:
            await self._session.rollback()
            return None
        knowledge_base, document, record = await lock_candidate(self._session, identity)
        if record.state == "ready":
            if record.requires_review or document.manual_review_required or document.usage_status == "rejected":
                record.state = "review"
                await self._session.commit()
                return ProcessingReceipt(record.id, document.id, record.state, record.source_sha256, record.candidate_revision)
            if document.latest_processing_id != record.id:
                record.state = "superseded"
                await self._session.commit()
                return ProcessingReceipt(record.id, document.id, record.state, record.source_sha256, record.candidate_revision)
            try:
                self._freeze(knowledge_base, document, record, index_spec=index_spec, manual=False)
            except ProcessingApplicationError as exc:
                # 规格变化或失效预览须进入人工处理，不能每次轮询再领同一条。
                record.state, record.error_code = "review", exc.code
                await self._session.commit()
                return ProcessingReceipt(record.id, document.id, record.state, record.source_sha256, record.candidate_revision, exc.code)
        if record.state != "adopting":
            await self._session.rollback()
            return None
        target = IndexTarget.model_validate(record.index_target)
        record.state = "indexing"
        await self._session.commit()
        return target

    async def complete(self, target: IndexTarget):
        knowledge_base, document, record = await lock_candidate(self._session, target.processing_id)
        if record.state != "indexing" or record.index_instance_id != target.index_instance_id:
            return False
        if (document.current_version_id != target.base_version_id or record.candidate_revision != target.candidate_revision
                or (not target.manual and (document.latest_processing_id != record.id or document.manual_review_required
                                           or document.usage_status == "rejected"))):
            return False
        now = datetime.now(UTC)
        revision = document.index_revision + 1 if document.current_version_id is not None else 1
        version = DocumentVersion(
            id=target.version_id, document_id=document.id, processing_id=record.id, revision=revision,
            title=target.preview.document.title, mime_type=target.mime_type,
            content_text=target.preview.document.body, content_hash=target.content_hash,
            parsed_document=target.preview.document.model_dump(mode="json"),
            chunk_result=target.preview.chunk_result.model_dump(mode="json"), processing_spec=target.index_spec,
            metadata_snapshot=target.metadata.model_dump(mode="json"), source_object_key=target.source.key,
            source_object_version=target.source.version_id, source_sha256=target.source.sha256, source_size=target.source.size,
            index_instance_id=str(target.index_instance_id), indexed_at=now,
        )
        self._session.add(version)
        await self._session.flush()
        if document.current_index_instance_id:
            await self._session.execute(update(DocumentProcessingRecord).where(
                DocumentProcessingRecord.index_instance_id == document.current_index_instance_id,
            ).values(index_cleanup_pending=True))
        document.current_version_id, document.current_index_instance_id = target.version_id, target.index_instance_id
        document.content_text, document.content_hash = target.preview.document.body, target.content_hash
        document.title, document.mime_type = target.preview.document.title, target.mime_type
        for field in ("document_type", "url", "upload_filename", "published_at", "source_updated_at"):
            setattr(document, field, getattr(target.metadata, field))
        document.authors, document.labels = list(target.metadata.authors), list(target.metadata.labels)
        document.image_urls = list(target.metadata.image_urls)
        document.index_revision = document.indexed_revision = revision
        document.indexed_content_hash, document.indexed_schema_version = target.content_hash, target.index_spec["schema_version"]
        document.processing_status = ProcessingStatus.INDEXED
        document.indexed_at, document.last_processing_error = now, None
        document.processing_started_at = None
        document.usage_status = "active"
        document.management_revision += 1
        if document.draft_processing_id == record.id:
            document.draft_processing_id = None
        record.state, record.index_prepared_at, record.error_code = "adopted", now, None
        record.draft_text, record.draft_mime_type = None, None
        knowledge_base.visibility_revision += 1
        await self._session.commit()
        return True

    async def fail(self, target: IndexTarget, code: str):
        await self._session.execute(update(DocumentProcessingRecord).where(
            DocumentProcessingRecord.id == target.processing_id,
            DocumentProcessingRecord.index_instance_id == target.index_instance_id,
            DocumentProcessingRecord.state == "indexing",
        ).values(state="adoption_failed", error_code=code))
        await self._session.commit()

    async def next_cleanup(self):
        row = (await self._session.execute(select(DocumentProcessingRecord.document_id, DocumentProcessingRecord.index_instance_id)
            .join(DocumentRecord, DocumentRecord.id == DocumentProcessingRecord.document_id).where(
                DocumentProcessingRecord.index_cleanup_pending.is_(True), DocumentProcessingRecord.index_deleted_at.is_(None),
                DocumentProcessingRecord.index_instance_id.is_not(None),
                ((DocumentRecord.current_index_instance_id.is_distinct_from(DocumentProcessingRecord.index_instance_id))
                 | (DocumentRecord.usage_status == "rejected")),
                DocumentRecord.usage_status != "deleting",
            ).order_by(DocumentProcessingRecord.updated_at).limit(1))).one_or_none()
        return tuple(row) if row else None

    async def mark_cleaned(self, document_id, index_instance_id):
        processing_id = await self._session.scalar(select(DocumentProcessingRecord.id).where(
            DocumentProcessingRecord.document_id == document_id, DocumentProcessingRecord.index_instance_id == index_instance_id,
        ))
        if processing_id is None:
            return
        knowledge_base, document, record = await lock_candidate(self._session, processing_id, require_active=False)
        if document.current_index_instance_id == index_instance_id and document.usage_status == "active":
            raise ProcessingApplicationError("document_adoption_conflict")
        record.index_cleanup_pending, record.index_deleted_at = False, datetime.now(UTC)
        knowledge_base.visibility_revision += 1
        await self._session.commit()


@asynccontextmanager
async def postgres_adoption_work(session_factory):
    try:
        async with session_factory() as session:
            yield PostgresAdoptionRepository(session)
    except SQLAlchemyError:
        raise ProcessingApplicationError("document_processing_storage_unavailable") from None
