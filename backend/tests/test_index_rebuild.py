"""冻结 Chunk 重建、发布失败与显式恢复；内存 Qdrant 不访问外部服务。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from agent_lab import cli
from agent_lab.domain.write_scope import WriteRecoveryRequiredError, WriteResourceBusyError, WriteScope, write_scope
from agent_lab.knowledge.document_contracts import IndexRebuildResult, RebuiltDocument
from agent_lab.knowledge.processing.indexing import require_preview_compatible
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.knowledge.rebuilding import IndexRebuildService
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.lifecycle import QdrantCollectionLifecycle, QdrantLifecycleError
from agent_lab.qdrant.rebuilding import QdrantRebuildTarget
from agent_lab.qdrant.store import QdrantPointStoreError
from tests.test_candidate_index import candidate, processor
from tests.test_qdrant_vector_store import qdrant_settings


class MemoryRepository:
    def __init__(self, processor, spec):
        self.documents = sorted([candidate(processor, spec, uuid4(), text=f"# 章节 {index}\n\n已采用正文 {index}")
                                 for index in range(3)], key=lambda target: target.document_id)
        self.pending = []
        self.status = "old"
        self.fenced = False

    async def require_ready(self):
        pass

    async def list_documents(self, *, after, limit):
        return [target.document_id for target in self.documents if after is None or target.document_id > after][:limit]

    async def prepare_document(self, identity, *, index_spec, location):
        original = next(item for item in self.documents if item.document_id == identity)
        require_preview_compatible(original.preview, index_spec)
        target = original.model_copy(update={"processing_id": uuid4(), "index_instance_id": uuid4(),
            "base_version_id": original.version_id, "index_spec": index_spec})
        snapshot = RebuiltDocument(identity, target.knowledge_base_id, 1, target.content_hash,
            target.version_id, original.index_instance_id, target.processing_id, target.index_instance_id)
        self.pending.append((snapshot, target, location))
        return snapshot, target

    async def mark_prepared(self, processing_id):
        pass

    async def begin_publication(self, documents):
        assert [snapshot for snapshot, _, _ in self.pending] == documents
        self.fenced = True

    async def mark_rebuilt(self, documents, *, schema_version):
        assert self.fenced
        self.status, self.fenced = schema_version, False

    async def fail_build(self, documents):
        pass

    async def pending_publication(self, collection):
        assert self.fenced
        return self.pending

    async def abort_publication(self, documents):
        self.fenced = False


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


@asynccontextmanager
async def scenario(processor):
    client = AsyncQdrantClient(location=":memory:")
    settings = qdrant_settings(generation=2).model_copy(update={"collection_schema_version": "v3"})
    old_name = settings.model_copy(update={"collection_generation": 1}).collection_name
    spec = VectorIndexSpec(dimension=3, chunk_size=64)
    repository, coordinator = MemoryRepository(processor, spec), Coordinator()
    await client.create_collection(old_name, vectors_config=spec.vector_params, metadata=spec.collection_metadata)
    await client.update_collection_aliases([models.CreateAliasOperation(create_alias=models.CreateAlias(
        collection_name=old_name, alias_name=settings.collection_alias))])
    await client.upsert(old_name, points=[models.PointStruct(id=str(uuid4()), vector=[1, 0, 0], payload={"old": True})])
    received = []

    async def embed(texts):
        assert coordinator.active and repository.status == "old"
        assert repository.pending
        points, _ = await client.scroll(settings.collection_alias)
        assert len(points) == 1 and points[0].payload == {"old": True}
        received.extend(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]

    embeddings = SimpleNamespace(embedding_model=spec.embedding_model, embed_documents=AsyncMock(side_effect=embed))
    target = QdrantRebuildTarget(client, settings, spec, embeddings)
    service = IndexRebuildService(repository, target, coordinator)
    try:
        yield SimpleNamespace(client=client, target=target, service=service, repository=repository,
            coordinator=coordinator, settings=settings, spec=spec, old_name=old_name, embeddings=embeddings, received=received)
    finally:
        await client.close()


def test_rebuild_embeds_frozen_text_before_publish_and_runtime_follows_alias(processor):
    async def verify():
        async with scenario(processor) as state:
            before = [document.model_dump() for document in state.repository.documents]
            result = await state.service.rebuild(batch_size=1)
            assert result.document_count == 3 and result.point_count >= 3
            assert state.repository.status == "v3" and not state.repository.fenced
            assert not state.coordinator.active and not state.coordinator.scope.uncertain
            assert state.received == [chunk.embedding_text for document in state.repository.documents
                                      for chunk in document.preview.chunk_result.chunks]
            assert before == [document.model_dump() for document in state.repository.documents]
            records, _ = await state.client.scroll(state.settings.collection_alias, limit=100)
            assert {record.payload["document_id"] for record in records} == {str(doc.document_id) for doc in state.repository.documents}
            assert {record.payload["index_instance_id"] for record in records} == {str(target.index_instance_id) for _, target, _ in state.repository.pending}
            assert (await state.client.count(state.old_name, exact=True)).count == 1
            lifecycle = QdrantCollectionLifecycle(state.client, state.settings.model_copy(update={"collection_generation": 1}), state.spec)
            assert await lifecycle.ensure_current_collection() == state.settings.collection_name
    asyncio.run(verify())


@pytest.mark.parametrize("failure", ["embedding", "payload", "count", "version", "target_exists", "chunk_spec"])
def test_rebuild_failure_before_publish_keeps_old_alias(processor, failure, monkeypatch):
    async def verify():
        async with scenario(processor) as state:
            if failure == "embedding":
                state.embeddings.embed_documents.side_effect = ValueError("bad embedding")
            elif failure == "payload":
                monkeypatch.setattr(state.client, "retrieve", AsyncMock(return_value=[]))
            elif failure == "count":
                monkeypatch.setattr(state.client, "count", AsyncMock(return_value=SimpleNamespace(count=999)))
            elif failure == "version":
                monkeypatch.setattr(state.repository, "begin_publication", AsyncMock(side_effect=WriteRecoveryRequiredError()))
            elif failure == "chunk_spec":
                state.target.index_spec = dict(state.target.index_spec, chunk_size=128)
            else:
                await state.client.create_collection(state.settings.collection_name, vectors_config=state.spec.vector_params)
            with pytest.raises((ValueError, QdrantLifecycleError, QdrantPointStoreError, WriteRecoveryRequiredError, ProcessingApplicationError)):
                await state.service.rebuild(batch_size=1)
            assert await state.target.current_target() == state.old_name
            assert state.repository.status == "old" and not state.repository.fenced
            if failure == "chunk_spec":
                state.embeddings.embed_documents.assert_not_called()
    asyncio.run(verify())


@pytest.mark.parametrize("failure", ["alias", "database"])
def test_publish_failure_keeps_fence_and_explicit_recovery_never_rewrites(processor, failure, monkeypatch):
    async def verify():
        async with scenario(processor) as state:
            with monkeypatch.context() as patch:
                if failure == "alias":
                    patch.setattr(state.client, "update_collection_aliases", AsyncMock(side_effect=TimeoutError()))
                else:
                    patch.setattr(state.repository, "mark_rebuilt", AsyncMock(side_effect=RuntimeError()))
                with pytest.raises(WriteRecoveryRequiredError):
                    await state.service.rebuild()
            assert state.coordinator.scope.uncertain and state.repository.fenced
            state.embeddings.embed_documents.reset_mock()
            state.embeddings.embed_documents.side_effect = AssertionError("恢复不得重新向量化")
            state.coordinator.scope = WriteScope(("sync", "index"))  # 模拟人工核实后的资源释放。
            outcome = await state.service.recover()
            assert outcome.published == (failure == "database")
            assert state.repository.status == ("v3" if outcome.published else "old")
            assert not state.repository.fenced
            state.embeddings.embed_documents.assert_not_called()
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


@pytest.mark.parametrize("command", ["rebuild-index", "recover-index-rebuild"])
def test_cli_rebuild_dispatches_explicit_generation_and_closes(monkeypatch, command):
    result = IndexRebuildResult(3, 7)
    service = SimpleNamespace(rebuild=AsyncMock(return_value=result), recover=AsyncMock(return_value=result))
    closed = []

    @asynccontextmanager
    async def factory(generation):
        assert generation == 2
        try:
            yield service
        finally:
            closed.append(True)

    monkeypatch.setattr(cli, "index_rebuild_service", factory)
    outcome = asyncio.run(cli.dispatch_command(cli.build_parser().parse_args([command, "--generation", "2"])))
    assert outcome.exit_code == 0
    assert outcome.payload == {"command": command, "generation": 2, "ok": True, "document_count": 3, "point_count": 7, "published": True}
    assert closed == [True]
