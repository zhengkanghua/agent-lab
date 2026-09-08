"""显式授权后在随机 schema 验证迁移、JSONB 参数、行锁和通用文档持久化。"""

import asyncio
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import httpx
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, func, select, text, update
from sqlalchemy.exc import DBAPIError

from agent_lab.api.documents import get_document_repository
from agent_lab.agent.checkpointer import to_psycopg_conninfo
from agent_lab.config.scheduler import SchedulerSettings
from agent_lab.domain.enums import DocumentType
from agent_lab.domain.source_document import SourceDocument, SourceInfo
from agent_lab.knowledge.adapters.documents import PostgresIndexingRepository
from agent_lab.knowledge.adapters.importing import postgres_import_work
from agent_lab.knowledge.adapters.sources import postgres_source_binding_work
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID, KnowledgeBaseInactiveError
from agent_lab.knowledge.importing import SourceImportService
from agent_lab.models.document import DocumentRecord
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.source import SourceRecord
from agent_lab.models.scheduled_job import JobRunRecord, ScheduledJobRecord
from agent_lab.models.write_operation import DocumentDeletionRecord, WriteOperationRecord
from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.repositories.scheduled_job_repository import ScheduledJobStore
from agent_lab.services.scheduled_job_service import ScheduledJobService
from agent_lab.services.scheduler_runner import ScheduledJobRunner
from agent_lab.services.source_binding_service import SourceBindingService
from agent_lab.services.write_coordination import WriteCoordinator
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_reader
from tests.test_scheduler_postgres_integration import database, isolated_database, run


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason="需要显式授权的随机 PostgreSQL schema 验收。",
)


def test_retention_params_and_run_snapshot_persist_jsonb(isolated_database):
    async def verify():
        db = isolated_database
        runner = ScheduledJobRunner(
            store_factory=lambda: ScheduledJobStore(db.sessions), write_runtime_factory=lambda: None,
            settings=SchedulerSettings(_env_file=None),
        )
        other = uuid4()
        async with db.sessions() as session:
            session.add(KnowledgeBaseRecord(id=other, key="tech", name="Tech"))
            await session.commit()
        for params, expected in [({}, [str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)]), (
            {"knowledge_base_ids": [str(other), str(other), str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)]},
            [str(other), str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)],
        )]:
            async with db.sessions() as session:
                view = await ScheduledJobService(session, runner).create_job(
                    key=f"retention-{uuid4().hex}", task_type="prune_old_documents",
                    cron_expr="0 0 * * *", params=params, enabled=False,
                )
                job_id = view.record.id
            async with db.sessions() as session:
                assert (await session.get(ScheduledJobRecord, job_id)).params["knowledge_base_ids"] == expected
                await ScheduledJobService(session, runner).update_job(job_id, params={**params, "retention_days": 60})
            _, run_id = await ScheduledJobStore(db.sessions).claim_run(
                job_id, trigger_type="manual", owner="knowledge-test", started_at=datetime.now(UTC),
            )
            async with db.sessions() as session:
                snapshot = (await session.get(JobRunRecord, run_id)).config_snapshot
                assert snapshot["params"]["knowledge_base_ids"] == expected
                assert snapshot["params"]["retention_days"] == 60

    run(verify())


@pytest.mark.parametrize("operation", ["bind", "save_page"])
def test_disable_commit_wins_over_waiting_source_write(isolated_database, operation):
    async def verify():
        db = isolated_database
        source_id = uuid4()
        info = SourceInfo(provider="synthetic", external_id="feed/1", name="Synthetic")
        async with db.sessions() as session:
            session.add(SourceRecord(
                id=source_id, provider=info.provider, external_id=info.external_id, name=info.name,
                knowledge_base_id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID if operation == "save_page" else None,
                sync_checkpoint="1",
            ))
            await session.commit()

        binding = SourceBindingService(partial(postgres_source_binding_work, db.sessions), WriteCoordinator(db.sessions))
        importer = SourceImportService(None, partial(postgres_import_work, db.sessions))
        entered = asyncio.Event()

        async def waiting_write():
            entered.set()
            if operation == "bind":
                return await binding.bind(source_id, DEFAULT_NEWS_KNOWLEDGE_BASE_ID)
            return await importer.save_source_page(
                existing_source_id=source_id, expected_checkpoint="1", new_checkpoint="2",
                documents=[SourceDocument(external_id="2", title="New", url="https://example.invalid/2", source=info, content_text="new")],
            )

        async with db.sessions() as disabling:
            await disabling.execute(update(KnowledgeBaseRecord).where(
                KnowledgeBaseRecord.id == DEFAULT_NEWS_KNOWLEDGE_BASE_ID,
            ).values(is_active=False))
            task = asyncio.create_task(waiting_write())
            try:
                await entered.wait()
                # 两条路径都应在真实行锁处等待，直到停用事务提交。
                await asyncio.sleep(0.2)
                assert not task.done()
                await disabling.commit()
                if operation == "bind":
                    with pytest.raises(KnowledgeBaseInactiveError):
                        await asyncio.wait_for(task, 8)
                else:
                    assert await asyncio.wait_for(task, 8) == (0, False)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        async with db.sessions() as session:
            source = await session.get(SourceRecord, source_id)
            assert source.sync_checkpoint == "1"
            assert source.knowledge_base_id == (DEFAULT_NEWS_KNOWLEDGE_BASE_ID if operation == "save_page" else None)
            assert await session.scalar(select(func.count()).select_from(DocumentRecord)) == 0
            assert await session.scalar(select(func.count()).select_from(WriteOperationRecord)) == 0

    run(verify())


def test_generic_document_persists_and_snapshot_survives_rollback(isolated_database):
    async def verify():
        db = isolated_database
        knowledge_id, document_id = uuid4(), uuid4()
        async with db.sessions() as session:
            session.add(KnowledgeBaseRecord(id=knowledge_id, key="notes", name="Notes"))
            await session.flush()
            session.add(DocumentRecord(
                id=document_id, knowledge_base_id=knowledge_id, source_id=None, external_id=None,
                title="Internal note", url=None, document_type=DocumentType.OTHER, mime_type="text/markdown",
                content_text="# Internal note", content_hash=sha256(b"# Internal note").hexdigest(),
            ))
            await session.commit()
        async with db.sessions() as session:
            snapshot = await PostgresIndexingRepository(session).get_for_indexing(document_id)
            assert not session.in_transaction()
        assert snapshot.knowledge_base_id == knowledge_id and snapshot.source is None
        assert snapshot.mime_type == "text/markdown" and snapshot.url is None
        app = allow_reader(create_offline_app())

        async def repository():
            async with db.sessions() as session:
                yield DocumentRepository(session)

        app.dependency_overrides[get_document_repository] = repository
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            detail = await client.get(f"/documents/{document_id}")
            assert detail.status_code == 200
            assert detail.json()["knowledge_base_id"] == str(knowledge_id)
            assert detail.json()["mime_type"] == "text/markdown"
            assert detail.json()["url"] is None and detail.json()["source_name"] is None

    run(verify())


def test_upgrade_from_previous_head_preserves_nonknowledge_records():
    async def verify():
        schema = f"scheduler_test_{uuid4().hex}"
        engine, _ = database(os.environ["SCHEDULER_TEST_DATABASE_URL"], schema)
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))

        def upgrade(connection, revision):
            config.attributes["connection"] = connection
            command.upgrade(config, revision)

        def downgrade(connection, revision):
            config.attributes["connection"] = connection
            command.downgrade(config, revision)

        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.run_sync(upgrade, "f1a8c3d9e602")
                metadata = MetaData()
                await connection.run_sync(metadata.reflect)
                tables = metadata.tables
                user_id, thread_id, source_id, document_id = uuid4(), uuid4(), uuid4(), uuid4()
                await connection.execute(tables["users"].insert().values(id=user_id, email="review@example.invalid", hashed_password="synthetic"))
                await connection.execute(tables["access_tokens"].insert().values(
                    token="synthetic-token", user_id=user_id, created_at=datetime.now(UTC),
                ))
                await connection.execute(tables["agent_threads"].insert().values(
                    thread_id=thread_id, user_id=user_id, title="Retained", created_at=datetime.now(UTC), last_active_at=datetime.now(UTC),
                ))
                await connection.execute(tables["sources"].insert().values(id=source_id, provider="synthetic", external_id="feed/1", name="Synthetic"))
                await connection.execute(tables["documents"].insert().values(
                    id=document_id, source_id=source_id, external_id="1", document_type="article", title="Existing",
                    url="https://example.invalid/1", content_text="existing", content_hash=sha256(b"existing").hexdigest(),
                ))
                job_id = await connection.scalar(select(tables["scheduled_jobs"].c.id).limit(1))
                await connection.execute(tables["scheduled_job_runs"].insert().values(
                    id=uuid4(), job_id=job_id, trigger_type="manual", status="succeeded", started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
                    stats={},
                ))
                preserved = ("users", "access_tokens", "agent_threads", "scheduled_jobs", "scheduled_job_runs")
                before = {name: list((await connection.execute(select(tables[name]))).mappings()) for name in preserved}

            # checkpointer 自行管理 DDL 事务，但连接仍只看本测试的随机 schema。
            async with await AsyncConnection.connect(
                to_psycopg_conninfo(os.environ["SCHEDULER_TEST_DATABASE_URL"]),
                autocommit=True, row_factory=dict_row,
                options=f"-csearch_path={schema} -cstatement_timeout=10000", connect_timeout=5,
            ) as checkpoint_connection:
                saver = AsyncPostgresSaver(checkpoint_connection)
                await saver.setup()
                checkpoint = empty_checkpoint()
                checkpoint["channel_values"] = {"messages": [HumanMessage(content="Retained history", id="review-message")]}
                checkpoint["channel_versions"] = {"messages": 1}
                history_config = await saver.aput(
                    {"configurable": {"thread_id": str(thread_id), "checkpoint_ns": ""}},
                    checkpoint, {"source": "input", "step": 0, "parents": {}}, {"messages": 1},
                )
                await saver.aput_writes(history_config, [("messages", HumanMessage(content="Pending history", id="pending-message"))], "review-task")
                history_before = await saver.aget_tuple(history_config)
                assert history_before is not None and history_before.pending_writes

                async with engine.begin() as connection:
                    await connection.run_sync(upgrade, "head")
                assert await saver.aget_tuple(history_config) == history_before

            async with engine.begin() as connection:
                for name in preserved:
                    assert list((await connection.execute(select(tables[name]))).mappings()) == before[name]
                document = (await connection.execute(select(DocumentRecord.__table__))).mappings().one()
                assert document["knowledge_base_id"] == DEFAULT_NEWS_KNOWLEDGE_BASE_ID
                assert document["mime_type"] == "text/plain" and document["content_text"] == "existing"
                assert document["upload_filename"] is None
                assert await connection.scalar(select(SourceRecord.knowledge_base_id)) == DEFAULT_NEWS_KNOWLEDGE_BASE_ID
                assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "d63e0891f752"
                scope_query = text("SELECT scope FROM agent_threads WHERE thread_id = :thread_id")
                assert await connection.scalar(scope_query, {"thread_id": thread_id}) == {
                    "mode": "selected", "knowledge_base_ids": [str(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)],
                }
                new_thread_id = uuid4()
                await connection.execute(tables["agent_threads"].insert().values(
                    thread_id=new_thread_id, user_id=user_id, title="New", created_at=datetime.now(UTC), last_active_at=datetime.now(UTC),
                ))
                assert await connection.scalar(scope_query, {"thread_id": new_thread_id}) == {"mode": "all"}
                deletion_id = uuid4()
                await connection.execute(DocumentDeletionRecord.__table__.insert().values(
                    document_id=deletion_id, revision=1, cutoff_date=None, retention_date=datetime.now(UTC),
                ))

            # 人工待办不能在降级时失去删除语义；失败的整段 DDL 也须回滚。
            with pytest.raises(DBAPIError, match="请先完成上传文档的删除待办"):
                async with engine.begin() as connection:
                    await connection.run_sync(downgrade, "b38f9a7c6d21")
            async with engine.begin() as connection:
                assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "d63e0891f752"
                assert await connection.scalar(scope_query, {"thread_id": new_thread_id}) == {"mode": "all"}
                await connection.execute(DocumentDeletionRecord.__table__.delete().where(DocumentDeletionRecord.document_id == deletion_id))
                await connection.run_sync(downgrade, "b38f9a7c6d21")
                assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "b38f9a7c6d21"
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()

    run(verify())


@pytest.mark.skipif(os.getenv("RUN_SCHEDULER_QDRANT_INTEGRATION_TEST") != "1", reason="还需要隔离 Qdrant 写入授权。")
def test_persisted_generic_documents_rebuild_into_remote_qdrant(isolated_database):
    """真实 PG 和随机 Qdrant generation；Embedding 的网络响应仍用确定性替身。"""
    from unittest.mock import AsyncMock
    from qdrant_client import models
    from agent_lab.config.qdrant import QdrantSettings
    from agent_lab.knowledge.adapters.postgres import postgres_knowledge_base_work
    from agent_lab.knowledge.adapters.rebuilding import PostgresRebuildRepository
    from agent_lab.knowledge.application import KnowledgeBaseService
    from agent_lab.knowledge.rebuilding import IndexRebuildService
    from agent_lab.pipeline.document_chunk_pipeline import DocumentChunkPipeline
    from agent_lab.pipeline.ollama_embedding_provider import OllamaEmbeddingProvider
    from agent_lab.qdrant.index_spec import VectorIndexSpec
    from agent_lab.qdrant.lifecycle import build_qdrant_client
    from agent_lab.qdrant.rebuilding import QdrantRebuildTarget
    from agent_lab.qdrant.search import QdrantVectorSearch
    from agent_lab.schemas.document_search import DocumentSearchRequest
    from agent_lab.services.vector_search_service import VectorSearchService
    from tests.test_vector_search import ollama_settings

    async def verify():
        db = isolated_database
        settings = QdrantSettings(
            _env_file=None, base_url=os.environ["SCHEDULER_TEST_QDRANT_URL"],
            api_key=os.getenv("SCHEDULER_TEST_QDRANT_API_KEY", ""),
            environment=db.schema, collection_schema_version="v2", collection_generation=2,
            vector_dimension=3,
        )
        spec = VectorIndexSpec(dimension=3)
        client = build_qdrant_client(settings)
        old_name = settings.model_copy(update={"collection_generation": 1}).collection_name
        knowledge_id = uuid4()
        async with db.sessions() as session:
            session.add(KnowledgeBaseRecord(id=knowledge_id, key="tech", name="Tech"))
            await session.flush()
            session.add_all([DocumentRecord(
                id=uuid4(), knowledge_base_id=target, source_id=None, external_id=None,
                title="Retained generic document", mime_type="text/markdown", document_type=DocumentType.OTHER,
                url=None, content_text="# Synthetic note", content_hash=sha256(b"# Synthetic note").hexdigest(),
                processing_status="indexed", indexed_revision=1, indexed_schema_version="v1",
            ) for target in (knowledge_id, DEFAULT_NEWS_KNOWLEDGE_BASE_ID)])
            await session.commit()
        provider = OllamaEmbeddingProvider(ollama_settings(), embeddings=SimpleNamespace(
            aembed_documents=AsyncMock(side_effect=lambda texts: [[1.0, 0.0, 0.0] for _ in texts]),
            aembed_query=AsyncMock(return_value=[1.0, 0.0, 0.0]),
        ))
        try:
            await client.create_collection(old_name, vectors_config=spec.vector_params, metadata=spec.collection_metadata)
            await client.update_collection_aliases([models.CreateAliasOperation(create_alias=models.CreateAlias(
                collection_name=old_name, alias_name=settings.collection_alias,
            ))])
            result = await IndexRebuildService(
                PostgresRebuildRepository(db.sessions),
                QdrantRebuildTarget(client, settings, spec, DocumentChunkPipeline(), provider),
                WriteCoordinator(db.sessions),
            ).rebuild(batch_size=1)
            assert result.document_count == 2 and result.point_count == 2
            info = await client.get_collection(settings.collection_name)
            assert info.payload_schema["knowledge_base_id"].data_type == models.PayloadSchemaType.UUID
            service = VectorSearchService(
                embedding_provider=provider, vector_search=QdrantVectorSearch(client, settings, spec), spec=spec,
                knowledge_base_scope=KnowledgeBaseService(partial(postgres_knowledge_base_work, db.sessions)),
            )
            for target in (knowledge_id, DEFAULT_NEWS_KNOWLEDGE_BASE_ID):
                results = await service.search_documents(DocumentSearchRequest(query="note", knowledge_base_id=target))
                assert len(results) == 1 and results[0].knowledge_base_id == target
                assert results[0].mime_type == "text/markdown" and results[0].source_name is None
            async with db.sessions() as session:
                documents = (await session.scalars(select(DocumentRecord))).all()
                assert all(document.indexed_revision == document.index_revision and document.indexed_schema_version == "v2" for document in documents)
                assert await session.scalar(select(func.count()).select_from(WriteOperationRecord)) == 0
        finally:
            # 名称全部由本测试随机 schema 派生，不能清理应用 Collection 或 Alias。
            await client.update_collection_aliases([models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=settings.collection_alias))])
            for name in (settings.collection_name, old_name):
                if await client.collection_exists(name):
                    await client.delete_collection(name)
            await provider.close()
            await client.close()

    run(verify())
