"""领取与批次交接共用的业务待办选择条件，避免空转或遗漏可继续资料。"""

from sqlalchemy import exists, or_, select

from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord


def pending_query():
    return select(DocumentProcessingRecord).where(
        DocumentProcessingRecord.state == "pending",
        DocumentProcessingRecord.source_stored_at.is_not(None),
        exists().where(
            DocumentRecord.id == DocumentProcessingRecord.document_id,
            DocumentRecord.usage_status != "deleting",
            ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
        ),
        exists().where(DocumentRecord.id == DocumentProcessingRecord.document_id,
            KnowledgeBaseRecord.id == DocumentRecord.knowledge_base_id, KnowledgeBaseRecord.is_active.is_(True)),
    )


def adoption_query():
    return select(DocumentProcessingRecord.id).join(DocumentRecord,
        DocumentRecord.id == DocumentProcessingRecord.document_id,
    ).join(KnowledgeBaseRecord, KnowledgeBaseRecord.id == DocumentRecord.knowledge_base_id).where(
        DocumentProcessingRecord.state.in_(("ready", "adopting")),
        DocumentRecord.usage_status != "deleting", KnowledgeBaseRecord.is_active.is_(True),
        ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id),
    )


def cleanup_query():
    return select(DocumentProcessingRecord.document_id, DocumentProcessingRecord.index_instance_id).join(
        DocumentRecord, DocumentRecord.id == DocumentProcessingRecord.document_id,
    ).where(
        DocumentProcessingRecord.index_cleanup_pending.is_(True), DocumentProcessingRecord.index_deleted_at.is_(None),
        DocumentProcessingRecord.index_instance_id.is_not(None), DocumentProcessingRecord.state != "publishing",
        (DocumentRecord.current_index_instance_id.is_distinct_from(DocumentProcessingRecord.index_instance_id))
            | (DocumentRecord.usage_status == "rejected"),
        DocumentRecord.usage_status != "deleting",
    )


async def has_pending_work(session):
    return bool(await session.scalar(select(or_(pending_query().exists(), adoption_query().exists(), cleanup_query().exists()))))
