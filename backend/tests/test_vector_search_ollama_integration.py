"""显式启用后验证真实 Ollama、内存 Qdrant 与 HTTP Vector Search。

测试使用真实 ``bge-m3:567m`` 分别生成 document/query Embedding，但 Qdrant 固定为
进程内 ``:memory:``，最终通过 ``POST /vector-search`` 查询 current Alias。它不访问
PostgreSQL、不连接远程 Qdrant、不打印文本或 Vector，也不会在默认离线测试中运行。
"""

import asyncio
import math
import os
import warnings
from uuid import uuid4

import httpx
import pytest
from langchain_core.documents import Document
from qdrant_client import AsyncQdrantClient

from news_vector_service.main import create_app
from news_vector_service.config.ollama_embedding import OllamaEmbeddingSettings
from news_vector_service.config.qdrant import QdrantSettings
from news_vector_service.pipeline.ollama_embedding_provider import (
    OllamaEmbeddingProvider,
)
from news_vector_service.qdrant.index_spec import VectorIndexSpec
from news_vector_service.qdrant.lifecycle import QdrantCollectionLifecycle
from news_vector_service.qdrant.runtime import VectorSearchRuntime
from news_vector_service.qdrant.search import QdrantVectorSearch
from news_vector_service.qdrant.store import QdrantChunkStore
from news_vector_service.schemas.vector_search import VectorSearchResult
from news_vector_service.services.vector_search_service import VectorSearchService
from tests.auth_helpers import allow_reader, skip_environment_admin_sync


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_VECTOR_SEARCH_OLLAMA_INTEGRATION_TEST") != "1",
    reason=(
        "set RUN_VECTOR_SEARCH_OLLAMA_INTEGRATION_TEST=1 to call real Ollama "
        "and search only an in-memory Qdrant"
    ),
)


def test_real_query_embedding_searches_in_memory_qdrant_alias_over_http() -> None:
    """真实生成 query/document Vector，并通过 HTTP 验证 current Alias 只读搜索。"""

    async def verify() -> None:
        qdrant_settings = QdrantSettings(
            _env_file=None,
            environment="vector_search_ollama_integration",
            vector_dimension=1024,
            distance="Cosine",
        )
        ollama_settings = OllamaEmbeddingSettings()
        spec = VectorIndexSpec.from_settings(qdrant_settings, ollama_settings)
        client = AsyncQdrantClient(location=":memory:")
        provider = OllamaEmbeddingProvider(ollama_settings)
        lifecycle = QdrantCollectionLifecycle(client, qdrant_settings, spec)
        store = QdrantChunkStore(client, qdrant_settings, spec)
        qdrant_search = QdrantVectorSearch(client, qdrant_settings, spec)
        service = VectorSearchService(
            embedding_provider=provider,
            vector_search=qdrant_search,
            spec=spec,
        )
        chunks: list[Document] = []
        for index, (title, content, labels) in enumerate(
            (
                ("政策利率新闻", "央行公布最新政策利率调整安排。", ["宏观", "利率"]),
                ("体育新闻", "球队公布本周联赛比赛结果。", ["体育"]),
            )
        ):
            document_id = str(uuid4())
            chunks.append(
                Document(
                    id=str(uuid4()),
                    page_content=content,
                    metadata={
                        "document_id": document_id,
                        "source_id": str(uuid4()),
                        "source_provider": "integration_test",
                        "source_external_id": f"feed/{index}",
                        "document_external_id": f"article/{index}",
                        "content_hash": f"{index + 1:x}" * 64,
                        "document_type": "article",
                        "title": title,
                        "url": f"https://example.com/integration/{index}",
                        "source_name": "集成测试来源",
                        "authors": [],
                        "labels": labels,
                        "published_at": "2026-08-14T00:00:00+00:00",
                        "chunk_index": 0,
                        "chunk_count": 1,
                    },
                )
            )

        closed_by_lifespan = False
        try:
            # 本地 Qdrant 会正确执行过滤，但 Payload index 仅对服务端部署有性能作用；
            # 该已知提示与 HTTP/Vector Search 契约无关，避免污染显式验证输出。
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="Payload indexes have no effect",
                    category=UserWarning,
                )
                await lifecycle.ensure_current_collection()
            vectors = await provider.embed_documents(
                [chunk.page_content for chunk in chunks]
            )
            assert all(len(vector) == spec.dimension for vector in vectors)
            for chunk, vector in zip(chunks, vectors, strict=True):
                await store.replace_document_chunks(
                    str(chunk.metadata["document_id"]),
                    [chunk],
                    [vector],
                )

            runtime = VectorSearchRuntime(
                client=client,
                service=service,
                spec=spec,
                embedding_provider=provider,
            )
            app = allow_reader(
                create_app(
                    runtime_factory=lambda: runtime,
                    environment_admin_sync=skip_environment_admin_sync,
                )
            )
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as http_client:
                    response = await http_client.post(
                        "/vector-search",
                        json={"query": "央行利率政策", "top_k": 2},
                    )
            closed_by_lifespan = True

            assert response.status_code == 200
            results = [
                VectorSearchResult.model_validate(item) for item in response.json()
            ]
            assert qdrant_search.collection_name == qdrant_settings.collection_alias
            assert len(results) == 2
            assert [result.score for result in results] == sorted(
                [result.score for result in results],
                reverse=True,
            )
            assert all(math.isfinite(result.score) for result in results)
            assert all(result.embedding_model == spec.embedding_model for result in results)
            assert all(
                result.index_schema_version == spec.schema_version
                for result in results
            )
        finally:
            if not closed_by_lifespan:
                await provider.close()
                await client.close()

    asyncio.run(verify())
