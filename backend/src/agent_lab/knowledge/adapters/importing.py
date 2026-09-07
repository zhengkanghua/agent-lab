"""导入端口的 PostgreSQL 实现，复用原有幂等和 checkpoint 条件更新。"""

from contextlib import asynccontextmanager

from agent_lab.knowledge.adapters.postgres import PostgresKnowledgeBaseRepository
from agent_lab.knowledge.document_contracts import ImportSourceState
from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.repositories.source_repository import SourceRepository


def _state(record):
    return ImportSourceState(record.id, record.knowledge_base_id, record.sync_checkpoint) if record else None


class PostgresImportSourceRepository:
    def __init__(self, session):
        self._repository = SourceRepository(session)

    async def get_for_update(self, source_id):
        return _state(await self._repository.get_for_update(source_id))

    async def upsert(self, source):
        return _state(await self._repository.upsert(source))

    async def update_sync_checkpoint(self, **values):
        return await self._repository.update_sync_checkpoint(**values)


class PostgresImportDocumentRepository:
    def __init__(self, session):
        self._repository = DocumentRepository(session)

    async def upsert(self, document, *, source_id, knowledge_base_id):
        return (await self._repository.upsert(document, source_id=source_id, knowledge_base_id=knowledge_base_id)).id


class PostgresImportUnitOfWork:
    def __init__(self, session):
        self._session = session
        self.sources = PostgresImportSourceRepository(session)
        self.documents = PostgresImportDocumentRepository(session)
        self.knowledge_bases = PostgresKnowledgeBaseRepository(session)

    async def commit(self):
        await self._session.commit()

    async def rollback(self):
        await self._session.rollback()


@asynccontextmanager
async def postgres_import_work(session_factory):
    async with session_factory() as session:
        yield PostgresImportUnitOfWork(session)
