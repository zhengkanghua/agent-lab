"""使用短 PostgreSQL 事务实现知识库端口，ORM 不离开适配器。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import fields
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_lab.knowledge.contracts import KnowledgeBaseCreateRequest
from agent_lab.knowledge.domain import (
    KnowledgeBase,
    KnowledgeBaseKeyConflictError,
    KnowledgeBaseStorageError,
)
from agent_lab.knowledge.ports import KnowledgeBaseUnitOfWork
from agent_lab.models.knowledge_base import KnowledgeBaseRecord


def _snapshot(record: KnowledgeBaseRecord) -> KnowledgeBase:
    """在 Session 关闭前取出纯数据，避免调用方触发 ORM 懒加载。"""

    return KnowledgeBase(**{field.name: getattr(record, field.name) for field in fields(KnowledgeBase)})


class PostgresKnowledgeBaseRepository:
    """仅执行当前事务内的 SQL，提交由应用用例控制。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, *, include_inactive: bool) -> list[KnowledgeBase]:
        statement = select(KnowledgeBaseRecord).order_by(KnowledgeBaseRecord.key)
        if not include_inactive:
            statement = statement.where(KnowledgeBaseRecord.is_active.is_(True))
        return [_snapshot(record) for record in (await self._session.scalars(statement)).all()]

    async def get(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        record = await self._session.get(KnowledgeBaseRecord, knowledge_base_id)
        return _snapshot(record) if record is not None else None

    async def get_for_update(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        record = await self._session.scalar(
            select(KnowledgeBaseRecord)
            .where(KnowledgeBaseRecord.id == knowledge_base_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return _snapshot(record) if record is not None else None

    async def create(self, request: KnowledgeBaseCreateRequest) -> KnowledgeBase:
        # 唯一键的并发保护交给数据库，避免先查后插的竞争窗口。
        statement = (
            insert(KnowledgeBaseRecord)
            .values(id=uuid4(), **request.model_dump())
            .on_conflict_do_nothing(constraint="uq_knowledge_bases_key")
            .returning(KnowledgeBaseRecord)
        )
        record = await self._session.scalar(statement)
        if record is None:
            raise KnowledgeBaseKeyConflictError()
        return _snapshot(record)

    async def update(self, knowledge_base: KnowledgeBase) -> KnowledgeBase:
        was_active = await self._session.scalar(select(KnowledgeBaseRecord.is_active).where(
            KnowledgeBaseRecord.id == knowledge_base.id,
        ))
        statement = (
            update(KnowledgeBaseRecord)
            .where(KnowledgeBaseRecord.id == knowledge_base.id)
            .values(
                name=knowledge_base.name,
                description=knowledge_base.description,
                is_active=knowledge_base.is_active,
                updated_at=func.now(),
            )
            .returning(KnowledgeBaseRecord)
            .execution_options(populate_existing=True)
        )
        record = (await self._session.scalars(statement)).one()
        if not was_active and record.is_active:
            from agent_lab.knowledge.task_intake import ensure_document_processing
            await ensure_document_processing(self._session)
        return _snapshot(record)


class PostgresKnowledgeBaseUnitOfWork:
    """绑定当前 Session 与 Repository，不跨请求共享可变状态。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.repository = PostgresKnowledgeBaseRepository(session)

    async def commit(self) -> None:
        await self._session.commit()


@asynccontextmanager
async def postgres_knowledge_base_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[KnowledgeBaseUnitOfWork]:
    """未提交事务随 Session 关闭回滚；存储故障只以安全异常类型向外传播。"""

    try:
        async with session_factory() as session:
            yield PostgresKnowledgeBaseUnitOfWork(session)
    except SQLAlchemyError:
        raise KnowledgeBaseStorageError() from None
