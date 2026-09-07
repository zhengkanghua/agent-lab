"""PostgreSQL 重建快照适配器；不修改 Source 游标或其他业务领域的数据。"""

from datetime import UTC, datetime

from sqlalchemy import exists, select, update
from sqlalchemy.orm import selectinload

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.domain.write_scope import DocumentDeletionPendingError, WriteRecoveryRequiredError
from agent_lab.knowledge.adapters.documents import document_snapshot
from agent_lab.knowledge.document_contracts import RebuiltDocument
from agent_lab.models.document import DocumentRecord
from agent_lab.models.write_operation import DocumentDeletionRecord


class PostgresRebuildRepository:
    """每次方法使用独立短事务，不在 Embedding 或 Qdrant 请求期间占连接。"""

    def __init__(self, session_factory):
        self._sessions = session_factory

    async def require_ready(self):
        async with self._sessions() as session:
            if await session.scalar(select(exists().select_from(DocumentDeletionRecord))):
                raise DocumentDeletionPendingError()
            if await session.scalar(select(exists().where(
                DocumentRecord.processing_status == ProcessingStatus.PROCESSING,
            ))):
                raise WriteRecoveryRequiredError()

    async def list_documents(self, *, after, limit):
        statement = select(DocumentRecord).options(selectinload(DocumentRecord.source)).order_by(DocumentRecord.id).limit(limit)
        if after is not None:
            statement = statement.where(DocumentRecord.id > after)
        async with self._sessions() as session:
            return [document_snapshot(record) for record in (await session.scalars(statement)).all()]

    async def verify_versions(self, documents):
        await self.require_ready()
        async with self._sessions() as session:
            rows = (await session.execute(select(
                DocumentRecord.id, DocumentRecord.knowledge_base_id,
                DocumentRecord.index_revision, DocumentRecord.content_hash,
            ).order_by(DocumentRecord.id))).all()
            if [RebuiltDocument(*row) for row in rows] != list(documents):
                raise WriteRecoveryRequiredError()

    async def mark_rebuilt(self, documents, *, schema_version):
        async with self._sessions() as session:
            for document in documents:
                result = await session.execute(update(DocumentRecord).where(
                    DocumentRecord.id == document.document_id,
                    DocumentRecord.knowledge_base_id == document.knowledge_base_id,
                    DocumentRecord.index_revision == document.revision,
                    DocumentRecord.content_hash == document.content_hash,
                    DocumentRecord.processing_status != ProcessingStatus.PROCESSING,
                    ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
                ).values(
                    processing_status=ProcessingStatus.INDEXED,
                    indexed_revision=document.revision, indexed_content_hash=document.content_hash,
                    indexed_schema_version=schema_version, indexed_at=datetime.now(UTC),
                    processing_started_at=None, last_processing_error=None,
                ))
                if result.rowcount != 1:
                    raise WriteRecoveryRequiredError()
            await session.commit()
