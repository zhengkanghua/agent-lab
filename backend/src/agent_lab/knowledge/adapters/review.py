"""人工审核的短 PostgreSQL 事务；锁顺序与采用统一，原件和索引 I/O 留在用例外层。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import case, exists, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased, load_only

from agent_lab.knowledge.adapters.adoption import lock_candidate, require_revisions
from agent_lab.knowledge.adapters.processing import new_processing_record
from agent_lab.knowledge.processing.contracts import DocumentPreview
from agent_lab.knowledge.processing.indexing import IndexTarget
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, ProcessingReceipt, SourceIntake
from agent_lab.knowledge.processing.review_contracts import (
    ManagedDocument, ProcessingDetail, ProcessingSummary, ReviewDecision, ReviewDetail, VersionDetail, VersionSummary,
)
from agent_lab.knowledge.storage import ObjectReference
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentReviewRecord, DocumentVersion
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord
from agent_lab.knowledge.task_intake import ensure_document_processing


def _summary(record):
    return ProcessingSummary(
        processing_id=record.id, document_id=record.document_id, source_kind=record.source_kind,
        state=record.state, title=record.source_metadata.get("title", ""),
        candidate_revision=record.candidate_revision, requires_review=record.requires_review,
        preview_fingerprint=record.preview_fingerprint, error_code=record.error_code,
        issue_codes=tuple(record.issue_codes), source_stored=record.source_stored_at is not None,
        source_sha256=record.source_sha256, created_at=record.created_at, updated_at=record.updated_at,
    )


def _preview(record):
    if record.parsed_document is None or record.chunk_result is None or record.preview_fingerprint is None:
        return None
    return DocumentPreview.model_validate({"document": record.parsed_document, "chunk_result": record.chunk_result})


def _format(record):
    return "plain" if (record.draft_mime_type or record.source_mime_type) == "text/plain" else "markdown"


def _decision_snapshot(record):
    body = record.draft_text if record.draft_text is not None else (record.parsed_document or {}).get("body")
    return {"title": record.source_metadata.get("title", ""), "body": body, "text_format": _format(record),
            "content_hash": sha256(body.encode()).hexdigest() if body is not None else None,
            "source_sha256": record.source_sha256}


def _receipt(record):
    return ProcessingReceipt(record.id, record.document_id, record.state, record.source_sha256 or "",
                             record.candidate_revision, record.error_code)


def _invalidate(record):
    record.parsed_document = record.chunk_result = record.preview_fingerprint = record.parser_id = None
    record.claim_token = record.claimed_at = record.error_code = None
    record.issue_codes = []


class PostgresReviewRepository:
    def __init__(self, session):
        self._session = session

    @staticmethod
    def _documents():
        latest, draft = aliased(DocumentProcessingRecord), aliased(DocumentProcessingRecord)
        return select(
            DocumentRecord.id.label("document_id"), DocumentRecord.knowledge_base_id,
            KnowledgeBaseRecord.name.label("knowledge_base_name"), KnowledgeBaseRecord.is_active.label("knowledge_base_active"),
            DocumentRecord.title, DocumentRecord.upload_filename,
            case((DocumentRecord.source_id.is_(None), "file"), else_="freshrss").label("source_kind"),
            DocumentRecord.usage_status, DocumentRecord.index_revision.label("revision"), DocumentRecord.management_revision,
            DocumentRecord.current_version_id, DocumentRecord.latest_processing_id, DocumentRecord.draft_processing_id,
            func.coalesce(draft.state, latest.state).label("processing_state"),
            func.coalesce(draft.error_code, latest.error_code).label("error_code"), DocumentRecord.updated_at,
            exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id).label("deletion_pending"),
        ).join(KnowledgeBaseRecord, KnowledgeBaseRecord.id == DocumentRecord.knowledge_base_id
        ).outerjoin(latest, latest.id == DocumentRecord.latest_processing_id
        ).outerjoin(draft, draft.id == DocumentRecord.draft_processing_id)

    async def list(self, *, knowledge_base_id, source_kind, state, offset, limit):
        statement = self._documents().order_by(DocumentRecord.updated_at.desc(), DocumentRecord.id).offset(offset).limit(limit)
        if knowledge_base_id is not None:
            statement = statement.where(DocumentRecord.knowledge_base_id == knowledge_base_id)
        if source_kind is not None:
            statement = statement.where(DocumentRecord.source_id.is_(None) if source_kind == "file" else DocumentRecord.source_id.is_not(None))
        if state is not None:
            statement = statement.where(exists().where(DocumentProcessingRecord.document_id == DocumentRecord.id,
                                                       DocumentProcessingRecord.state == state))
        return [ManagedDocument.model_validate(row._mapping) for row in (await self._session.execute(statement)).all()]

    async def detail(self, document_id, processing_id=None):
        row = (await self._session.execute(self._documents().where(DocumentRecord.id == document_id))).one_or_none()
        if row is None:
            raise ProcessingApplicationError("document_processing_not_found")
        document = ManagedDocument.model_validate(row._mapping)
        selected = processing_id or document.draft_processing_id or document.latest_processing_id
        record = await self._session.get(DocumentProcessingRecord, selected) if selected else None
        if record is None or record.document_id != document_id:
            raise ProcessingApplicationError("document_processing_not_found")
        latest = await self._session.get(DocumentProcessingRecord, document.latest_processing_id) if document.latest_processing_id else None
        draft = await self._session.get(DocumentProcessingRecord, document.draft_processing_id) if document.draft_processing_id else None
        return ReviewDetail(document=document, candidate=ProcessingDetail(
            **_summary(record).model_dump(), draft_text=record.draft_text, text_format=_format(record), preview=_preview(record),
        ), latest_source=_summary(latest) if latest else None, draft=_summary(draft) if draft else None)

    async def candidates(self, document_id, *, offset, limit):
        # 列表不读取正文或 Chunk JSON，完整候选仅在选中后读取。
        fields = [getattr(DocumentProcessingRecord, field) for field in (
            "id", "document_id", "source_kind", "state", "source_metadata", "candidate_revision", "requires_review",
            "preview_fingerprint", "error_code", "issue_codes", "source_stored_at", "source_sha256", "created_at", "updated_at",
        )]
        records = await self._session.scalars(select(DocumentProcessingRecord).options(load_only(*fields)).where(
            DocumentProcessingRecord.document_id == document_id,
        ).order_by(DocumentProcessingRecord.created_at.desc(), DocumentProcessingRecord.id).offset(offset).limit(limit))
        return [_summary(record) for record in records]

    async def _locked(self, processing_id, *, management_revision, candidate_revision, require_active=True):
        kb, document, record = await lock_candidate(self._session, processing_id, require_active=require_active)
        require_revisions(document, record, management_revision=management_revision, candidate_revision=candidate_revision)
        return kb, document, record

    async def _mutable_draft(self, document, source):
        """未审核草稿原位覆盖；已冻结或被审核的候选保留依据，再创建唯一最新草稿。"""
        if (source.state == "adoption_failed" and source.index_instance_id
                and source.index_instance_id != document.current_index_instance_id):
            source.index_cleanup_pending = source.index_deleted_at is None
        existing = await self._session.get(DocumentProcessingRecord, document.draft_processing_id) if document.draft_processing_id else None
        if existing is not None:
            if existing.state in {"adopting", "indexing"}:
                raise ProcessingApplicationError("document_processing_busy")
            reviewed = await self._session.scalar(select(exists().where(DocumentReviewRecord.processing_id == existing.id)))
            if existing.index_target is None and not reviewed:
                if source.index_cleanup_pending:
                    await ensure_document_processing(self._session)
                return existing
            if existing.index_instance_id and existing.index_instance_id != document.current_index_instance_id:
                existing.index_cleanup_pending = existing.index_deleted_at is None
            existing.draft_text = existing.draft_mime_type = None
        intake = SourceIntake(uuid4(), document.id, "manual", ObjectReference(
            source.source_object_key, source.source_size, source.source_sha256, source.source_object_version,
        ), source.source_mime_type, dict(source.source_metadata))
        draft = new_processing_record(intake, requires_review=True)
        draft.source_stored_at, draft.source_object_version = source.source_stored_at, source.source_object_version
        self._session.add(draft)
        await self._session.flush()
        document.draft_processing_id = draft.id
        if source.index_cleanup_pending or existing is not None and existing.index_cleanup_pending:
            await ensure_document_processing(self._session)
        return draft

    async def start(self, document_id, *, management_revision, processing_id=None, use_latest=False):
        selected = await self._session.scalar(select(DocumentRecord.latest_processing_id).where(DocumentRecord.id == document_id))
        if selected is None:
            raise ProcessingApplicationError("document_processing_not_found")
        _, document, latest = await lock_candidate(self._session, selected)
        if document.management_revision != management_revision:
            raise ProcessingApplicationError("document_processing_conflict")
        if document.draft_processing_id and not use_latest:
            return _receipt(await self._session.get(DocumentProcessingRecord, document.draft_processing_id))
        if use_latest:
            source = latest
        elif processing_id is not None:
            source = await self._session.get(DocumentProcessingRecord, processing_id)
        elif document.current_version_id is not None:
            version = await self._session.get(DocumentVersion, document.current_version_id)
            source = await self._session.get(DocumentProcessingRecord, version.processing_id)
        else:
            source = latest
        if source is None or source.document_id != document_id:
            raise ProcessingApplicationError("document_processing_not_found")
        if source.source_stored_at is None:
            raise ProcessingApplicationError("document_source_storage_failed")
        # 取副本后再取得可编辑记录，换用来源不会把旧草稿反向写回来源候选。
        preview = _preview(source)
        body = source.draft_text if source.draft_text is not None else preview.document.body if preview else ""
        metadata = dict(source.source_metadata)
        draft = await self._mutable_draft(document, source)
        for field in ("source_object_key", "source_object_version", "source_sha256", "source_size", "source_mime_type", "source_stored_at"):
            setattr(draft, field, getattr(source, field))
        draft.source_metadata, draft.draft_text = metadata, body
        draft.draft_mime_type = "text/plain" if _format(source) == "plain" else "text/markdown"
        draft.candidate_revision += 1
        draft.draft_revision += 1
        _invalidate(draft)
        draft.state = "review" if preview is not None else "draft"
        if preview is not None:
            draft.parsed_document, draft.chunk_result = preview.document.model_dump(mode="json"), preview.chunk_result.model_dump(mode="json")
            draft.parser_id, draft.preview_fingerprint = preview.document.parser, preview.fingerprint
            draft.issue_codes = [issue.code for issue in preview.issues]
        document.manual_review_required = True
        document.management_revision += 1
        document.updated_at = draft.updated_at = datetime.now(UTC)
        receipt = _receipt(draft)
        await self._session.commit()
        return receipt

    async def save(self, processing_id, *, management_revision, candidate_revision, title, text):
        _, document, record = await self._locked(processing_id, management_revision=management_revision, candidate_revision=candidate_revision)
        if document.draft_processing_id != record.id:
            raise ProcessingApplicationError("document_draft_required")
        if record.state in {"adopting", "indexing"}:
            raise ProcessingApplicationError("document_processing_busy")
        if record.draft_text == text and record.source_metadata.get("title", "") == title:
            return _receipt(record)
        # UTF-8 和 NUL 是实际解析边界；正文空白允许保存，预览会明确报质量问题。
        if "\0" in text or "\0" in title:
            raise ProcessingApplicationError("document_content_invalid")
        try:
            text.encode("utf-8")
            title.encode("utf-8")
        except UnicodeEncodeError:
            raise ProcessingApplicationError("document_encoding_invalid") from None
        mime_type = record.draft_mime_type
        draft = await self._mutable_draft(document, record)
        draft.draft_text, draft.draft_mime_type = text, mime_type
        draft.source_metadata = dict(draft.source_metadata) | {"title": title}
        draft.candidate_revision += 1
        draft.draft_revision += 1
        draft.state = "draft"
        _invalidate(draft)
        document.management_revision += 1
        document.updated_at = draft.updated_at = datetime.now(UTC)
        receipt = _receipt(draft)
        await self._session.commit()
        return receipt

    async def preview(self, processing_id, *, management_revision, candidate_revision):
        _, document, record = await self._locked(processing_id, management_revision=management_revision, candidate_revision=candidate_revision)
        if document.draft_processing_id != record.id:
            raise ProcessingApplicationError("document_draft_required")
        if record.state in {"adopting", "indexing"}:
            raise ProcessingApplicationError("document_processing_busy")
        if record.index_target is not None:
            # 采用失败后的冻结目标不可改写；即使正文未改，也能用新规格重新预览。
            body, mime_type = record.draft_text, record.draft_mime_type
            record = await self._mutable_draft(document, record)
            record.draft_text, record.draft_mime_type = body, mime_type
        if record.state not in {"pending", "processing"}:
            _invalidate(record)
            record.state, record.requires_review = "pending", True
            document.management_revision += 1
            document.updated_at = record.updated_at = datetime.now(UTC)
        receipt = _receipt(record)
        await ensure_document_processing(self._session)
        await self._session.commit()
        return receipt

    async def reject(self, processing_id, *, management_revision, candidate_revision, actor_id, conclusion):
        kb, document, record = await self._locked(processing_id, management_revision=management_revision,
                                                 candidate_revision=candidate_revision, require_active=False)
        self._session.add(DocumentReviewRecord(
            id=uuid4(), document_id=document.id, processing_id=record.id, candidate_revision=record.candidate_revision,
            decision="reject", decision_source="manual", actor_id=actor_id, conclusion=conclusion,
            preview_fingerprint=record.preview_fingerprint, content_snapshot=_decision_snapshot(record),
        ))
        # 在途采用的条件确认会看到 state 已变化；不得等网络写完再停止正式使用。
        await self._session.execute(update(DocumentProcessingRecord).where(
            DocumentProcessingRecord.document_id == document.id,
            DocumentProcessingRecord.state.in_(("adopting", "indexing", "pending", "processing", "ready", "draft", "review", "adoption_failed")),
        ).values(state="review", requires_review=True, claim_token=None, claimed_at=None))
        await self._session.execute(update(DocumentProcessingRecord).where(
            DocumentProcessingRecord.document_id == document.id, DocumentProcessingRecord.index_instance_id.is_not(None),
            DocumentProcessingRecord.index_deleted_at.is_(None),
        ).values(index_cleanup_pending=True))
        if record.state not in {"adopted", "rebuilding", "publishing", "rebuilt"}:
            record.state = "rejected"
        document.usage_status, document.manual_review_required = "rejected", True
        document.management_revision += 1
        document.updated_at = record.updated_at = datetime.now(UTC)
        kb.visibility_revision += 1
        receipt = _receipt(record)
        await ensure_document_processing(self._session)
        await self._session.commit()
        return receipt

    async def retry(self, processing_id, *, management_revision, candidate_revision, actor_id, index_spec):
        _, document, record = await self._locked(processing_id, management_revision=management_revision, candidate_revision=candidate_revision)
        if record.state in {"received", "receiving_failed"}:
            return _receipt(record)
        if record.state == "stored":
            raise ProcessingApplicationError("document_source_waiting_sync")
        if record.index_target is not None:
            if record.state not in {"adoption_failed", "indexing"} or document.usage_status == "rejected" or record.index_cleanup_pending or record.index_deleted_at:
                raise ProcessingApplicationError("document_draft_required")
            target = IndexTarget.model_validate(record.index_target)
            if target.index_spec != index_spec:
                raise ProcessingApplicationError("document_index_spec_changed")
            if document.current_version_id != target.base_version_id:
                raise ProcessingApplicationError("document_adoption_conflict")
            record.state, record.error_code = "adopting", None
        else:
            if record.state not in {"failed", "review"}:
                raise ProcessingApplicationError("document_processing_busy")
            if record.source_stored_at is None:
                raise ProcessingApplicationError("document_source_storage_failed")
            _invalidate(record)
            record.state = "pending"
            record.candidate_revision += 1
            record.requires_review = record.requires_review or document.manual_review_required or document.usage_status == "rejected"
        self._session.add(DocumentReviewRecord(
            id=uuid4(), document_id=document.id, processing_id=record.id, candidate_revision=record.candidate_revision,
            decision="retry", decision_source="manual", actor_id=actor_id, preview_fingerprint=record.preview_fingerprint,
            content_snapshot=_decision_snapshot(record),
        ))
        document.management_revision += 1
        document.updated_at = record.updated_at = datetime.now(UTC)
        receipt = _receipt(record)
        await ensure_document_processing(self._session)
        await self._session.commit()
        return receipt

    async def original(self, processing_id):
        record = await self._session.get(DocumentProcessingRecord, processing_id)
        if record is None:
            raise ProcessingApplicationError("document_processing_not_found")
        if record.source_stored_at is None:
            raise ProcessingApplicationError("document_source_not_found")
        reference = ObjectReference(record.source_object_key, record.source_size, record.source_sha256, record.source_object_version)
        extension = {"text/html": "html", "text/markdown": "md", "text/plain": "txt"}.get(record.source_mime_type, "bin")
        return reference, record.source_metadata.get("filename") or f"{record.document_id}.{extension}"

    async def versions(self, document_id, *, offset, limit):
        rows = await self._session.execute(select(
            DocumentVersion.id.label("version_id"), DocumentVersion.processing_id, DocumentVersion.revision,
            DocumentVersion.title, DocumentVersion.content_hash, DocumentVersion.created_at,
        ).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.revision.desc()).offset(offset).limit(limit))
        return [VersionSummary.model_validate(row._mapping) for row in rows]

    async def version(self, document_id, version_id):
        record = await self._session.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id, DocumentVersion.document_id == document_id))
        if record is None:
            raise ProcessingApplicationError("document_processing_not_found")
        return VersionDetail(version_id=record.id, processing_id=record.processing_id, revision=record.revision,
                             title=record.title, content_hash=record.content_hash, created_at=record.created_at,
                             preview=DocumentPreview.model_validate({"document": record.parsed_document, "chunk_result": record.chunk_result}),
                             metadata=record.metadata_snapshot, processing_spec=record.processing_spec)

    async def decisions(self, document_id, *, offset, limit):
        records = await self._session.scalars(select(DocumentReviewRecord).where(DocumentReviewRecord.document_id == document_id
        ).order_by(DocumentReviewRecord.created_at.desc(), DocumentReviewRecord.id).offset(offset).limit(limit))
        return [ReviewDecision(review_id=record.id, processing_id=record.processing_id, candidate_revision=record.candidate_revision,
                               decision=record.decision, decision_source=record.decision_source, actor_id=record.actor_id,
                               conclusion=record.conclusion, preview_fingerprint=record.preview_fingerprint,
                               content_snapshot=record.content_snapshot, created_at=record.created_at) for record in records]


@asynccontextmanager
async def postgres_review_work(session_factory):
    try:
        async with session_factory() as session:
            yield PostgresReviewRepository(session)
    except SQLAlchemyError:
        raise ProcessingApplicationError("document_processing_storage_unavailable") from None
