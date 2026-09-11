"""独立 generation 复用冻结索引器，完整回读通过后才发布 current Alias。"""

from agent_lab.domain.write_scope import remote_write
from agent_lab.knowledge.processing.indexing import CandidateIndexer
from agent_lab.qdrant.lifecycle import QdrantCollectionLifecycle, QdrantLifecycleError
from agent_lab.qdrant.store import QdrantChunkStore


class QdrantRebuildTarget:
    """失败目标不自动复用；恢复只核对已经准备的 Point，不重新切分或向量化。"""

    def __init__(self, client, settings, spec, embedding_provider):
        self.schema_version = spec.schema_version
        self.index_spec = spec.collection_metadata
        self.collection_name = settings.collection_name
        self._client, self._spec = client, spec
        self._lifecycle = QdrantCollectionLifecycle(client, settings, spec)
        self._store = QdrantChunkStore(client, settings, spec, rebuild_collection=self.collection_name)
        self._indexer = CandidateIndexer(embedding_provider, self._store, self.index_spec)
        self.location = None

    async def current_target(self):
        return await self._lifecycle.current_target()

    async def prepare(self):
        previous = await self.current_target()
        if await self._client.collection_exists(self.collection_name):
            raise QdrantLifecycleError("重建 generation 已存在，请选择新的代次。")
        await self._lifecycle.ensure_collection(self.collection_name)
        self.location = {"collection": self.collection_name, "previous_collection": previous}

    async def write_document(self, target):
        await self._indexer.prepare(target)
        return len(target.preview.chunk_result.chunks)

    async def verify_document(self, target):
        self._spec.validate_collection_info(await self._client.get_collection(self.collection_name))
        if target.index_spec != self.index_spec:
            raise QdrantLifecycleError("恢复目标的索引规格与当前配置不一致。")
        await self._store.verify_candidate(target)
        return len(target.preview.chunk_result.chunks)

    async def verify_total(self, expected_points):
        count = await self._client.count(self.collection_name, exact=True)
        if count.count != expected_points:
            raise QdrantLifecycleError("重建 Point 总数与已验证文档不一致。")

    @remote_write
    async def publish(self):
        if await self.current_target() != self.location["previous_collection"]:
            raise QdrantLifecycleError("重建期间 current Alias 已被其他操作者修改。")
        await self._lifecycle.switch_current_alias(self.collection_name)
