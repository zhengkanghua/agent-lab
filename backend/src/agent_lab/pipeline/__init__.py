"""把已持久化文档转换为 Chunk，并调用 Ollama 生成内存 Embedding。"""

from agent_lab.pipeline.document_chunk_pipeline import DocumentChunkPipeline
from agent_lab.pipeline.ollama_embedding_provider import (
    ChunkEmbedding,
    OllamaEmbeddingProvider,
)
__all__ = [
    "ChunkEmbedding",
    "DocumentChunkPipeline",
    "OllamaEmbeddingProvider",
]
