"""Qdrant 的索引规格、Payload、生命周期、Point 存储和只读搜索组件。

应用写入与读取统一走 current Alias；只有 Collection 生命周期组件可以操作物理
Collection。当前模块提供阶段 3 Vector Search，但不提供 Retriever、Agent 或 RAG
问答。
"""

from news_vector_service.qdrant.index_spec import VectorIndexSpec
from news_vector_service.qdrant.lifecycle import QdrantCollectionLifecycle
from news_vector_service.qdrant.payload import QdrantPayloadMapper
from news_vector_service.qdrant.search import QdrantVectorSearch
from news_vector_service.qdrant.store import QdrantChunkStore

__all__ = [
    "QdrantChunkStore",
    "QdrantCollectionLifecycle",
    "QdrantPayloadMapper",
    "QdrantVectorSearch",
    "VectorIndexSpec",
]
