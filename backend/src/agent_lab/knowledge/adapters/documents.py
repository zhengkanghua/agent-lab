"""把 PostgreSQL 当前文档转成纯快照，并提供索引工作单元。"""

from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import inspect

from agent_lab.domain.source_document import SourceInfo
from agent_lab.knowledge.document_contracts import DocumentSnapshot
from agent_lab.models.document import DocumentRecord
from agent_lab.repositories.document_repository import DocumentRepository


def document_snapshot(record: DocumentRecord) -> DocumentSnapshot:
    """要求来源已预加载，避免构造快照时触发隐式异步查询。"""
    if "source" in inspect(record).unloaded:
        raise ValueError("构建文档快照前必须先预加载 DocumentRecord.source。")
    source = record.source
    return DocumentSnapshot(
        id=record.id, knowledge_base_id=record.knowledge_base_id,
        source_id=record.source_id, external_id=record.external_id,
        source=SourceInfo(provider=source.provider, external_id=source.external_id, name=source.name,
                          feed_url=source.feed_url, home_url=source.home_url) if source else None,
        document_type=record.document_type, mime_type=record.mime_type,
        title=record.title, url=record.url, content_text=record.content_text,
        content_hash=record.content_hash, index_revision=record.index_revision,
        authors=tuple(record.authors), labels=tuple(record.labels),
        published_at=record.published_at, source_updated_at=record.source_updated_at,
    )


class PostgresIndexingRepository(DocumentRepository):
    """复用状态条件更新，仅把应用输入从 ORM 收紧为事务外可用的快照。"""

    async def get_for_indexing(self, document_id: UUID) -> DocumentSnapshot | None:
        record = await self.get_with_source(document_id)
        snapshot = document_snapshot(record) if record is not None else None
        await self._session.rollback()
        return snapshot


@asynccontextmanager
async def postgres_indexing_work(session_factory):
    async with session_factory() as session:
        yield PostgresIndexingRepository(session)
