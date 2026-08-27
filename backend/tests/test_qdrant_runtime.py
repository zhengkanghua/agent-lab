"""阶段 2 标准 Runtime 组装入口的离线测试。"""

import asyncio
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient

from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.qdrant.runtime import (
    DocumentIndexingRuntime,
    VectorSearchRuntime,
)


def run(coroutine: Any) -> Any:
    """执行一个测试协程。"""

    return asyncio.run(coroutine)


def test_runtime_build_uses_one_shared_index_spec_and_alias() -> None:
    qdrant_settings = QdrantSettings(_env_file=None, environment="runtime_test")
    ollama_settings = OllamaEmbeddingSettings(_env_file=None)
    client = AsyncQdrantClient(location=":memory:")

    async def verify() -> None:
        runtime = DocumentIndexingRuntime.build(
            qdrant_settings,
            ollama_settings,
            client=client,
        )
        try:
            assert runtime.spec.dimension == 1024
            assert runtime.spec.distance.value == "Cosine"
            assert runtime.spec.embedding_model == "bge-m3:567m"
            assert runtime.service._point_store.collection_name == (  # noqa: SLF001
                "news_chunks_runtime_test_current"
            )
            assert runtime.service._chunk_pipeline.encoding_name == (  # noqa: SLF001
                runtime.spec.tokenizer
            )
            assert not hasattr(runtime, "search_service")
        finally:
            await runtime.close()

    run(verify())


def test_read_only_runtime_contains_no_lifecycle_or_point_store() -> None:
    qdrant_settings = QdrantSettings(_env_file=None, environment="search_runtime_test")
    ollama_settings = OllamaEmbeddingSettings(_env_file=None)
    client = AsyncQdrantClient(location=":memory:")

    async def verify() -> None:
        runtime = VectorSearchRuntime.build(
            qdrant_settings,
            ollama_settings,
            client=client,
        )
        try:
            assert runtime.spec.dimension == 1024
            assert runtime.service._vector_search.collection_name == (  # noqa: SLF001
                "news_chunks_search_runtime_test_current"
            )
            assert runtime.service._embedding_provider is runtime.embedding_provider  # noqa: SLF001
            assert not hasattr(runtime, "lifecycle")
            assert not hasattr(runtime, "point_store")
            assert not hasattr(runtime, "ensure_ready")
        finally:
            await runtime.close()

    run(verify())


def test_read_only_runtime_close_attempts_both_clients_and_preserves_first_error() -> None:
    events: list[str] = []

    @dataclass
    class FailingCloser:
        name: str

        async def close(self) -> None:
            events.append(self.name)
            raise RuntimeError(f"{self.name} 关闭失败")

    runtime = VectorSearchRuntime(
        client=FailingCloser("qdrant"),  # type: ignore[arg-type]
        service=object(),  # type: ignore[arg-type]
        spec=object(),  # type: ignore[arg-type]
        embedding_provider=FailingCloser("embedding"),  # type: ignore[arg-type]
    )

    async def verify() -> None:
        try:
            await runtime.close()
        except RuntimeError as exc:
            assert str(exc) == "embedding 关闭失败"
            assert exc.__notes__ == [
                "此外关闭 Qdrant 客户端也失败：RuntimeError。"
            ]
        else:
            raise AssertionError("runtime.close() 应保留第一个关闭错误")

    run(verify())
    assert events == ["embedding", "qdrant"]
