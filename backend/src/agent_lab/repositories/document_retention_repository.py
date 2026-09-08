"""旧 Document 的有游标遍历和删除意图，事务均在网络调用前后结束。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select, tuple_, update

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.models.document import DocumentRecord
from agent_lab.models.write_operation import DocumentDeletionRecord
from agent_lab.knowledge.document_contracts import DocumentDeletion, RetentionCandidate


def _deletion_snapshot(record: DocumentDeletionRecord) -> DocumentDeletion:
    return DocumentDeletion(record.document_id, record.revision, record.cutoff_date, record.retention_date, record.qdrant_deleted)


class DocumentRetentionRepository:
    def __init__(self, session) -> None:
        self._session = session

    async def candidates(
        self,
        cutoff: datetime,
        after: RetentionCandidate | None,
        limit: int,
        *,
        knowledge_base_ids: tuple[UUID, ...],
    ):
        retention_date = func.coalesce(DocumentRecord.published_at, DocumentRecord.created_at)
        statement = select(DocumentRecord.id, DocumentRecord.index_revision, retention_date).where(
            DocumentRecord.processing_status == ProcessingStatus.INDEXED,
            DocumentRecord.knowledge_base_id.in_(knowledge_base_ids),
            retention_date < cutoff,
            ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
        )
        if after is not None:
            statement = statement.where(tuple_(retention_date, DocumentRecord.id) > tuple_(after.retention_date, after.document_id))
        rows = (await self._session.execute(statement.order_by(retention_date, DocumentRecord.id).limit(limit))).all()
        result = [RetentionCandidate(*row) for row in rows]
        await self._session.rollback()
        return result

    async def pending(
        self,
        after: UUID | None,
        limit: int,
        *,
        knowledge_base_ids: tuple[UUID, ...],
    ):
        # 意图表本身没有归属字段，恢复范围经 document_id 回联归属行限定：
        # 范围外的未完成意图不会被本次任务处理，留给覆盖该库的实例恢复。
        statement = (
            select(DocumentDeletionRecord)
            .join(DocumentRecord, DocumentRecord.id == DocumentDeletionRecord.document_id)
            .where(DocumentRecord.knowledge_base_id.in_(knowledge_base_ids))
            .where(DocumentDeletionRecord.cutoff_date.is_not(None))
            .order_by(DocumentDeletionRecord.document_id)
            .limit(limit)
        )
        if after is not None:
            statement = statement.where(DocumentDeletionRecord.document_id > after)
        records = list((await self._session.scalars(statement)).all())
        snapshots = [_deletion_snapshot(record) for record in records]
        await self._session.rollback()
        return snapshots

    async def prepare(self, candidates: list[RetentionCandidate], cutoff: datetime):
        """复核状态与版本，先提交意图；之后同步/索引不得修改这些目标。"""
        records = []
        for candidate in candidates:
            document = await self._session.scalar(select(DocumentRecord).where(
                DocumentRecord.id == candidate.document_id,
                DocumentRecord.index_revision == candidate.revision,
                DocumentRecord.processing_status == ProcessingStatus.INDEXED,
                func.coalesce(DocumentRecord.published_at, DocumentRecord.created_at) < cutoff,
            ).with_for_update())
            if document is not None:
                record = DocumentDeletionRecord(
                    document_id=candidate.document_id, revision=candidate.revision,
                    cutoff_date=cutoff, retention_date=candidate.retention_date,
                    qdrant_deleted=False,
                )
                self._session.add(record)
                records.append(record)
        await self._session.commit()
        return [_deletion_snapshot(record) for record in records]

    async def finish(self, records: list[DocumentDeletion]) -> int:
        """Qdrant 已确认删除后，条件删除 Document 并移除待办；不盲删新版。"""
        count = 0
        for record in records:
            conditions = [
                DocumentRecord.id == record.document_id,
                DocumentRecord.index_revision == record.revision,
            ]
            if record.cutoff_date is None:
                conditions.extend([DocumentRecord.upload_filename.is_not(None), DocumentRecord.source_id.is_(None)])
            else:
                conditions.extend([
                    DocumentRecord.processing_status == ProcessingStatus.INDEXED,
                    func.coalesce(DocumentRecord.published_at, DocumentRecord.created_at) < record.cutoff_date,
                ])
            result = await self._session.execute(delete(DocumentRecord).where(*conditions))
            if not result.rowcount and await self._session.get(DocumentRecord, record.document_id) is not None:
                raise RuntimeError("删除目标版本或状态已改变，保留待办等待核实。")
            count += result.rowcount or 0
            await self._session.execute(delete(DocumentDeletionRecord).where(
                DocumentDeletionRecord.document_id == record.document_id,
            ))
        await self._session.commit()
        return count

    async def mark_qdrant_deleted(self, records: list[DocumentDeletion]) -> None:
        """在独立短事务保存远端确认，数据库删除失败后仍能识别这一步。"""
        await self._session.execute(update(DocumentDeletionRecord).where(
            DocumentDeletionRecord.document_id.in_([record.document_id for record in records]),
        ).values(qdrant_deleted=True))
        await self._session.commit()

    async def verify(self, records: list[DocumentDeletion]) -> None:
        """恢复待办也先核实当前数据；互斥保护持续到网络删除与数据库收尾结束。"""
        for record in records:
            document = await self._session.scalar(select(DocumentRecord).where(
                DocumentRecord.id == record.document_id,
            ).with_for_update())
            if document is not None:
                retention_date = document.published_at or document.created_at
                valid_target = (
                    document.upload_filename is not None and document.source_id is None
                    if record.cutoff_date is None else
                    document.processing_status == ProcessingStatus.INDEXED and retention_date < record.cutoff_date
                )
                if document.index_revision != record.revision or not valid_target:
                    await self._session.rollback()
                    raise RuntimeError("删除目标资格已改变，保留待办等待核实。")
        await self._session.rollback()

    async def record_error(self, records, error_type: str) -> None:
        await self._session.rollback()
        await self._session.execute(update(DocumentDeletionRecord).where(
            DocumentDeletionRecord.document_id.in_([record.document_id for record in records]),
        ).values(error_type=error_type))
        await self._session.commit()
