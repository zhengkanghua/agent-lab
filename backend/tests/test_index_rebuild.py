"""生产重建用例与内存 Qdrant 的发布边界；不访问真实服务。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from agent_lab import cli
from agent_lab.domain.write_scope import WriteRecoveryRequiredError, WriteResourceBusyError, WriteScope, write_scope
from agent_lab.knowledge.document_contracts import IndexRebuildResult, RebuiltDocument
from agent_lab.knowledge.rebuilding import IndexRebuildService
from agent_lab.pipeline.document_chunk_pipeline import DocumentChunkPipeline
from agent_lab.pipeline.ollama_embedding_provider import ChunkEmbedding
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.lifecycle import QdrantCollectionLifecycle, QdrantLifecycleError
from agent_lab.qdrant.rebuilding import QdrantRebuildTarget
from tests.test_document_indexing_service import build_record
from tests.test_qdrant_vector_store import qdrant_settings


class MemoryRepository:
    def __init__(self):
        self.documents = sorted([
            replace(build_record(), id=uuid4(), knowledge_base_id=uuid4(), title=f"Document {index}")
            for index in range(3)
        ], key=lambda document: document.id)
        self.status = "indexed-v1"

    async def require_ready(self):
        pass

    async def list_documents(self, *, after, limit):
        return [document for document in self.documents if after is None or document.id > after][:limit]

    async def verify_versions(self, documents):
        assert list(documents) == [RebuiltDocument(
            document.id, document.knowledge_base_id, document.index_revision, document.content_hash,
        ) for document in self.documents]

    async def mark_rebuilt(self, documents, *, schema_version):
        await self.verify_versions(documents)
        self.status = f"indexed-{schema_version}"


class Coordinator:
    def __init__(self):
        self.scope = WriteScope(("sync", "index"))
        self.active = False

    @asynccontextmanager
    async def hold(self, resources, *, wait=True):
        assert resources == ("sync", "index") and not wait
        self.active = True
        token = write_scope.set(self.scope)
        try:
            yield
        finally:
            self.active = False
            write_scope.reset(token)


class Embeddings:
    embedding_model = "bge-m3:567m"
    dimension = 3

    def __init__(self, during_write):
        self._during_write = during_write

    async def embed_chunks(self, chunks):
        await self._during_write()
        return [ChunkEmbedding(chunk_id=chunk.id, embedding=[1.0, 0.0, 0.0]) for chunk in chunks]


@asynccontextmanager
async def scenario():
    client = AsyncQdrantClient(location=":memory:")
    settings = qdrant_settings(generation=2)
    old_name = settings.model_copy(update={"collection_generation": 1}).collection_name
    spec = VectorIndexSpec(dimension=3)
    repository = MemoryRepository()
    coordinator = Coordinator()
    await client.create_collection(old_name, vectors_config=spec.vector_params, metadata=spec.collection_metadata)
    await client.update_collection_aliases([models.CreateAliasOperation(create_alias=models.CreateAlias(
        collection_name=old_name, alias_name=settings.collection_alias,
    ))])
    await client.upsert(old_name, points=[models.PointStruct(id=str(uuid4()), vector=[1, 0, 0], payload={"old": True})])

    async def during_write():
        assert coordinator.active
        assert repository.status == "indexed-v1"
        points, _ = await client.scroll(settings.collection_alias)
        assert len(points) == 1 and points[0].payload == {"old": True}

    target = QdrantRebuildTarget(client, settings, spec, DocumentChunkPipeline(), Embeddings(during_write))
    service = IndexRebuildService(repository, target, coordinator)
    try:
        yield SimpleNamespace(client=client, target=target, service=service, repository=repository,
                              coordinator=coordinator, settings=settings, spec=spec, old_name=old_name)
    finally:
        await client.close()


def test_rebuild_indexes_all_documents_before_publish_and_runtime_follows_alias():
    async def verify():
        async with scenario() as state:
            result = await state.service.rebuild(batch_size=1)
            assert result.document_count == 3 and result.point_count >= 3
            assert state.repository.status == "indexed-v2"
            assert not state.coordinator.active and not state.coordinator.scope.uncertain
            records, _ = await state.client.scroll(state.settings.collection_alias, limit=100)
            assert {record.payload["document_id"] for record in records} == {str(document.id) for document in state.repository.documents}
            assert {record.payload["knowledge_base_id"] for record in records} == {str(document.knowledge_base_id) for document in state.repository.documents}
            assert all(record.payload["mime_type"] == "text/plain" for record in records)
            assert (await state.client.count(state.old_name, exact=True)).count == 1
            old_settings = state.settings.model_copy(update={"collection_generation": 1})
            lifecycle = QdrantCollectionLifecycle(state.client, old_settings, state.spec)
            assert await lifecycle.ensure_current_collection() == state.settings.collection_name

    asyncio.run(verify())


@pytest.mark.parametrize("failure", ["embedding", "payload", "count", "version", "target_exists"])
def test_rebuild_failure_before_publish_keeps_old_alias(failure, monkeypatch):
    async def verify():
        async with scenario() as state:
            if failure == "embedding":
                monkeypatch.setattr(state.target._indexer._embedding_provider, "embed_chunks", AsyncMock(side_effect=ValueError("bad embedding")))
            elif failure == "payload":
                monkeypatch.setattr(state.client, "retrieve", AsyncMock(return_value=[]))
            elif failure == "count":
                monkeypatch.setattr(state.client, "count", AsyncMock(return_value=SimpleNamespace(count=999)))
            elif failure == "version":
                monkeypatch.setattr(state.repository, "verify_versions", AsyncMock(side_effect=WriteRecoveryRequiredError()))
            else:
                await state.client.create_collection(state.settings.collection_name, vectors_config=state.spec.vector_params)
            with pytest.raises((ValueError, QdrantLifecycleError, WriteRecoveryRequiredError)):
                await state.service.rebuild(batch_size=1)
            lifecycle = QdrantCollectionLifecycle(state.client, state.settings, state.spec)
            assert await lifecycle.current_target() == state.old_name
            assert state.repository.status == "indexed-v1"
            assert not state.coordinator.active

    asyncio.run(verify())


@pytest.mark.parametrize("failure", ["alias", "database"])
def test_publish_failure_keeps_manual_recovery_boundary(failure, monkeypatch):
    async def verify():
        async with scenario() as state:
            if failure == "alias":
                monkeypatch.setattr(state.client, "update_collection_aliases", AsyncMock(side_effect=TimeoutError()))
            else:
                monkeypatch.setattr(state.repository, "mark_rebuilt", AsyncMock(side_effect=RuntimeError()))
            with pytest.raises(WriteRecoveryRequiredError):
                await state.service.rebuild()
            assert state.coordinator.scope.uncertain
            assert state.repository.status == "indexed-v1"

    asyncio.run(verify())


def test_rebuild_conflict_does_not_prepare_target():
    @asynccontextmanager
    async def busy(*args, **kwargs):
        raise WriteResourceBusyError()
        yield

    repository, target = AsyncMock(), AsyncMock()
    with pytest.raises(WriteResourceBusyError):
        asyncio.run(IndexRebuildService(repository, target, SimpleNamespace(hold=busy)).rebuild())
    repository.require_ready.assert_not_called()
    target.prepare.assert_not_called()


def test_cli_rebuild_dispatches_explicit_generation_and_closes(monkeypatch):
    service = SimpleNamespace(rebuild=AsyncMock(return_value=IndexRebuildResult(3, 7)))
    closed = []

    @asynccontextmanager
    async def factory(generation):
        assert generation == 2
        try:
            yield service
        finally:
            closed.append(True)

    monkeypatch.setattr(cli, "index_rebuild_service", factory)
    result = asyncio.run(cli.dispatch_command(cli.build_parser().parse_args(["rebuild-index", "--generation", "2"])))
    assert result.exit_code == 0
    assert result.payload == {"command": "rebuild-index", "generation": 2, "ok": True, "document_count": 3, "point_count": 7}
    assert closed == [True]
