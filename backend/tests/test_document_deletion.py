"""删除跨 generation 的完整性，以及本项目环境和 Document 身份边界。"""

import asyncio
from uuid import uuid4

from qdrant_client import AsyncQdrantClient, models

from agent_lab.config.qdrant import QdrantSettings
from agent_lab.qdrant.store import QdrantDeletionStore


def test_deletion_covers_owned_generations_and_preserves_other_documents_and_environments():
    async def verify():
        settings = QdrantSettings(_env_file=None, environment="deletion_test", vector_dimension=3)
        target, retained = str(uuid4()), str(uuid4())
        names = [settings.collection_name, settings.model_copy(update={"collection_generation": 2}).collection_name,
                 "knowledge_chunks_deletion_test_other_v3_001"]
        client = AsyncQdrantClient(location=":memory:")
        try:
            for name in names:
                await client.create_collection(name, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
                await client.upsert(name, [models.PointStruct(id=str(uuid4()), vector=[1.0, 0.0, 0.0], payload={"document_id": identity})
                                            for identity in (target, retained)])
            store = QdrantDeletionStore(client, settings)
            assert await store.count_by_document_ids([target]) == 2
            await store.delete_by_document_ids([target])
            assert await store.count_by_document_ids([target]) == 0
            assert await store.count_by_document_ids([retained]) == 2
            assert (await client.count(names[2], exact=True)).count == 2
            await store.delete_by_document_ids([target])
        finally:
            await client.close()
    asyncio.run(verify())
