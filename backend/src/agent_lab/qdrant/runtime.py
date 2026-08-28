"""分别组装「文档索引」和「只读向量搜索」两套进程级 Runtime（工具箱）。

本模块是应用层的组件组装入口（composition root）。它把两类工具箱分开，这是刻意
的权限隔离：
- DocumentIndexingRuntime（写）：只组合「写入 + 生命周期」组件——切分、Embedding、
  Qdrant Point Store、Collection/Alias 生命周期；
- VectorSearchRuntime（读）：只组合「query Embedding + current Alias 查询」组件。

读的 Runtime 永远拿不到写的能力（没有 lifecycle、没有 Point Store），从结构上
保证 HTTP 搜索进程不可能误写 Qdrant。二者都集中关闭 Ollama/Qdrant 客户端，不启动
后台 Worker、不接入 LLM，也不在 import 时访问外部服务。只有索引 Runtime 允许显式
执行 ``ensure_ready``；HTTP 搜索 Runtime 从不持有 Collection lifecycle 或 Point Store。
"""

from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient

from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.pipeline.document_chunk_pipeline import DocumentChunkPipeline
from agent_lab.pipeline.document_chunker import DocumentChunker
from agent_lab.pipeline.ollama_embedding_provider import (
    OllamaEmbeddingProvider,
)
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.lifecycle import (
    QdrantCollectionLifecycle,
    build_qdrant_client,
)
from agent_lab.qdrant.search import QdrantVectorSearch
from agent_lab.qdrant.store import QdrantChunkStore
from agent_lab.services.document_indexing_service import (
    DocumentIndexingService,
)
from agent_lab.services.vector_search_service import VectorSearchService


def _build_shared_components(
    qdrant_settings: QdrantSettings,
    ollama_settings: OllamaEmbeddingSettings,
    client: AsyncQdrantClient | None,
) -> tuple[VectorIndexSpec, AsyncQdrantClient, OllamaEmbeddingProvider]:
    """组装两类 Runtime 都需要的规格、Qdrant client 和 Embedding Provider。

    刻意做成模块级函数而不是共同基类：两个 Runtime 的分裂是权限边界，只读 Runtime
    绝不能通过继承意外获得写能力。函数只返回中立零件，由各自的 ``build`` 决定往上
    再装读组件还是写组件。

    Args:
        qdrant_settings: Qdrant URL、current Alias 命名、timeout、维度与 Distance。
        ollama_settings: query/document 共用的 Ollama 模型、URL、timeout 和凭据。
        client: 可选异步 Qdrant client；为 ``None`` 时按配置新建。

    Returns:
        ``(spec, qdrant_client, embedding_provider)`` 三元组。

    Raises:
        ValueError: Settings 无法组成合法 ``VectorIndexSpec``。

    Notes:
        不执行 PostgreSQL、Ollama/Embedding 或 Qdrant I/O，不创建 Collection 或 Alias。
    """

    spec = VectorIndexSpec.from_settings(qdrant_settings, ollama_settings)
    qdrant_client = client or build_qdrant_client(qdrant_settings)
    embedding_provider = OllamaEmbeddingProvider(ollama_settings)
    return spec, qdrant_client, embedding_provider


async def _close_shared_clients(
    embedding_provider: OllamaEmbeddingProvider,
    client: AsyncQdrantClient,
) -> None:
    """依次关闭 Ollama 与 Qdrant client，保证两者都被尝试关闭。

    为什么不用 try/finally 直接抛：先失败的那个才是根因，但另一个仍必须关掉，否则
    连接池泄漏。这里先记下第一个异常，关完第二个再抛，并把第二个失败作为 note 附上。

    Args:
        embedding_provider: 先关闭的 Ollama Provider。
        client: 后关闭的 Qdrant client。

    Raises:
        Exception: 任一 client 关闭失败；保留第一个异常作为根因。

    Notes:
        只释放进程本地 HTTP/gRPC 连接池，不进行 PostgreSQL、Embedding、Qdrant query
        或写操作，也不删除任何远程数据。重复关闭由底层 client 保证安全。
    """

    embedding_error: Exception | None = None
    try:
        await embedding_provider.close()
    except Exception as exc:
        embedding_error = exc
    try:
        await client.close()
    except Exception as client_error:
        if embedding_error is None:
            raise
        embedding_error.add_note(
            "此外关闭 Qdrant 客户端也失败："
            f"{type(client_error).__name__}。"
        )
    if embedding_error is not None:
        raise embedding_error


@dataclass(frozen=True, slots=True)
class VectorSearchRuntime:
    """HTTP 搜索进程持有的「最小只读工具箱」。

    实例与 FastAPI 进程同生命周期：启动时构造（但不连外部服务），每次请求通过
    ``service`` 做 Embedding + 查询。它刻意只装「只读零件」——没有 lifecycle、
    没有 Point Store、没有索引 Service、没有 PostgreSQL Session——从结构上保证
    搜索进程无法执行任何 Qdrant 写操作。多个请求可共享，内部不保存状态。
    """

    client: AsyncQdrantClient
    service: VectorSearchService
    spec: VectorIndexSpec
    embedding_provider: OllamaEmbeddingProvider

    @classmethod
    def build(
        cls,
        qdrant_settings: QdrantSettings,
        ollama_settings: OllamaEmbeddingSettings,
        *,
        client: AsyncQdrantClient | None = None,
    ) -> "VectorSearchRuntime":
        """由同一配置组装 query Provider 与 current Alias 搜索组件。

        Args:
            qdrant_settings: Qdrant URL、current Alias 命名、timeout、维度和 Distance。
            ollama_settings: query/document 共用的 Ollama 模型、URL、timeout 和凭据。
            client: 可选异步 Qdrant client；测试可注入内存 client，返回 Runtime 负责关闭。

        Returns:
            尚未执行外部 I/O、可交给 FastAPI lifespan 管理的只读 Runtime。

        Raises:
            ValueError: Settings 无法组成合法 ``VectorIndexSpec``。
            VectorIndexConfigurationError: Provider、Search 与规格不一致。

        Notes:
            本方法不执行 PostgreSQL、Ollama/Embedding 或 Qdrant I/O，不创建 Collection、
            Payload index 或 Alias。qdrant-client 的构造兼容性探测已在统一 builder 中关闭。
        """

        # 1. 规格 + Qdrant client + Embedding Provider（与写 Runtime 共用的中立零件）
        spec, qdrant_client, embedding_provider = _build_shared_components(
            qdrant_settings,
            ollama_settings,
            client,
        )
        # 2. 只往上装只读零件：只查 current Alias 的搜索组件
        vector_search = QdrantVectorSearch(
            qdrant_client,
            qdrant_settings,
            spec,
        )
        # 3. 用同一个 spec 拼装 Service（构造时就会校验模型一致性）
        service = VectorSearchService(
            embedding_provider=embedding_provider,
            vector_search=vector_search,
            spec=spec,
        )
        return cls(
            client=qdrant_client,
            service=service,
            spec=spec,
            embedding_provider=embedding_provider,
        )

    async def close(self) -> None:
        """关闭 Ollama 与 Qdrant client，不删除或修改任何远程数据。

        Raises:
            Exception: 任一 client 关闭失败；仍会尽力关闭另一个 client，并保留第一个
                异常作为根因。

        Notes:
            本方法只释放进程本地 HTTP/gRPC 连接池，不进行 PostgreSQL、Embedding、
            Qdrant query 或写操作。重复关闭由底层 client 保证安全。
        """

        await _close_shared_clients(self.embedding_provider, self.client)


@dataclass(frozen=True, slots=True)
class DocumentIndexingRuntime:
    """索引/写进程持有的「完整写入工具箱」。

    实例由 ``build`` 创建，构造时不访问网络。使用约定（三步）：
    1. 开始索引前先调 ``ensure_ready()``：创建/校验物理 Collection 与 current Alias；
    2. 用 ``service`` 逐篇索引（切分 → 向量化 → 写入 Qdrant）；
    3. 进程结束时调 ``close()`` 释放连接。

    它不持有 Search Service——写进程不提供读入口，读由独立的 VectorSearchRuntime
    负责。组件使用原生异步 client，应在同一 asyncio 事件循环生命周期内使用；
    每次 PostgreSQL 工作单元仍由调用方提供独立 Session。
    """

    client: AsyncQdrantClient
    lifecycle: QdrantCollectionLifecycle
    service: DocumentIndexingService
    spec: VectorIndexSpec
    embedding_provider: OllamaEmbeddingProvider

    @classmethod
    def build(
        cls,
        qdrant_settings: QdrantSettings,
        ollama_settings: OllamaEmbeddingSettings,
        *,
        client: AsyncQdrantClient | None = None,
    ) -> "DocumentIndexingRuntime":
        """使用同一套配置组装标准文档索引组件。

        Args:
            qdrant_settings: Qdrant URL、Alias 命名、维度、Distance 和写批量配置。
            ollama_settings: Ollama URL、模型、可选密钥、超时和 Embedding 批量配置。
            client: 可选的异步 Qdrant client；生产环境省略，离线测试可注入内存 client。

        Returns:
            尚未访问网络的运行时；``service`` 只用于显式准备后的文档索引。

        Raises:
            ValueError: Settings 无法组成合法 VectorIndexSpec。
            VectorIndexConfigurationError: 标准组件与规格不一致；正常构造不应发生。

        Notes:
            本方法不进行 PostgreSQL、Ollama、Embedding 或 Qdrant I/O。注入 client 后，
            其关闭责任也转移给返回的 runtime，避免连接池泄漏。
        """

        # 1. 规格 + Qdrant client + Embedding Provider（与读 Runtime 共用的中立零件）
        spec, qdrant_client, embedding_provider = _build_shared_components(
            qdrant_settings,
            ollama_settings,
            client,
        )
        # 2. 往上装写路径零件：切分流水线（参数取自 spec）、Collection/Alias 生命周期、
        #    只用 current Alias 的 Point Store
        chunk_pipeline = DocumentChunkPipeline(
            document_chunker=DocumentChunker(
                chunk_size=spec.chunk_size,
                chunk_overlap=spec.chunk_overlap,
                encoding_name=spec.tokenizer,
            )
        )
        lifecycle = QdrantCollectionLifecycle(
            qdrant_client,
            qdrant_settings,
            spec,
        )
        point_store = QdrantChunkStore(
            qdrant_client,
            qdrant_settings,
            spec,
        )
        # 3. 用同一 spec 拼装索引 Service（构造时会校验 Chunk 参数/模型一致性）
        service = DocumentIndexingService(
            chunk_pipeline=chunk_pipeline,
            embedding_provider=embedding_provider,
            point_store=point_store,
            spec=spec,
        )
        return cls(
            client=qdrant_client,
            lifecycle=lifecycle,
            service=service,
            spec=spec,
            embedding_provider=embedding_provider,
        )

    async def ensure_ready(self) -> None:
        """创建或校验物理 Collection、Payload index 和 current Alias。

        Raises:
            QdrantLifecycleError: Qdrant 网络或生命周期操作失败。
            VectorIndexConfigurationError: 已有 Collection 不符合当前规格。

        Notes:
            本方法进行 Qdrant 网络 I/O，不执行 PostgreSQL、Embedding、Point 写入或检索。
            应在部署迁移/启动准备步骤显式调用，而不是在模块 import 时隐式修改外部状态。
        """

        await self.lifecycle.ensure_current_collection()

    async def close(self) -> None:
        """关闭 Ollama 与 Qdrant client，不删除 Collection、Alias 或 Point。

        Raises:
            Exception: 任一 client 关闭失败；仍会尽力关闭另一个 client，并保留第一个
                异常作为根因。

        Notes:
            本方法只释放进程本地网络资源，不进行业务数据库或 Embedding I/O。
        """

        await _close_shared_clients(self.embedding_provider, self.client)
