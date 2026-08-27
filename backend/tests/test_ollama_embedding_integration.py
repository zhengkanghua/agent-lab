"""显式启用后才访问真实 Ollama 的只读 Embedding 集成测试。"""

import asyncio
import math
import os

import pytest

from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.pipeline.ollama_embedding_provider import (
    OllamaEmbeddingProvider,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OLLAMA_INTEGRATION_TEST") != "1",
    reason="set RUN_OLLAMA_INTEGRATION_TEST=1 to call the real Ollama service",
)


def test_real_ollama_returns_stable_finite_vectors() -> None:
    """只发送无敏感短文本，并验证 query 与批量 document 的维度一致。"""

    async def verify() -> None:
        settings = OllamaEmbeddingSettings()
        provider = OllamaEmbeddingProvider(settings)

        query_vector = await provider.embed_query("这是一个向量测试")
        document_vectors = await provider.embed_documents(
            ["这是第一段测试文本", "这是第二段测试文本"]
        )

        dimensions = {len(query_vector), *(len(vector) for vector in document_vectors)}
        assert len(dimensions) == 1
        assert next(iter(dimensions)) > 0
        assert all(
            math.isfinite(value)
            for vector in [query_vector, *document_vectors]
            for value in vector
        )

    asyncio.run(verify())
