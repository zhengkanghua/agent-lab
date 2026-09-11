"""显式启用后才访问真实 Ollama 的只读 Embedding 集成测试。"""

import asyncio
import json
import math
import os
from pathlib import Path

import httpx
import pytest

from agent_lab.config.document_processing import DocumentProcessingSettings
from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings, build_ollama_headers
from agent_lab.knowledge.adapters.docling_chunker import DoclingStructuredChunker
from agent_lab.knowledge.adapters.docling_parser import DoclingDocumentParser
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


def test_real_bge_token_count_matches_frozen_chunk_input() -> None:
    """用真实服务计数核对包含标题和特殊 token 的冻结输入，并禁止服务端截断。"""
    settings = OllamaEmbeddingSettings()
    if not settings.embedding_model.startswith("bge-m3"):
        pytest.skip("该验收比较锁定的 BGE-M3 tokenizer，需要对应的 Ollama 模型。")
    processing = DocumentProcessingSettings()
    chunker = DoclingStructuredChunker(
        tokenizer_path=processing.tokenizer_path, max_tokens=processing.chunk_max_tokens,
    )
    document = DoclingDocumentParser().parse(
        ("# 运维手册\n\n## 备份恢复\n\n" + "检查 backup 完整性，再执行 restore。" * 120).encode("utf-8"),
        mime_type="text/markdown", title="运维手册",
    )
    result = chunker.build_chunks(document)
    assert result.chunks and not result.issues
    chunk = max(result.chunks, key=lambda item: item.token_count)
    assert chunk.headings and chunk.token_count <= processing.chunk_max_tokens

    async def verify() -> None:
        async with httpx.AsyncClient(
            base_url=str(settings.base_url), headers=build_ollama_headers(settings.api_key),
            timeout=settings.embedding_request_timeout_seconds,
        ) as client:
            # 生产 SDK 只返回向量；验收直接读取同一接口的计数字段，不把计数工具放进生产流程。
            response = await client.post("/api/embed", json={
                "model": settings.embedding_model, "input": [chunk.embedding_text], "truncate": False,
            })
        assert response.status_code == 200
        data = response.json()
        assert data["prompt_eval_count"] == chunk.token_count
        vectors = data["embeddings"]
        assert len(vectors) == 1 and vectors[0]
        assert all(math.isfinite(value) for value in vectors[0])
        report = {
            "model": settings.embedding_model, "budget": processing.chunk_max_tokens,
            "local_tokens": chunk.token_count, "server_tokens": data["prompt_eval_count"],
            "dimensions": len(vectors[0]), "server_truncation": False,
        }
        output = Path(".pytest_cache/docling-tokenizer-report.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    asyncio.run(verify())
