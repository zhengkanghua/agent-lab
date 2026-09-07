"""Source 配置端口的 PostgreSQL 适配；数据库对象和异常不离开边界。"""

from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from agent_lab.knowledge.adapters.postgres import PostgresKnowledgeBaseRepository
from agent_lab.knowledge.contracts import SourceView
from agent_lab.knowledge.domain import SourceBindingError
from agent_lab.models.document import DocumentRecord
from agent_lab.models.source import SourceRecord
from agent_lab.models.write_operation import DocumentDeletionRecord


def _view(source: SourceRecord) -> SourceView:
    return SourceView(
        id=source.id, provider=source.provider, external_id=source.external_id,
        name=source.name, feed_url=source.feed_url, home_url=source.home_url,
        knowledge_base_id=source.knowledge_base_id,
        knowledge_base_key=source.knowledge_base.key if source.knowledge_base else None,
        sync_checkpoint=source.sync_checkpoint,
        sync_checkpoint_updated_at=source.sync_checkpoint_updated_at,
    )


class PostgresSourceBindingRepository:
    """读取纯快照，更新仅覆盖绑定与同步基线字段。"""

    def __init__(self, session):
        self._session = session

    async def list(self) -> list[SourceView]:
        records = await self._session.scalars(
            select(SourceRecord).options(selectinload(SourceRecord.knowledge_base))
            .order_by(SourceRecord.provider, SourceRecord.external_id)
        )
        return [_view(record) for record in records.all()]

    async def get_for_update(self, source_id: UUID) -> SourceView | None:
        record = await self._session.scalar(
            select(SourceRecord).options(selectinload(SourceRecord.knowledge_base))
            .where(SourceRecord.id == source_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        return _view(record) if record else None

    async def has_documents(self, source_id: UUID) -> bool:
        return bool(await self._session.scalar(select(exists().where(DocumentRecord.source_id == source_id))))

    async def has_pending_deletions(self, source_id: UUID) -> bool:
        return bool(await self._session.scalar(select(exists().where(
            DocumentDeletionRecord.document_id == DocumentRecord.id,
            DocumentRecord.source_id == source_id,
        ))))

    async def save_binding(self, source: SourceView) -> None:
        await self._session.execute(update(SourceRecord).where(SourceRecord.id == source.id).values(
            knowledge_base_id=source.knowledge_base_id,
            sync_checkpoint=source.sync_checkpoint,
            sync_checkpoint_updated_at=source.sync_checkpoint_updated_at,
        ))


class PostgresSourceBindingUnitOfWork:
    def __init__(self, session):
        self._session = session
        self.sources = PostgresSourceBindingRepository(session)
        self.knowledge_bases = PostgresKnowledgeBaseRepository(session)

    async def commit(self) -> None:
        await self._session.commit()


@asynccontextmanager
async def postgres_source_binding_work(session_factory):
    """Session 在所有退出路径先关闭；外层应用随后才能释放写资源。"""
    try:
        async with session_factory() as session:
            yield PostgresSourceBindingUnitOfWork(session)
    except SQLAlchemyError:
        raise SourceBindingError("source_database_unavailable", "来源存储当前不可用。") from None
