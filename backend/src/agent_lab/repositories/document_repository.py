"""正式全文只读仓储；候选接收、审核、采用和删除分别由所属适配器管理。"""

from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from agent_lab.models.document import DocumentRecord
from agent_lab.models.write_operation import DocumentDeletionRecord


class DocumentRepository:
    """只返回当前可用的已采用正文，候选与管理历史不能通过普通全文入口读取。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_with_source(self, document_id: UUID) -> DocumentRecord | None:
        """一次查询预加载已采用元数据与归属，调用方无需隐式异步读取关系。"""
        return await self._session.scalar(
            select(DocumentRecord)
            .options(joinedload(DocumentRecord.current_version), selectinload(DocumentRecord.source),
                     selectinload(DocumentRecord.knowledge_base))
            .where(DocumentRecord.id == document_id, DocumentRecord.current_version_id.is_not(None),
                   DocumentRecord.usage_status == "active",
                   ~exists().where(DocumentDeletionRecord.document_id == DocumentRecord.id))
        )
