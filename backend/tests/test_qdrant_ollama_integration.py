"""显式启用后验证真实 Ollama 到内存 Qdrant Alias 的端到端写入。"""

import asyncio
import math
import os
from uuid import uuid4

import pytest
from langchain_core.documents import Document
from qdrant_client import AsyncQdrantClient

from news_vector_service.config.ollama_embedding import OllamaEmbeddingSettings
from news_vector_service.config.qdrant import QdrantSettings
from news_vector_service.pipeline.ollama_embedding_provider import (
    OllamaEmbeddingProvider,
)
from news_vector_service.qdrant.index_spec import VectorIndexSpec
from news_vector_service.qdrant.lifecycle import QdrantCollectionLifecycle
from news_vector_service.qdrant.store import QdrantChunkStore


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_QDRANT_OLLAMA_INTEGRATION_TEST") != "1",
    reason=(
        "set RUN_QDRANT_OLLAMA_INTEGRATION_TEST=1 to call Ollama and write only "
        "to an in-memory Qdrant"
    ),
)


def test_real_ollama_vector_is_written_through_qdrant_alias() -> None:
    """发送一条无敏感短文本，不创建远程 Qdrant 数据。"""

    async def verify() -> None:
        qdrant_client = AsyncQdrantClient(location=":memory:")
        qdrant_settings = QdrantSettings(
            _env_file=None,
            environment="integration",
            vector_dimension=1024,
            distance="Cosine",
        )
        ollama_settings = OllamaEmbeddingSettings()
        spec = VectorIndexSpec.from_settings(qdrant_settings, ollama_settings)
        provider = OllamaEmbeddingProvider(ollama_settings)
        lifecycle = QdrantCollectionLifecycle(
            qdrant_client,
            qdrant_settings,
            spec,
        )
        store = QdrantChunkStore(qdrant_client, qdrant_settings, spec)
        document_id = str(uuid4())
        chunk = Document(
            id=str(uuid4()),
            page_content="这是一个 Qdrant 向量写入测试",
            metadata={
                "document_id": document_id,
                "source_id": str(uuid4()),
                "source_provider": "integration_test",
                "source_external_id": "feed/integration",
                "document_external_id": "article/integration",
                "content_hash": "a" * 64,
                "document_type": "article",
                "title": "Qdrant 集成测试新闻",
                "url": "https://example.com/integration",
                "source_name": "集成测试来源",
                "authors": [],
                "labels": ["测试"],
                "published_at": "2026-08-13T01:02:03+00:00",
                "chunk_index": 0,
                "chunk_count": 1,
            },
        )
        try:
            await lifecycle.ensure_current_collection()
            embedding = await provider.embed_documents([chunk.page_content])
            result = await store.replace_document_chunks(
                document_id,
                [chunk],
                embedding,
            )
            records, _ = await qdrant_client.scroll(
                collection_name=qdrant_settings.collection_alias,
                with_payload=True,
                with_vectors=True,
            )

            assert result.upserted_ids == (chunk.id,)
            assert len(records) == 1
            assert len(records[0].vector) == 1024
            assert all(math.isfinite(value) for value in records[0].vector)
            assert math.sqrt(sum(value * value for value in records[0].vector)) == pytest.approx(
                1.0,
                abs=1e-5,
            )
            assert records[0].payload["published_at"] == "2026-08-13T01:02:03+00:00"
        finally:
            await qdrant_client.close()

    asyncio.run(verify())
