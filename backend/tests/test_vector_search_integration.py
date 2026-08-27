"""显式启用后执行真实 Ollama 到远程 Qdrant current Alias 的只读搜索。

本测试不会调用 Collection lifecycle，不创建、切换或删除 Alias，不写 Point，也不访问
PostgreSQL。默认跳过；只有操作者确认本地 .env 指向可读服务并设置开关后才执行。
"""

import asyncio
import math
import os

import pytest

from news_vector_service.config.ollama_embedding import OllamaEmbeddingSettings
from news_vector_service.config.qdrant import QdrantSettings
from news_vector_service.qdrant.runtime import VectorSearchRuntime
from news_vector_service.schemas.vector_search import VectorSearchRequest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_VECTOR_SEARCH_INTEGRATION_TEST") != "1",
    reason=(
        "set RUN_VECTOR_SEARCH_INTEGRATION_TEST=1 to perform one read-only Ollama "
        "and Qdrant current Alias search"
    ),
)


def test_real_vector_search_is_read_only_and_accepts_empty_results() -> None:
    """使用无敏感短 query，只验证真实调用契约，不假设 Alias 中一定已有 Point。"""

    async def verify() -> None:
        qdrant_settings = QdrantSettings()
        ollama_settings = OllamaEmbeddingSettings()
        runtime = VectorSearchRuntime.build(
            qdrant_settings,
            ollama_settings,
        )
        try:
            results = await runtime.service.search(
                VectorSearchRequest(query="利率政策", top_k=3)
            )
            assert len(results) <= 3
            assert all(math.isfinite(result.score) for result in results)
            assert all(result.embedding_model == runtime.spec.embedding_model for result in results)
            assert all(
                result.index_schema_version == runtime.spec.schema_version
                for result in results
            )
        finally:
            await runtime.close()

    asyncio.run(verify())
