"""真实内存 Qdrant 验证候选隔离、写入核验、切换与查询补足。"""

import asyncio
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient

from agent_lab.config.qdrant import QdrantSettings
from agent_lab.knowledge.adapters.docling_chunker import DoclingStructuredChunker
from agent_lab.knowledge.adapters.docling_parser import DoclingDocumentParser
from agent_lab.knowledge.processing.indexing import CandidateIndexer, IndexMetadata, IndexTarget
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.knowledge.processing.processor import DocumentProcessor
from agent_lab.knowledge.storage import ObjectReference
from agent_lab.knowledge.visibility import AdoptedVectorSearch, SearchVisibilityError, VisibilitySnapshot
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.lifecycle import QdrantCollectionLifecycle
from agent_lab.qdrant.search import QdrantVectorSearch
from agent_lab.qdrant.store import QdrantChunkStore, QdrantPointStoreError
from agent_lab.schemas.vector_search import VectorSearchFilters


@pytest.fixture(scope="module")
def processor():
    return DocumentProcessor(parser=DoclingDocumentParser(), chunker=DoclingStructuredChunker(
        tokenizer_path=Path(__file__).parents[1] / ".cache/tokenizers/bge-m3", max_tokens=64,
    ))


def candidate(processor, spec, knowledge_base_id, *, document_id=None, text="正文", base=None):
    preview = processor.preview(text.encode(), mime_type="text/markdown", title="资料")
    return IndexTarget(
        processing_id=uuid4(), document_id=document_id or uuid4(), knowledge_base_id=knowledge_base_id,
        candidate_revision=1, review_id=uuid4(), version_id=uuid4(), index_instance_id=uuid4(),
        base_version_id=base, preview=preview, metadata=IndexMetadata(), index_spec=spec.collection_metadata,
        source=ObjectReference("synthetic/source", len(text.encode()), sha256(text.encode()).hexdigest()),
    )


class MemoryVisibility:
    def __init__(self, knowledge_base_id):
        self.knowledge_base_id = knowledge_base_id
        self.revision = 1
        self.instances = {}
        self.current = {}
        self.drift = False

    def intent(self, target):
        self.instances[target.index_instance_id] = target
        self.revision += 1

    def adopt(self, target):
        self.current[target.document_id] = target
        self.revision += 1

    def reject(self, document_id):
        self.current.pop(document_id, None)
        self.revision += 1

    async def snapshot(self, knowledge_base_ids):
        active = {item.index_instance_id for item in self.current.values()}
        return VisibilitySnapshot(((self.knowledge_base_id, self.revision, True),), tuple(set(self.instances) - active))

    async def validate(self, snapshot, hits):
        if self.drift:
            self.revision += 1
        return snapshot.revisions[0][1] == self.revision and all(
            hit.document_id in self.current and self.current[hit.document_id].index_instance_id == hit.index_instance_id
            for hit in hits
        )


async def setup():
    client = AsyncQdrantClient(location=":memory:")
    settings = QdrantSettings(_env_file=None, environment="candidate-test", collection_schema_version="v3",
                              vector_dimension=3, write_batch_size=2)
    spec = VectorIndexSpec(dimension=3, chunk_size=64)
    await QdrantCollectionLifecycle(client, settings, spec).ensure_current_collection()
    return client, settings, spec, QdrantChunkStore(client, settings, spec)


def vectors(target, vector=(1.0, 0.0, 0.0)):
    return [list(vector) for _ in target.preview.chunk_result.chunks]


def test_partial_candidate_write_keeps_previous_instance_and_search(processor):
    async def verify():
        client, settings, spec, store = await setup()
        try:
            kb = uuid4()
            old = candidate(processor, spec, kb, text="# 原版\n\n旧正文可用")
            new = candidate(processor, spec, kb, document_id=old.document_id, base=old.version_id,
                            text="\n\n".join(f"## 章节 {i}\n\n新的正文 {i}" for i in range(6)))
            visibility = MemoryVisibility(kb)
            visibility.intent(old)
            await store.prepare_candidate(old, vectors(old))
            visibility.adopt(old)
            visibility.intent(new)
            original = client.upsert
            writes = 0
            async def failing(**kwargs):
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise RuntimeError("synthetic network failure")
                return await original(**kwargs)
            client.upsert = failing
            with pytest.raises(QdrantPointStoreError):
                await store.prepare_candidate(new, vectors(new))
            search = AdoptedVectorSearch(QdrantVectorSearch(client, settings, spec), visibility)
            groups = await search.search_groups([1.0, 0.0, 0.0], document_limit=10, matches_per_document=3,
                score_threshold=None, filters=VectorSearchFilters(knowledge_base_id=kb))
            assert len(groups) == 1 and groups[0].matches[0].content_hash == old.content_hash
            assert all(hit.index_instance_id == old.index_instance_id for hit in groups[0].matches)
            assert (await client.count(settings.collection_alias, exact=True)).count > len(old.preview.chunk_result.chunks)
        finally:
            await client.close()
    asyncio.run(verify())


def test_adoption_switch_and_cleanup_are_scoped_to_one_instance(processor):
    async def verify():
        client, settings, spec, store = await setup()
        try:
            kb = uuid4()
            old = candidate(processor, spec, kb, text="旧正文")
            new = candidate(processor, spec, kb, document_id=old.document_id, text="新正文")
            other = candidate(processor, spec, kb, text="其他资料")
            visibility = MemoryVisibility(kb)
            for target in (old, new, other):
                visibility.intent(target)
                await store.prepare_candidate(target, vectors(target))
            visibility.adopt(old)
            visibility.adopt(other)
            search = AdoptedVectorSearch(QdrantVectorSearch(client, settings, spec), visibility)
            async def visible():
                return await search.search([1.0, 0.0, 0.0], top_k=10, score_threshold=None,
                                           filters=VectorSearchFilters(knowledge_base_id=kb))
            assert {hit.index_instance_id for hit in await visible()} == {old.index_instance_id, other.index_instance_id}
            visibility.adopt(new)
            assert {hit.index_instance_id for hit in await visible()} == {new.index_instance_id, other.index_instance_id}
            await store.delete_instance(old.document_id, old.index_instance_id)
            assert (await client.count(settings.collection_alias, exact=True)).count == 2
            assert {hit.index_instance_id for hit in await visible()} == {new.index_instance_id, other.index_instance_id}
        finally:
            await client.close()
    asyncio.run(verify())


@pytest.mark.parametrize("grouped", [False, True])
def test_concurrent_candidate_write_or_rejection_requeries_without_losing_slots(processor, grouped):
    async def verify():
        client, settings, spec, store = await setup()
        try:
            kb = uuid4()
            old = candidate(processor, spec, kb, text="旧正文")
            other = candidate(processor, spec, kb, text="继续返回的资料")
            visibility = MemoryVisibility(kb)
            for target in (old, other):
                visibility.intent(target)
                await store.prepare_candidate(target, vectors(target, (0.8, 0.6, 0.0)))
                visibility.adopt(target)
            pending = candidate(processor, spec, kb, document_id=old.document_id, text="高分候选")
            method_name = "query_points_groups" if grouped else "query_points"
            original = getattr(client, method_name)
            changed = False
            async def concurrent(**kwargs):
                nonlocal changed
                if not changed:
                    changed = True
                    visibility.intent(pending)
                    await store.prepare_candidate(pending, vectors(pending))
                    visibility.reject(old.document_id)
                return await original(**kwargs)
            setattr(client, method_name, concurrent)
            search = AdoptedVectorSearch(QdrantVectorSearch(client, settings, spec), visibility)
            filters = VectorSearchFilters(knowledge_base_id=kb)
            result = (await search.search_groups([1.0, 0.0, 0.0], document_limit=1, matches_per_document=2,
                        score_threshold=None, filters=filters)) if grouped else (
                        await search.search([1.0, 0.0, 0.0], top_k=1, score_threshold=None, filters=filters))
            assert len(result) == 1 and result[0].document_id == other.document_id
        finally:
            await client.close()
    asyncio.run(verify())


def test_continuous_visibility_changes_return_retryable_failure(processor):
    async def verify():
        client, settings, spec, store = await setup()
        try:
            kb = uuid4()
            visibility = MemoryVisibility(kb)
            visibility.drift = True
            search = AdoptedVectorSearch(QdrantVectorSearch(client, settings, spec), visibility)
            with pytest.raises(SearchVisibilityError):
                await search.search([1.0, 0.0, 0.0], top_k=1, score_threshold=None,
                                    filters=VectorSearchFilters(knowledge_base_id=kb))
        finally:
            await client.close()
    asyncio.run(verify())


@pytest.mark.parametrize("failure", ["missing", "payload", "vector", "count"])
def test_incomplete_or_corrupt_candidate_does_not_pass_preparation(processor, failure):
    async def verify():
        client, settings, spec, store = await setup()
        try:
            target = candidate(processor, spec, uuid4())
            original = client.retrieve
            async def corrupted(**kwargs):
                records = await original(**kwargs)
                if failure == "missing":
                    return []
                if failure == "payload":
                    records[0].payload["content_hash"] = "a" * 64
                if failure == "vector":
                    records[0].vector = [0.0, 1.0, 0.0]
                return records
            client.retrieve = corrupted
            if failure == "count":
                from types import SimpleNamespace
                async def wrong_count(**kwargs):
                    return SimpleNamespace(count=5)
                client.count = wrong_count
            with pytest.raises(QdrantPointStoreError):
                await store.prepare_candidate(target, vectors(target))
        finally:
            await client.close()
    asyncio.run(verify())


def test_indexer_embeds_frozen_contextual_text_without_rechunking(processor):
    async def verify():
        client, settings, spec, store = await setup()
        try:
            target = candidate(processor, spec, uuid4(), text="# 上下文\n\n## 子标题\n\n正文")
            received = []
            class Embeddings:
                embedding_model = spec.embedding_model
                async def embed_documents(self, texts):
                    received.extend(texts)
                    return vectors(target)
            await CandidateIndexer(Embeddings(), store, spec.collection_metadata).prepare(target)
            assert received == [chunk.embedding_text for chunk in target.preview.chunk_result.chunks]
            assert "上下文" in received[0] and "子标题" in received[0]
        finally:
            await client.close()
    asyncio.run(verify())


@pytest.mark.parametrize("mismatch", ["model", "spec", "empty"])
def test_indexer_rejects_invalid_target_without_embedding_or_writing(processor, mismatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    spec = VectorIndexSpec(dimension=3, chunk_size=64)
    target = candidate(processor, spec, uuid4())
    embeddings = SimpleNamespace(embedding_model=spec.embedding_model, embed_documents=AsyncMock())
    store = SimpleNamespace(prepare_candidate=AsyncMock())
    if mismatch == "model":
        embeddings.embedding_model = "other-model"
    elif mismatch == "spec":
        target = target.model_copy(update={"index_spec": dict(target.index_spec, chunk_size=32)})
    else:
        target = target.model_copy(update={"preview": target.preview.model_copy(update={
            "chunk_result": target.preview.chunk_result.model_copy(update={"chunks": ()})})})
    with pytest.raises(ProcessingApplicationError):
        asyncio.run(CandidateIndexer(embeddings, store, spec.collection_metadata).prepare(target))
    embeddings.embed_documents.assert_not_awaited()
    store.prepare_candidate.assert_not_awaited()
