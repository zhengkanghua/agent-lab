"""显式启用后验证远程 Qdrant Collection/Alias/Point 生命周期并自动清理。"""

import asyncio
import os
from uuid import uuid4

import pytest
from langchain_core.documents import Document
from pydantic import SecretStr
from qdrant_client.http import models

from news_vector_service.config.qdrant import QdrantSettings
from news_vector_service.qdrant.index_spec import VectorIndexSpec
from news_vector_service.qdrant.lifecycle import (
    QdrantCollectionLifecycle,
    build_qdrant_client,
)
from news_vector_service.qdrant.store import QdrantChunkStore


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_QDRANT_REMOTE_INTEGRATION_TEST") != "1",
    reason=(
        "set RUN_QDRANT_REMOTE_INTEGRATION_TEST=1 to create and clean an isolated "
        "test Collection on the configured Qdrant"
    ),
)


def test_remote_qdrant_alias_lifecycle_and_point_round_trip() -> None:
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
            collection_schema_version="v1",
            collection_generation=1,
            vector_dimension=3,
            distance="Cosine",
        )
        spec = VectorIndexSpec(dimension=3)
        client = build_qdrant_client(settings)
        lifecycle = QdrantCollectionLifecycle(client, settings, spec)
        store = QdrantChunkStore(client, settings, spec)
        document_id = str(uuid4())
        chunk = Document(
            id=str(uuid4()),
            page_content="远程 Qdrant 隔离测试文本",
            metadata={
                "document_id": document_id,
                "source_id": str(uuid4()),
                "source_provider": "integration_test",
                "source_external_id": f"feed/{suffix}",
                "document_external_id": f"article/{suffix}",
                "content_hash": "a" * 64,
                "document_type": "article",
                "title": "远程 Qdrant 集成测试新闻",
                "url": "https://example.com/qdrant-integration",
                "source_name": "远程集成测试来源",
                "authors": [],
                "labels": ["测试"],
                "published_at": "2026-08-13T01:02:03+00:00",
                "chunk_index": 0,
                "chunk_count": 1,
            },
        )
        try:
            await lifecycle.ensure_current_collection()
            await store.replace_document_chunks(
                document_id,
                [chunk],
                [[3.0, 4.0, 0.0]],
            )
            records, _ = await client.scroll(
                collection_name=settings.collection_alias,
                with_payload=True,
                with_vectors=True,
            )
            assert len(records) == 1
            assert records[0].vector == pytest.approx([0.6, 0.8, 0.0])
            assert records[0].payload["published_at"] == "2026-08-13T01:02:03+00:00"
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
