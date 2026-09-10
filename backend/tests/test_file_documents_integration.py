"""上传资料的隔离完整链路与删除恢复；必须显式授权 PostgreSQL 写入。

复用随机 scheduler_test_* schema。默认搭配内存 Qdrant；使用
--scheduler-configured-services 时搭配随机远端 Collection/Alias，结束后清理。
Embedding 为确定性替身，不访问 FreshRSS、Ollama、真实模型或业务资料。
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import func, select, update

from agent_lab.api.dependencies import get_vector_search_service
from agent_lab.api.file_documents import get_file_document_service
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.db.session import get_db_session
from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.adapters.documents import postgres_indexing_work
from agent_lab.knowledge.adapters.files import PostgresFileDocumentRepository, postgres_file_work
from agent_lab.knowledge.adapters.postgres import postgres_knowledge_base_work
from agent_lab.knowledge.adapters.text_files import parse_text_file
from agent_lab.knowledge.application import KnowledgeBaseService
from agent_lab.knowledge.document_contracts import RetentionCandidate
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.knowledge.file_application import FileDocumentService
from agent_lab.knowledge.files import FileDocumentError
from agent_lab.models.document import DocumentRecord
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord, WriteOperationRecord
from agent_lab.pipeline.document_chunk_pipeline import DocumentChunkPipeline
from agent_lab.pipeline.ollama_embedding_provider import OllamaEmbeddingProvider
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.lifecycle import build_qdrant_client
from agent_lab.qdrant.search import QdrantVectorSearch
from agent_lab.qdrant.store import QdrantChunkStore, QdrantDeletionStore
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository
from agent_lab.services.document_indexing_service import DocumentIndexingService
from agent_lab.services.document_retention_service import DocumentRetentionService
from agent_lab.services.news_pipeline_execution_service import NewsPipelineExecutionService
from agent_lab.services.vector_search_service import VectorSearchService
from agent_lab.services.write_coordination import WriteCoordinator
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_superuser
from tests.test_scheduler_postgres_integration import isolated_database, run
from tests.test_vector_search import ollama_settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason="需要显式授权隔离 PostgreSQL 文件资料验证。",
)


@asynccontextmanager
async def isolated_vectors(schema, *, spec=None):
    spec = spec or VectorIndexSpec(dimension=3)
    remote = os.getenv("RUN_SCHEDULER_QDRANT_INTEGRATION_TEST") == "1"
    settings = QdrantSettings(
        _env_file=None,
        base_url=os.environ["SCHEDULER_TEST_QDRANT_URL"] if remote else "http://qdrant.example.test:6333",
        api_key=os.getenv("SCHEDULER_TEST_QDRANT_API_KEY", "") if remote else "",
        environment=schema, vector_dimension=spec.dimension, collection_schema_version=spec.schema_version,
        distance=spec.distance.value,
    )
    client = build_qdrant_client(settings) if remote else AsyncQdrantClient(location=":memory:")
    created = aliased = False
    try:
        await client.create_collection(settings.collection_name, vectors_config=spec.vector_params, metadata=spec.collection_metadata)
        created = True
        await client.update_collection_aliases([models.CreateAliasOperation(create_alias=models.CreateAlias(
            collection_name=settings.collection_name, alias_name=settings.collection_alias,
        ))])
        aliased = True
        if remote:
            await client.create_payload_index(settings.collection_alias, "document_id", models.PayloadSchemaType.KEYWORD, wait=True)
            await client.create_payload_index(settings.collection_alias, "index_instance_id", models.PayloadSchemaType.KEYWORD, wait=True)
            await client.create_payload_index(settings.collection_alias, "knowledge_base_id", models.PayloadSchemaType.UUID, wait=True)
        yield SimpleNamespace(client=client, settings=settings, spec=spec, store=QdrantDeletionStore(client, settings))
    finally:
        try:
            if aliased:
                await client.update_collection_aliases([models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=settings.collection_alias))])
        finally:
            try:
                if created:
                    await client.delete_collection(settings.collection_name)
            finally:
                await client.close()


def file_service(sessions, vectors, *, repository_type=PostgresFileDocumentRepository):
    @asynccontextmanager
    async def work():
        async with sessions() as session:
            yield repository_type(session)

    @asynccontextmanager
    async def store():
        yield vectors.store

    return FileDocumentService(work, WriteCoordinator(sessions), store)


async def seed_points(vectors, documents):
    await vectors.client.upsert(vectors.settings.collection_alias, [models.PointStruct(
        id=str(uuid4()), vector=[1.0, 0.0, 0.0],
        payload={"document_id": str(document.document_id), "knowledge_base_id": str(document.knowledge_base_id)},
    ) for document in documents], wait=True)


@pytest.mark.parametrize("state", list(ProcessingStatus))
def test_explicit_file_delete_covers_all_states_and_preserves_other_ids(isolated_database, state):
    async def verify():
        db = isolated_database
        async with isolated_vectors(db.schema) as vectors:
            service = file_service(db.sessions, vectors)
            parsed = parse_text_file("同名.txt", b"synthetic")
            target = await service.upload(parsed, DEFAULT_NEWS_KNOWLEDGE_BASE_ID)
            untouched = await service.upload(parsed, DEFAULT_NEWS_KNOWLEDGE_BASE_ID)
            async with db.sessions() as session:
                await session.execute(update(DocumentRecord).where(DocumentRecord.id == target.document_id).values(processing_status=state))
                await session.commit()
            await seed_points(vectors, [target, untouched])
            await service.delete(target.document_id, target.revision)
            async with db.sessions() as session:
                assert await session.get(DocumentRecord, target.document_id) is None
                assert await session.get(DocumentDeletionRecord, target.document_id) is None
                assert await session.get(DocumentRecord, untouched.document_id) is not None
                assert await session.scalar(select(func.count()).select_from(WriteOperationRecord)) == 0
            assert await vectors.store.count_by_document_ids([str(target.document_id)]) == 0
            assert await vectors.store.count_by_document_ids([str(untouched.document_id)]) == 1
    run(verify())


def test_qdrant_confirmation_survives_postgres_finish_failure_and_resume(isolated_database):
    class FailingFinish(PostgresFileDocumentRepository):
        async def finish(self, records):
            raise RuntimeError("隔离收尾故障注入")

    async def verify():
        db = isolated_database
        async with isolated_vectors(db.schema) as vectors:
            service = file_service(db.sessions, vectors, repository_type=FailingFinish)
            document = await service.upload(parse_text_file("保留.txt", b"synthetic"), DEFAULT_NEWS_KNOWLEDGE_BASE_ID)
            await seed_points(vectors, [document])
            with pytest.raises(FileDocumentError) as caught:
                await service.delete(document.document_id, document.revision)
            assert caught.value.code == "file_delete_failed"
            async with db.sessions() as session:
                pending = await session.get(DocumentDeletionRecord, document.document_id)
                assert pending.cutoff_date is None and pending.qdrant_deleted and pending.error_type == "RuntimeError"
                assert await session.get(DocumentRecord, document.document_id) is not None
            assert await vectors.store.count_by_document_ids([str(document.document_id)]) == 0

            @asynccontextmanager
            async def already_confirmed():
                raise AssertionError("已确认的 Qdrant 删除不应重做")
                yield  # pragma: no cover

            resumed = FileDocumentService(partial(postgres_file_work, db.sessions), WriteCoordinator(db.sessions), already_confirmed)
            await resumed.delete(document.document_id, document.revision)
            async with db.sessions() as session:
                assert await session.get(DocumentRecord, document.document_id) is None
                assert await session.get(DocumentDeletionRecord, document.document_id) is None
    run(verify())


def test_retention_neither_resumes_nor_reselects_manual_deletion(isolated_database):
    async def verify():
        db = isolated_database
        now = datetime.now(UTC)
        old = now - timedelta(days=200)
        async with isolated_vectors(db.schema) as vectors:
            service = file_service(db.sessions, vectors)
            documents = [await service.upload(parse_text_file(f"{name}.txt", b"synthetic"), DEFAULT_NEWS_KNOWLEDGE_BASE_ID) for name in ("manual", "pending-retention", "candidate")]
            async with db.sessions() as session:
                await session.execute(update(DocumentRecord).values(processing_status=ProcessingStatus.INDEXED, created_at=old))
                await session.commit()
            await seed_points(vectors, documents)
            coordinator = WriteCoordinator(db.sessions)
            async with coordinator.hold(("sync", "index")):
                async with db.sessions() as session:
                    await PostgresFileDocumentRepository(session).prepare_deletion(documents[0].document_id, 1)
                    repository = DocumentRetentionRepository(session)
                    await repository.prepare([RetentionCandidate(documents[1].document_id, 1, old)], now - timedelta(days=180))
                    result = await DocumentRetentionService(repository, vectors.store, clock=lambda: now).prune_old_documents(180, False)
                    assert result.documents_deleted == 2 and result.failed_batches == 0
            async with db.sessions() as session:
                assert list((await session.scalars(select(DocumentRecord.id))).all()) == [documents[0].document_id]
                assert await session.get(DocumentDeletionRecord, documents[0].document_id) is not None
            assert await vectors.store.count_by_document_ids([str(document.document_id) for document in documents]) == 1
    run(verify())


@pytest.mark.parametrize("filename,content", [("运行说明.txt", "备份保留 7 天。"), ("运行说明.md", "# 备份规则\n\n备份保留 7 天。\n\n```py\nif ready:\n    backup()\n```")])
def test_http_upload_index_search_read_and_replace_current_document(isolated_database, filename, content):
    async def verify():
        db = isolated_database
        other_id = uuid4()
        async with db.sessions() as session:
            session.add(KnowledgeBaseRecord(id=other_id, key="manuals", name="操作资料", is_active=True))
            await session.commit()
        async with isolated_vectors(db.schema) as vectors:
            provider = OllamaEmbeddingProvider(ollama_settings(), embeddings=SimpleNamespace(
                aembed_documents=AsyncMock(side_effect=lambda texts: [[1.0, 0.0, 0.0] for _ in texts]),
                aembed_query=AsyncMock(return_value=[1.0, 0.0, 0.0]),
            ))
            files = file_service(db.sessions, vectors)
            search = VectorSearchService(
                embedding_provider=provider, vector_search=QdrantVectorSearch(vectors.client, vectors.settings, vectors.spec), spec=vectors.spec,
                knowledge_base_scope=KnowledgeBaseService(partial(postgres_knowledge_base_work, db.sessions)),
            )
            indexer = DocumentIndexingService(chunk_pipeline=DocumentChunkPipeline(), embedding_provider=provider,
                point_store=QdrantChunkStore(vectors.client, vectors.settings, vectors.spec), spec=vectors.spec)
            execution = NewsPipelineExecutionService(partial(postgres_indexing_work, db.sessions), coordinator=WriteCoordinator(db.sessions))
            app = allow_superuser(create_offline_app())
            app.dependency_overrides[get_file_document_service] = lambda: files
            app.dependency_overrides[get_vector_search_service] = lambda: search

            async def session_dependency():
                async with db.sessions() as session:
                    yield session

            app.dependency_overrides[get_db_session] = session_dependency
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                    uploads = []
                    for knowledge_id in (DEFAULT_NEWS_KNOWLEDGE_BASE_ID, other_id):
                        response = await client.post("/file-documents", data={"knowledge_base_id": str(knowledge_id)}, files={"file": (filename, content.encode())})
                        assert response.status_code == 201 and response.json()["processing_status"] == "pending"
                        uploads.append(response.json())
                    assert uploads[0]["document_id"] != uploads[1]["document_id"]
                    result = await execution.index_pending(indexer, batch_size=10, stale_after=timedelta(minutes=15))
                    assert result.indexed_count == 2 and not result.failures
                    selection = {"mode": "selected", "knowledge_base_ids": [str(other_id)]}
                    found = await client.post("/document-search", json={"query": "备份规则", "scope": selection})
                    assert found.status_code == 200
                    hit = found.json()["results"][0]
                    assert len(found.json()["results"]) == 1 and hit["document_id"] == uploads[1]["document_id"]
                    assert hit["knowledge_base_id"] == str(other_id) and hit["upload_filename"] == filename
                    detail = (await client.get(f"/documents/{hit['document_id']}")).json()
                    assert detail["content_text"] == content and detail["content_hash"] == hit["content_hash"]
                    assert detail["source_name"] is None and detail["url"] is None and detail["published_at"] is None

                    unchanged = await client.put(f"/file-documents/{hit['document_id']}/file", data={"revision": "1"}, files={"file": (filename, content.encode())})
                    assert unchanged.status_code == 200 and unchanged.json()["revision"] == 1
                    assert unchanged.json()["processing_status"] == "indexed"
                    updated_text = content.replace("7 天", "14 天")
                    changed = await client.put(f"/file-documents/{hit['document_id']}/file", data={"revision": "1"}, files={"file": ("new-" + filename, updated_text.encode())})
                    assert changed.status_code == 200 and changed.json()["revision"] == 2
                    assert changed.json()["knowledge_base_id"] == str(other_id)
                    current = (await client.get(f"/documents/{hit['document_id']}")).json()
                    assert current["content_text"] == updated_text and current["content_hash"] != hit["content_hash"]
                    stale = await client.put(f"/file-documents/{hit['document_id']}/file", data={"revision": "1"}, files={"file": (filename, b"stale")})
                    assert stale.status_code == 409 and stale.json()["code"] == "file_revision_conflict"
                    reindexed = await execution.index_pending(indexer, batch_size=10, stale_after=timedelta(minutes=15))
                    assert reindexed.indexed_count == 1
                    latest = (await client.post("/document-search", json={"query": "备份规则", "scope": selection})).json()["results"][0]
                    assert latest["document_id"] == hit["document_id"] and latest["content_hash"] == current["content_hash"]
            finally:
                await provider.close()
    run(verify())
