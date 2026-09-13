"""只重建已采用的冻结清单；物理发布意图与正文历史分开保存。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import exists, select, update

from agent_lab.domain.write_scope import DocumentDeletionPendingError, WriteRecoveryRequiredError
from agent_lab.knowledge.document_contracts import RebuiltDocument
from agent_lab.knowledge.processing.contracts import DocumentPreview
from agent_lab.knowledge.processing.indexing import IndexMetadata, IndexTarget, require_preview_compatible
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.knowledge.storage import ObjectReference
from agent_lab.knowledge.task_intake import ensure_document_processing
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentVersion
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord


def _snapshot(record, target):
    location = record.index_location
    return RebuiltDocument(target.document_id, target.knowledge_base_id, location["revision"],
        target.content_hash, target.version_id, UUID(location["previous_index_instance_id"]),
        record.id, target.index_instance_id)


def _available():
    return (DocumentRecord.usage_status == "active",
            DocumentRecord.current_version_id.is_not(None),
            DocumentRecord.current_index_instance_id.is_not(None))


class PostgresRebuildRepository:
    """所有方法在返回前结束事务；Embedding 与 Qdrant 请求不占数据库连接。"""

    def __init__(self, session_factory):
        self._sessions = session_factory

    async def require_ready(self):
        async with self._sessions() as session:
            if await session.scalar(select(exists().select_from(DocumentDeletionRecord))):
                raise DocumentDeletionPendingError()
            if await session.scalar(select(exists().where(
                DocumentProcessingRecord.state.in_(("indexing", "publishing")),
            ))):
                raise WriteRecoveryRequiredError()

    async def list_documents(self, *, after, limit):
        statement = select(DocumentRecord.id).where(*_available()).order_by(DocumentRecord.id).limit(limit)
        if after is not None:
            statement = statement.where(DocumentRecord.id > after)
        async with self._sessions() as session:
            return list((await session.scalars(statement)).all())

    async def prepare_document(self, document_id, *, index_spec, location):
        """写 Point 前保存身份与原指向，失败 generation 的内容也始终可定位。"""
        async with self._sessions() as session:
            kb_id = await session.scalar(select(DocumentRecord.knowledge_base_id).where(DocumentRecord.id == document_id))
            kb = await session.scalar(select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.id == kb_id).with_for_update())
            document = await session.scalar(select(DocumentRecord).where(DocumentRecord.id == document_id).with_for_update())
            if document is None or document.usage_status != "active" or document.current_version_id is None:
                return None
            version = await session.get(DocumentVersion, document.current_version_id)
            origin = await session.get(DocumentProcessingRecord, version.processing_id)
            preview = DocumentPreview.model_validate({"document": version.parsed_document, "chunk_result": version.chunk_result})
            require_preview_compatible(preview, index_spec)
            target = IndexTarget(
                processing_id=uuid4(), document_id=document.id, knowledge_base_id=document.knowledge_base_id,
                candidate_revision=1, review_id=UUID(origin.index_target["review_id"]), version_id=version.id,
                index_instance_id=uuid4(), base_version_id=version.id,
                preview=preview, metadata=IndexMetadata.model_validate(version.metadata_snapshot),
                source=ObjectReference(version.source_object_key, version.source_size, version.source_sha256, version.source_object_version),
                index_spec=index_spec, manual=origin.index_target["manual"],
            )
            record = DocumentProcessingRecord(
                id=target.processing_id, document_id=document.id, source_kind="rebuild", state="rebuilding",
                candidate_revision=1, source_object_key=target.source.key, source_object_version=target.source.version_id,
                source_sha256=target.source.sha256, source_size=target.source.size,
                source_mime_type=origin.source_mime_type, source_stored_at=origin.source_stored_at,
                source_metadata=dict(origin.source_metadata),
                parsed_document=version.parsed_document, chunk_result=version.chunk_result,
                preview_fingerprint=preview.fingerprint, parser_id=preview.document.parser,
                index_instance_id=target.index_instance_id, index_target=target.model_dump(mode="json"),
                index_location=dict(location, revision=document.index_revision,
                    previous_index_instance_id=str(document.current_index_instance_id)),
                index_cleanup_pending=True,
            )
            session.add(record)
            kb.visibility_revision += 1
            snapshot = _snapshot(record, target)
            await ensure_document_processing(session)
            await session.commit()
            return snapshot, target

    async def mark_prepared(self, processing_id):
        async with self._sessions() as session:
            await session.execute(update(DocumentProcessingRecord).where(
                DocumentProcessingRecord.id == processing_id, DocumentProcessingRecord.state == "rebuilding",
            ).values(index_prepared_at=datetime.now(UTC)))
            await session.commit()

    async def _lock_documents(self, session, documents):
        # 与审核和采用同序锁定；一次发布涉及多个库，按 UUID 排序避免交叉等待。
        ids = sorted({document.knowledge_base_id for document in documents})
        bases = (await session.scalars(select(KnowledgeBaseRecord).where(
            KnowledgeBaseRecord.id.in_(ids)).order_by(KnowledgeBaseRecord.id).with_for_update())).all()
        records = (await session.scalars(select(DocumentRecord).where(
            DocumentRecord.id.in_([document.document_id for document in documents])
        ).order_by(DocumentRecord.id).with_for_update())).all()
        return bases, {record.id: record for record in records}

    @staticmethod
    def _require_unchanged(document, snapshot):
        if document is None or (
            document.knowledge_base_id, document.current_version_id, document.current_index_instance_id,
            document.index_revision, document.content_hash,
        ) != (snapshot.knowledge_base_id, snapshot.version_id, snapshot.previous_index_instance_id,
              snapshot.revision, snapshot.content_hash):
            raise WriteRecoveryRequiredError()

    async def begin_publication(self, documents):
        """先建立持久发布屏障；窗口内搜索明确失败，不返回被排除完的假空结果。"""
        async with self._sessions() as session:
            bases, records = await self._lock_documents(session, documents)
            available = set((await session.scalars(select(DocumentRecord.id).where(*_available()))).all())
            planned = {document.document_id for document in documents}
            if available - planned:
                raise WriteRecoveryRequiredError()
            for snapshot in documents:
                self._require_unchanged(records.get(snapshot.document_id), snapshot)
                record = await session.get(DocumentProcessingRecord, snapshot.processing_id)
                if record.state != "rebuilding" or record.index_prepared_at is None:
                    raise WriteRecoveryRequiredError()
                record.state = "publishing"
            for base in bases:
                base.visibility_revision += 1
            await session.commit()

    async def mark_rebuilt(self, documents, *, schema_version):
        """仅切换物理索引；拒绝可以并发发生，绝不因此恢复已拒绝资料。"""
        async with self._sessions() as session:
            bases, records = await self._lock_documents(session, documents)
            for snapshot in documents:
                document = records.get(snapshot.document_id)
                self._require_unchanged(document, snapshot)
                record = await session.get(DocumentProcessingRecord, snapshot.processing_id)
                if record.state != "publishing":
                    raise WriteRecoveryRequiredError()
                if document.usage_status == "active":
                    await session.execute(update(DocumentProcessingRecord).where(
                        DocumentProcessingRecord.index_instance_id == snapshot.previous_index_instance_id,
                    ).values(index_cleanup_pending=True))
                    document.current_index_instance_id = snapshot.index_instance_id
                    document.indexed_schema_version = schema_version
                    document.indexed_at = datetime.now(UTC)
                    record.state, record.index_cleanup_pending = "rebuilt", False
                else:
                    record.state, record.index_cleanup_pending = "superseded", True
            for base in bases:
                base.visibility_revision += 1
            await ensure_document_processing(session)
            await session.commit()

    async def fail_build(self, documents):
        async with self._sessions() as session:
            await session.execute(update(DocumentProcessingRecord).where(
                DocumentProcessingRecord.id.in_([document.processing_id for document in documents]),
                DocumentProcessingRecord.state == "rebuilding",
            ).values(state="rebuild_failed", error_code="document_rebuild_failed"))
            await ensure_document_processing(session)
            await session.commit()

    async def pending_publication(self, collection):
        async with self._sessions() as session:
            records = (await session.scalars(select(DocumentProcessingRecord).where(
                DocumentProcessingRecord.state == "publishing",
                DocumentProcessingRecord.index_location["collection"].astext == collection,
            ).order_by(DocumentProcessingRecord.document_id))).all()
            if not records:
                raise ProcessingApplicationError("document_rebuild_not_pending")
            return [(_snapshot(record, target), target, record.index_location)
                    for record in records for target in [IndexTarget.model_validate(record.index_target)]]

    async def abort_publication(self, documents):
        """人工核实 Alias 仍为原目标后结束失败发布，保留原正式指向和清理依据。"""
        async with self._sessions() as session:
            bases, records = await self._lock_documents(session, documents)
            for snapshot in documents:
                self._require_unchanged(records.get(snapshot.document_id), snapshot)
                record = await session.get(DocumentProcessingRecord, snapshot.processing_id)
                if record.state != "publishing":
                    raise WriteRecoveryRequiredError()
                record.state, record.error_code = "rebuild_failed", "document_rebuild_failed"
            for base in bases:
                base.visibility_revision += 1
            await ensure_document_processing(session)
            await session.commit()
