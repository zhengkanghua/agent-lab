"""真实 PostgreSQL/Qdrant 重建发布、并发拒绝和人工恢复；仅写随机隔离资源。"""

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from agent_lab.domain.write_scope import WriteRecoveryRequiredError
from agent_lab.knowledge.adapters.rebuilding import PostgresRebuildRepository
from agent_lab.knowledge.adapters.text_files import parse_text_file
from agent_lab.knowledge.adapters.visibility import PostgresDocumentVisibility
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID as KB
from agent_lab.knowledge.rebuilding import IndexRebuildService
from agent_lab.knowledge.visibility import SearchVisibilityError
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentVersion
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import WriteOperationRecord
from agent_lab.qdrant.lifecycle import QdrantCollectionLifecycle
from agent_lab.qdrant.rebuilding import QdrantRebuildTarget
from agent_lab.scheduler_maintenance import inspect_or_recover
from agent_lab.schemas.vector_search import VectorSearchFilters
from agent_lab.services.write_coordination import WriteCoordinator
from tests.auth_helpers import SUPERUSER_ID
from tests.test_document_review_integration import actor, command
from tests.test_processing_application import processor
from tests.test_processing_postgres_integration import current, hits, scenario
from tests.test_scheduler_postgres_integration import isolated_database, run

pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1", reason="需要授权隔离重建验证。")


@asynccontextmanager
async def rebuild(app, db):
    settings = app.vectors.settings.model_copy(update={"collection_generation": 2})
    target = QdrantRebuildTarget(app.vectors.client, settings, app.spec, app.embeddings)
    repository = PostgresRebuildRepository(db.sessions)
    service = IndexRebuildService(repository, target, WriteCoordinator(db.sessions))
    try:
        yield service, repository, target
    finally:
        # 仅清理由本测试创建的第二代；退出后外层继续清理本测试原 Alias 与第一代。
        lifecycle = QdrantCollectionLifecycle(app.vectors.client, settings, app.spec)
        if await lifecycle.current_target() == settings.collection_name:
            await lifecycle.switch_current_alias(app.vectors.settings.collection_name)
        if await app.vectors.client.collection_exists(settings.collection_name):
            await app.vectors.client.delete_collection(settings.collection_name)


async def adopted(app, name, content, knowledge_base_id=KB):
    receipt = await app.files.upload(parse_text_file(name, content), knowledge_base_id)
    await app.processing.process(receipt.processing_id)
    assert (await app.adoption.process(receipt.processing_id)).state == "adopted"
    return receipt


def test_rebuild_keeps_history_and_rejection_wins_during_publication(isolated_database, processor):
    async def verify():
        db = isolated_database
        await actor(db)
        other_kb = uuid4()
        async with db.sessions() as session:
            session.add(KnowledgeBaseRecord(id=other_kb, key="manuals", name="操作资料", is_active=True))
            await session.commit()
        async with scenario(db, processor) as app:
            first = await adopted(app, "manual.md", "# 总标题\n\n## 子标题\n\n需要保留的正文。".encode())
            second = await adopted(app, "rejected.txt", b"body that will be rejected", other_kb)
            other_hits = await app.search.search_groups(
                [1.0, 0.0, 0.0], document_limit=10, matches_per_document=3,
                score_threshold=None, filters=VectorSearchFilters(knowledge_base_id=other_kb))
            assert {group.document_id for group in other_hits} == {second.document_id}
            await app.files.upload(parse_text_file("not-adopted.txt", b"pending source"), KB)
            old = await current(db, first.document_id)
            original_version = await app.review.version(old.id, old.current_version_id)
            visibility = PostgresDocumentVisibility(db.sessions)
            before = await visibility.snapshot((KB,))
            async with rebuild(app, db) as (service, _, target):
                publish = target.publish

                async def concurrent_reject():
                    with pytest.raises(SearchVisibilityError):
                        await hits(app)
                    assert not await visibility.validate(before, [])
                    detail = await app.review.detail(second.document_id)
                    await app.review.reject(second.processing_id, **command(detail), actor_id=SUPERUSER_ID, conclusion="重建期间拒绝")
                    await publish()
                    with pytest.raises(SearchVisibilityError):
                        await hits(app)

                target.publish = concurrent_reject
                result = await service.rebuild(batch_size=1)
                assert result.document_count == 2
                new = await current(db, old.id)
                assert (new.current_version_id, new.index_revision, new.content_hash, new.content_text) == (
                    old.current_version_id, old.index_revision, old.content_hash, old.content_text)
                assert new.current_index_instance_id != old.current_index_instance_id
                assert await app.review.version(new.id, new.current_version_id) == original_version
                assert {group.document_id for group in await hits(app)} == {first.document_id}
                assert (await current(db, second.document_id)).usage_status == "rejected"
                other_hits = await app.search.search_groups(
                    [1.0, 0.0, 0.0], document_limit=10, matches_per_document=3,
                    score_threshold=None, filters=VectorSearchFilters(knowledge_base_id=other_kb))
                assert other_hits == []
                # 旧 generation 与拒绝资料的新实例都能准确回收，成功资料的新实例保留。
                for _ in range(4):
                    if not await app.adoption.cleanup_one():
                        break
                assert (await app.vectors.client.count(app.vectors.settings.collection_name, exact=True)).count == 0
                assert {group.document_id for group in await hits(app)} == {first.document_id}
                async with db.sessions() as session:
                    assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 2
                    record = await session.scalar(select(DocumentProcessingRecord).where(
                        DocumentProcessingRecord.index_instance_id == new.current_index_instance_id))
                    assert record.state == "rebuilt" and not record.index_cleanup_pending
    run(verify())


@pytest.mark.parametrize("failure", ["alias", "database"])
def test_rebuild_publication_recovers_persisted_mapping_without_new_vectors(isolated_database, processor, monkeypatch, failure):
    async def verify():
        db = isolated_database
        async with scenario(db, processor) as app:
            first = await adopted(app, "guide.txt", b"adopted body")
            old = await current(db, first.document_id)
            async with rebuild(app, db) as (service, repository, target):
                with monkeypatch.context() as patch:
                    if failure == "alias":
                        patch.setattr(app.vectors.client, "update_collection_aliases", AsyncMock(side_effect=TimeoutError()))
                    else:
                        patch.setattr(repository, "mark_rebuilt", AsyncMock(side_effect=RuntimeError()))
                    with pytest.raises(WriteRecoveryRequiredError):
                        await service.rebuild()
                with pytest.raises(SearchVisibilityError):
                    await hits(app)
                async with db.sessions() as session:
                    operation = await session.scalar(select(WriteOperationRecord).where(WriteOperationRecord.status == "uncertain"))
                    assert operation is not None
                    operation_id = operation.id
                    # 执行已返回，故障写入替身也已停止；使用公开的人工核实恢复入口。
                    await session.execute(update(WriteOperationRecord).where(WriteOperationRecord.id == operation_id
                        ).values(heartbeat_at=datetime.now(UTC) - timedelta(minutes=2)))
                    await session.commit()
                await inspect_or_recover(db.sessions, operation_id=operation_id, confirm_stopped=True)
                app.embeddings.fail = True
                recovered = await service.recover()
                assert recovered.published == (failure == "database")
                new = await current(db, first.document_id)
                assert new.current_version_id == old.current_version_id and new.index_revision == old.index_revision
                assert (new.current_index_instance_id != old.current_index_instance_id) == recovered.published
                assert {group.document_id for group in await hits(app)} == {first.document_id}
                async with db.sessions() as session:
                    assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
    run(verify())
