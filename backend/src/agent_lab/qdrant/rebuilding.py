"""只向尚未发布的新 generation 写入；逐篇核验后原子发布 current Alias。"""

from agent_lab.domain.write_scope import remote_write
from agent_lab.qdrant.lifecycle import QdrantCollectionLifecycle, QdrantLifecycleError
from agent_lab.qdrant.payload import QdrantPayloadMapper
from agent_lab.qdrant.store import QdrantChunkStore
from agent_lab.services.document_indexing_service import DocumentIndexingService


class QdrantRebuildTarget:
    """重建专用适配器；目标必须未存在，失败留下的 generation 不自动复用或删除。"""

    def __init__(self, client, settings, spec, chunk_pipeline, embedding_provider):
        self.schema_version = spec.schema_version
        self._client = client
        self._settings = settings
        self._spec = spec
        self._pipeline = chunk_pipeline
        self._mapper = QdrantPayloadMapper(spec)
        self._lifecycle = QdrantCollectionLifecycle(client, settings, spec)
        self._store = QdrantChunkStore(client, settings, spec, rebuild_collection=settings.collection_name)
        self._indexer = DocumentIndexingService(
            chunk_pipeline=chunk_pipeline, embedding_provider=embedding_provider,
            point_store=self._store, spec=spec,
        )
        self._original_alias = None

    async def prepare(self):
        self._original_alias = await self._lifecycle.current_target()
        if await self._client.collection_exists(self._settings.collection_name):
            raise QdrantLifecycleError("重建 generation 已存在，请选择新的代次。")
        await self._lifecycle.ensure_collection(self._settings.collection_name)

    async def write_document(self, document):
        result = await self._indexer.write_snapshot(document)
        # 使用同一确定性切分规则核对完整 Payload，防止缺 Chunk、错归属或版本混写。
        import asyncio
        chunks = await asyncio.to_thread(self._pipeline.build_chunks, document)
        expected = {chunk.id: self._mapper.build(chunk) for chunk in chunks}
        if set(result.upserted_ids) != set(expected):
            raise QdrantLifecycleError("重建返回的 Chunk 身份不完整。")
        actual = {}
        ids = list(expected)
        for start in range(0, len(ids), self._settings.write_batch_size):
            records = await self._client.retrieve(
                collection_name=self._settings.collection_name,
                ids=ids[start:start + self._settings.write_batch_size],
                with_payload=True, with_vectors=False,
            )
            actual.update({str(record.id): record.payload for record in records})
        if actual != expected:
            raise QdrantLifecycleError("重建 Point 回读结果与 Document 不一致。")
        return len(expected)

    async def verify_total(self, expected_points):
        count = await self._client.count(self._settings.collection_name, exact=True)
        if count.count != expected_points:
            raise QdrantLifecycleError("重建 Point 总数与已验证文档不一致。")

    @remote_write
    async def publish(self):
        if await self._lifecycle.current_target() != self._original_alias:
            raise QdrantLifecycleError("重建期间 current Alias 已被其他操作者修改。")
        await self._lifecycle.switch_current_alias(self._settings.collection_name)
