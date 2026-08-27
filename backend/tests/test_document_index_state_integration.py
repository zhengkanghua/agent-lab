"""显式启用后验证真实 PostgreSQL 的索引 revision 和状态条件更新。"""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from news_vector_service.db.session import async_session_factory, engine
from news_vector_service.domain.enums import DocumentType, ProcessingStatus
from news_vector_service.domain.source_document import (
    ImageReference,
    SourceDocument,
    SourceInfo,
)
from news_vector_service.models.document import DocumentRecord
from news_vector_service.models.source import SourceRecord
from news_vector_service.repositories.document_repository import DocumentRepository
from news_vector_service.repositories.source_repository import SourceRepository


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INDEX_STATE_INTEGRATION_TEST") != "1",
    reason=(
        "set RUN_POSTGRES_INDEX_STATE_INTEGRATION_TEST=1 to verify the configured "
        "PostgreSQL index state transitions"
    ),
)


def test_real_postgresql_revision_serializes_document_versions() -> None:
    """创建临时业务记录验证状态流转，并在成功或失败后清理。"""

    async def verify() -> None:
        suffix = uuid4().hex
        provider = f"integration_{suffix}"
        source_external_id = f"feed/{suffix}"
        document_external_id = f"article/{suffix}"
        source_id = None
        document_id = None
        try:
            async with async_session_factory() as session:
                source = await SourceRepository(session).upsert(
                    SourceInfo(
                        provider=provider,
                        external_id=source_external_id,
                        name="索引状态集成测试来源",
                    )
                )
                source_id = source.id
                original = SourceDocument(
                    external_id=document_external_id,
                    document_type=DocumentType.ARTICLE,
                    title="索引状态测试新闻",
                    url=f"https://example.com/{suffix}",
                    published_at=datetime(2026, 8, 13, tzinfo=UTC),
                    source=SourceInfo(
                        provider=provider,
                        external_id=source_external_id,
                        name="索引状态集成测试来源",
                    ),
                    content_text="第一版正文",
                )
                repository = DocumentRepository(session)
                record = await repository.upsert(original, source_id=source.id)
                await session.commit()
                document_id = record.id
                assert record.index_revision == 1
                assert await repository.claim_for_indexing(
                    document_id=record.id,
                    expected_revision=1,
                )

                changed = original.model_copy(
                    update={"title": "索引状态测试新闻（更新）"}
                )
                updated = await repository.upsert(changed, source_id=source.id)
                await session.commit()
                assert updated.index_revision == 2
                assert updated.processing_status == ProcessingStatus.PROCESSING

                assert not await repository.mark_indexed(
                    document_id=record.id,
                    index_revision=1,
                    content_hash=record.content_hash,
                    schema_version="v1",
                )
                assert await repository.release_stale_claim(
                    document_id=record.id,
                    stale_revision=1,
                )
                assert await repository.claim_for_indexing(
                    document_id=record.id,
                    expected_revision=2,
                )
                assert await repository.mark_indexed(
                    document_id=record.id,
                    index_revision=2,
                    content_hash=updated.content_hash,
                    schema_version="v1",
                )

                final_record = await session.scalar(
                    select(DocumentRecord).where(DocumentRecord.id == record.id)
                )
                assert final_record is not None
                assert final_record.processing_status == ProcessingStatus.INDEXED
                assert final_record.indexed_revision == 2
                assert final_record.indexed_schema_version == "v1"

                image_only = changed.model_copy(
                    update={
                        "images": (
                            ImageReference(url="https://example.com/image.jpg"),
                        )
                    }
                )
                image_updated = await repository.upsert(
                    image_only,
                    source_id=source.id,
                )
                await session.commit()
                assert image_updated.image_urls == ["https://example.com/image.jpg"]
                assert image_updated.index_revision == 2
                assert image_updated.processing_status == ProcessingStatus.INDEXED
        finally:
            async with async_session_factory() as cleanup_session:
                if document_id is not None:
                    await cleanup_session.execute(
                        delete(DocumentRecord).where(DocumentRecord.id == document_id)
                    )
                if source_id is not None:
                    await cleanup_session.execute(
                        delete(SourceRecord).where(SourceRecord.id == source_id)
                    )
                await cleanup_session.commit()
            await engine.dispose()

    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)
