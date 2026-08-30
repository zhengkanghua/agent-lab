"""描述一套「可被 Qdrant 接受的向量索引契约」。

为什么需要这套契约：向量索引里「存什么模型产出的向量、多少维、用什么相似度度量」
必须全局一致。VectorIndexSpec 把这些集中记录：模型名（bge-m3:567m）、维度（1024）、
距离度量（Cosine）、Chunk 切分参数、Payload 版本。

它不是数据库表，也不创建 Collection——它只是一个「规格书」，被三类组件使用：
生命周期组件按它创建/校验 Qdrant 物理 Collection，Point mapper 按它写审计字段，
Service 按它校验一致性。维度校验仍以真实 Embedding 响应为准。
"""

from dataclasses import dataclass
from typing import Any

from qdrant_client.http import models

from agent_lab.config.qdrant import QdrantSettings
from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.pipeline.document_chunker import DocumentChunker


class VectorIndexConfigurationError(RuntimeError):
    """Qdrant Collection 与代码期望的索引契约不一致。"""


@dataclass(frozen=True, slots=True)
class VectorIndexSpec:
    """一套不可变的「向量/Collection/Payload」规格（纯内存值对象）。

    ``schema_version`` 代表「整个索引空间」的版本，而不是单纯数据库迁移版本：
    只要模型、维度、Distance、tokenizer、Chunk 参数或 Payload 契约有一个不兼容，
    就应该开新版本 + 新 Collection（老数据还能留在旧 Collection 里）。
    实例不可变（frozen），可在多个异步任务之间安全共享。
    """

    schema_version: str = "v1"
    embedding_model: str = "bge-m3:567m"
    dimension: int = 1024
    distance: models.Distance = models.Distance.COSINE
    tokenizer: str = DocumentChunker.DEFAULT_ENCODING_NAME
    chunk_size: int = DocumentChunker.DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DocumentChunker.DEFAULT_CHUNK_OVERLAP
    payload_schema_version: str = "v1"

    @classmethod
    def from_settings(
        cls,
        qdrant_settings: QdrantSettings,
        ollama_settings: OllamaEmbeddingSettings,
    ) -> "VectorIndexSpec":
        """由两个独立 Settings 组装当前运行时索引规格。

        Args:
            qdrant_settings: Qdrant 地址、版本、维度和 Distance 配置。
            ollama_settings: 实际 Embedding 模型配置；document/query 必须共用它。

        Returns:
            与当前服务配置一致的不可变索引规格。

        Raises:
            ValueError: Qdrant Distance 或其他规格字段不满足约束时抛出。

        Notes:
            该方法只组合内存配置，不发起 Ollama、Qdrant、数据库或向量库 I/O。
        """

        try:
            distance = models.Distance(qdrant_settings.distance)
        except ValueError as exc:
            raise ValueError(
                f"不支持的 Qdrant 距离度量 {qdrant_settings.distance!r}。"
            ) from exc
        return cls(
            schema_version=qdrant_settings.collection_schema_version,
            embedding_model=ollama_settings.embedding_model,
            dimension=qdrant_settings.vector_dimension,
            distance=distance,
        )

    def __post_init__(self) -> None:
        """在构造时校验规格自身，让错误配置在这里就失败，而不是流到 Collection 生命周期操作。"""

        if not self.schema_version.startswith("v") or not self.schema_version[1:].isdigit():
            raise ValueError("schema_version 必须形如 v1 或 v2")
        if not self.embedding_model.strip():
            raise ValueError("embedding_model 不能为空白")
        if self.dimension < 1:
            raise ValueError("dimension 必须大于零")
        if self.chunk_size < 1 or self.chunk_overlap < 0:
            raise ValueError("chunk 参数必须为正数或非负数")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        if not self.payload_schema_version.startswith("v"):
            raise ValueError("payload_schema_version 必须以 v 开头")

    @property
    def vector_params(self) -> models.VectorParams:
        """返回 Qdrant 创建 Collection 所需的 dense Vector 参数。"""

        return models.VectorParams(size=self.dimension, distance=self.distance)

    @property
    def collection_metadata(self) -> dict[str, Any]:
        """返回写入 Collection metadata 的可审计规格快照。"""

        return {
            "schema_version": self.schema_version,
            "embedding_model": self.embedding_model,
            "dimension": self.dimension,
            "distance": self.distance.value,
            "tokenizer": self.tokenizer,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "payload_schema_version": self.payload_schema_version,
        }

    def validate_collection_info(self, info: models.CollectionInfo) -> None:
        """校验已存在 Collection 的维度、Distance 和管理元数据。

        Args:
            info: Qdrant ``get_collection`` 返回的 Collection 信息。

        Raises:
            VectorIndexConfigurationError: 实际 Collection 与规格不一致。

        Notes:
            只读 Collection 元数据和向量配置，不进行网络 I/O。发现不一致时故意
            停止，而不是自动删除或修改已有数据，避免错误覆盖生产索引。
        """

        # 1、校验「向量配置」：维度与 Distance 必须和规格一致
        vectors = info.config.params.vectors

        # 本项目约定每个 Point 只存一个未命名稠密向量。Qdrant 用 named vectors 时
        # params.vectors 是 dict（形如 {"title": VectorParams}），因此 dict 即表示
        # 集合结构与规格不符，直接拒绝而不去猜该取哪个向量。
        if isinstance(vectors, dict):
            raise VectorIndexConfigurationError(
                "应只有一个未命名稠密向量，但集合使用了命名向量。"
            )
        if vectors is None or vectors.size != self.dimension:
            actual_dimension = None if vectors is None else vectors.size
            raise VectorIndexConfigurationError(
                f"向量维度不匹配：期望 {self.dimension}，"
                f"实际 {actual_dimension}。"
            )
        if vectors.distance != self.distance:
            raise VectorIndexConfigurationError(
                f"距离度量不匹配：期望 {self.distance.value}，"
                f"实际 {vectors.distance.value}。"
            )

        # 2、核对写入 Collection 的 metadata 快照，防止误用错模型/错版本的索引
        actual_metadata = info.config.metadata or {}
        for key, expected in self.collection_metadata.items():
            if actual_metadata.get(key) != expected:
                raise VectorIndexConfigurationError(
                    f"集合元数据不匹配（{key!r}）：期望 {expected!r}，"
                    f"实际 {actual_metadata.get(key)!r}。"
                )
