"""把已持久化文档转换为 Chunk，并调用 Ollama 生成内存 Embedding。"""

from news_vector_service.pipeline.document_chunk_pipeline import DocumentChunkPipeline
from news_vector_service.pipeline.ollama_embedding_provider import (
    ChunkEmbedding,
    OllamaEmbeddingProvider,
)
__all__ = [
    "ChunkEmbedding",
    "DocumentChunkPipeline",
    "OllamaEmbeddingProvider",
]
