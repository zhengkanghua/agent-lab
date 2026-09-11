"""批量核验索引身份和可用性，不读取全文或枚举全库已采用文档。"""

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.knowledge.visibility import SearchVisibilityError, VisibilitySnapshot
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord


class PostgresDocumentVisibility:
    def __init__(self, session_factory):
        self._sessions = session_factory

    async def _revisions(self, session, ids):
        rows = (await session.execute(select(
            KnowledgeBaseRecord.id, KnowledgeBaseRecord.visibility_revision, KnowledgeBaseRecord.is_active,
        ).where(KnowledgeBaseRecord.id.in_(ids)).order_by(KnowledgeBaseRecord.id))).all()
        return tuple(tuple(row) for row in rows)

    async def snapshot(self, knowledge_base_ids):
        try:
            async with self._sessions() as session:
                # 修订与排除集合必须来自同一数据库快照，收尾核验使用另一短事务。
                await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                revisions = await self._revisions(session, knowledge_base_ids)
                if len(revisions) != len(set(knowledge_base_ids)) or not all(row[2] for row in revisions):
                    raise SearchVisibilityError()
                # Alias 与数据库指向没有共同事务；发布窗口给出可重试失败，不能误报空结果。
                publishing = await session.scalar(select(exists().where(
                    DocumentProcessingRecord.document_id == DocumentRecord.id,
                    DocumentRecord.knowledge_base_id.in_(knowledge_base_ids),
                    DocumentProcessingRecord.state == "publishing",
                )))
                if publishing:
                    raise SearchVisibilityError()
                # 只枚举尚在准备、已停用和待回收实例；清理确认后退出排除集合。
                excluded = await session.scalars(select(DocumentProcessingRecord.index_instance_id)
                    .join(DocumentRecord, DocumentRecord.id == DocumentProcessingRecord.document_id).where(
                        DocumentRecord.knowledge_base_id.in_(knowledge_base_ids),
                        DocumentProcessingRecord.index_instance_id.is_not(None),
                        DocumentProcessingRecord.index_deleted_at.is_(None),
                        or_(DocumentRecord.usage_status != "active",
                            DocumentRecord.current_index_instance_id.is_distinct_from(DocumentProcessingRecord.index_instance_id)),
                    ).order_by(DocumentProcessingRecord.index_instance_id))
                return VisibilitySnapshot(revisions, tuple(excluded.all()))
        except SQLAlchemyError:
            raise SearchVisibilityError() from None

    async def validate(self, snapshot, hits):
        try:
            async with self._sessions() as session:
                ids = {hit.document_id for hit in hits}
                current = {}
                if ids:
                    rows = (await session.execute(select(
                        DocumentRecord.id, DocumentRecord.knowledge_base_id, DocumentRecord.current_index_instance_id,
                        DocumentRecord.content_hash,
                    ).where(
                        DocumentRecord.id.in_(ids), DocumentRecord.current_version_id.is_not(None),
                        DocumentRecord.usage_status == "active",
                        ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
                    ))).all()
                    current = {row.id: (row.knowledge_base_id, row.current_index_instance_id, row.content_hash) for row in rows}
                # 放在命中核验之后读取修订，捕捉之前任一时刻发生的采用、拒绝或候选写入意图。
                revisions = await self._revisions(session, [row[0] for row in snapshot.revisions])
                return revisions == snapshot.revisions and all(
                    current.get(hit.document_id) == (hit.knowledge_base_id, hit.index_instance_id, hit.content_hash)
                    for hit in hits
                )
        except SQLAlchemyError:
            raise SearchVisibilityError() from None
