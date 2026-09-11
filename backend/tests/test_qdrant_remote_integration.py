"""显式启用后验证远程 Qdrant Collection/Alias/Point 生命周期并自动清理。"""

import asyncio
import os
from uuid import uuid4

import pytest
from pydantic import SecretStr
from qdrant_client.http import models

from agent_lab.config.qdrant import QdrantSettings
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.lifecycle import (
    QdrantCollectionLifecycle,
    build_qdrant_client,
)
from agent_lab.qdrant.store import QdrantChunkStore
from agent_lab.knowledge.processing.indexing import IndexMetadata
from tests.test_candidate_index import candidate, processor
from datetime import UTC, datetime


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_QDRANT_REMOTE_INTEGRATION_TEST") != "1",
    reason=(
        "set RUN_QDRANT_REMOTE_INTEGRATION_TEST=1 to create and clean an isolated "
        "test Collection on the configured Qdrant"
    ),
)


def test_remote_qdrant_alias_lifecycle_and_point_round_trip(processor) -> None:
    """只写随机隔离的测试 Collection/Alias，并在 finally 中删除。"""

    async def verify() -> None:
        suffix = uuid4().hex[:12]
        base_url = os.environ["QDRANT_BASE_URL"]
        api_key = os.getenv("QDRANT_API_KEY", "")
        settings = QdrantSettings(
            _env_file=None,
            base_url=base_url,
            api_key=SecretStr(api_key),
            environment=f"integration_{suffix}",
            collection_schema_version="v3",
            collection_generation=1,
            vector_dimension=3,
            distance="Cosine",
        )
        spec = VectorIndexSpec(dimension=3, schema_version="v3", chunk_size=64)
        client = build_qdrant_client(settings)
        lifecycle = QdrantCollectionLifecycle(client, settings, spec)
        store = QdrantChunkStore(client, settings, spec)
        knowledge_base_id = uuid4()
        target = candidate(processor, spec, knowledge_base_id, text="远程 Qdrant 隔离测试文本")
        target = target.model_copy(update={"metadata": IndexMetadata(published_at=datetime(2026, 8, 13, 1, 2, 3, tzinfo=UTC))})
        try:
            await lifecycle.ensure_current_collection()
            await store.prepare_candidate(target, [[3.0, 4.0, 0.0]])
            records, _ = await client.scroll(
                collection_name=settings.collection_alias,
                with_payload=True,
                with_vectors=True,
            )
            assert len(records) == 1
            assert records[0].vector == pytest.approx([0.6, 0.8, 0.0])
            assert records[0].payload["published_at"] == "2026-08-13T01:02:03+00:00"
            assert records[0].payload["knowledge_base_id"] == str(knowledge_base_id)
            assert records[0].payload["index_schema_version"] == "v3"
        finally:
            aliases = await client.get_aliases()
            if any(alias.alias_name == settings.collection_alias for alias in aliases.aliases):
                await client.update_collection_aliases(
                    [
                        models.DeleteAliasOperation(
                            delete_alias=models.DeleteAlias(
                                alias_name=settings.collection_alias
                            )
                        )
                    ]
                )
            if await client.collection_exists(settings.collection_name):
                await client.delete_collection(settings.collection_name)
            await client.close()

    asyncio.run(verify())
