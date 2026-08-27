"""通过 Ollama 把 LangChain Chunk 正文转换成经过校验的 Embedding 向量。

Embedding = 模型把一段文本映射成一组浮点数。核心特点：语义越相近的文本，它们的
向量就越"靠近"（距离小/角度小）；但单个坐标并不对应某个人类能读懂的词义。

本模块位于 pipeline 层，是业务 Pipeline 与 Ollama 客户端之间的「唯一样板」：
统一创建官方 ``OllamaEmbeddings``、异步分批调用、把远程错误分类、校验响应向量。
它不切分文档、不生成摘要或回答、不连 PostgreSQL、不保存向量，也不含 Qdrant/检索逻辑。
"""

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Never

import httpx
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from ollama import ResponseError

from agent_lab.config.ollama_embedding import (
    OllamaEmbeddingSettings,
    build_ollama_headers,
)


class OllamaEmbeddingError(RuntimeError):
    """Ollama Embedding 调用或响应不满足契约时的公共异常基类。"""


class OllamaAuthenticationError(OllamaEmbeddingError):
    """反向代理拒绝凭据，且异常文本不会包含 API Key。"""


class OllamaConnectionError(OllamaEmbeddingError):
    """客户端无法连接到配置的 Ollama 服务。"""


class OllamaTimeoutError(OllamaEmbeddingError):
    """Ollama 请求超过配置的总等待时间。"""


class OllamaModelNotFoundError(OllamaEmbeddingError):
    """Ollama 服务不存在配置的 Embedding 模型。"""


class OllamaServiceError(OllamaEmbeddingError):
    """Ollama 或反向代理返回其他失败响应。"""


class EmbeddingResponseError(OllamaEmbeddingError):
    """远程响应无法形成可靠的同维有限数值向量。"""


# @dataclass 用来高效定义不可变且省内存数据类的注解。其中 frozen=True 让对象变成只读（不可修改），而 slots=True 通过取消每个实例的字典来节省内存并加快属性访问。
# frozen=True 对象创建之后，不能修改他的属性
# slots=True 不再为每个实例动态创建 __dict__字典，减少内存
@dataclass(frozen=True, slots=True)
class ChunkEmbedding:
    """一个 LangChain Chunk 与其内存 Embedding 的稳定映射。

    ``chunk_id`` 来自 LangChain Chunk ``Document.id``，用于后续阶段关联派生数据；
    ``embedding`` 只来自该 Chunk 的 ``page_content``，ID 与 Metadata 均不进入模型。
    对象只在内存中生存，本阶段不会把它写入 PostgreSQL、文件或向量数据库。
    """

    chunk_id: str
    embedding: list[float]


class OllamaEmbeddingProvider:
    """隔离业务 Pipeline 与具体 Ollama 客户端的异步 Embedding Provider。

    为什么需要它：如果业务层到处直接 new ``OllamaEmbeddings``，模型名/认证头/超时/
    批量/校验规则就会散落各处，容易遗漏数据检查。Provider 把这些规则集中起来。

    两个关键概念：
    - document embedding：给「待检索资料」（存的新闻 Chunk）用；
    - query embedding：给「用户检索输入」（搜索时的问题）用。
    两者语义角色不同，但必须由「同一个模型」映射到「同一个向量空间」，否则距离
    不可比。本阶段只生成两者的向量，不计算相似度、不保存结果。

    一个实例在同一进程内长期复用；官方客户端分别持有同步和异步 httpx client，
    本类只调原生异步 API。实例可被多个 asyncio Task 使用，维度写入由短临界区保护，
    网络请求不会在锁内串行（不会因为加锁而拖慢并发）。
    """

    def __init__(
        self,
        settings: OllamaEmbeddingSettings,
        *,
        embeddings: Embeddings | None = None,
    ) -> None:
        """创建 Provider，并在未注入测试替身时构造官方 LangChain 客户端。

        Args:
            settings: 已校验的 Ollama Embedding 独立配置。
            embeddings: 可选的 LangChain ``Embeddings`` 实现；生产环境省略，离线
                测试可注入 fake，避免访问真实 Ollama。

        Notes:
            构造过程不发起网络请求。timeout 和认证 header 通过 ``client_kwargs``
            同时传给当前 1.1.0 版本的同步/异步 Ollama 客户端，但 Provider 只使用
            ``aembed_query`` 和 ``aembed_documents``。
        """

        self._settings = settings
        if embeddings is not None:
            self._embeddings = embeddings
            self._owns_embeddings = False
        else:
            ollama_embeddings = OllamaEmbeddings(
                model=settings.embedding_model,
                base_url=str(settings.base_url),
                client_kwargs={
                    "headers": build_ollama_headers(settings.api_key),
                    "timeout": httpx.Timeout(
                        settings.embedding_request_timeout_seconds
                    ),
                },
            )
            # 清空这份"可 repr 的参数副本"避免打印出 Authorization
            ollama_embeddings.client_kwargs = {}
            self._embeddings = ollama_embeddings
            self._owns_embeddings = True
        self._dimension: int | None = None
        self._dimension_lock = asyncio.Lock()

    @property
    def dimension(self) -> int | None:
        """返回本实例已由真实响应确认的维度，尚未成功调用时返回 ``None``。"""

        return self._dimension

    @property
    def embedding_model(self) -> str:
        """返回 document/query 共用的 Ollama Embedding 模型名称。"""

        return self._settings.embedding_model

    async def close(self) -> None:
        """关闭 Provider 自己创建的 Ollama 同步/异步 HTTP client。

        Notes:
            本方法只释放本地连接池，不进行 Embedding、数据库或向量库 I/O。测试注入
            的 ``Embeddings`` 生命周期仍由注入方负责；重复调用底层 httpx close 安全。
            当前实现依据 ``langchain-ollama==1.1.0`` 的私有 client 持有方式和
            ``ollama==0.6.2`` 的公开 ``close`` API，升级依赖时必须重新核实。
        """

        if not self._owns_embeddings or not isinstance(
            self._embeddings,
            OllamaEmbeddings,
        ):
            return
        async_client = self._embeddings._async_client  # noqa: SLF001
        sync_client = self._embeddings._client  # noqa: SLF001
        async_error: Exception | None = None
        try:
            if async_client is not None:
                await async_client.close()
        except Exception as exc:
            async_error = exc
        finally:
            if sync_client is not None:
                sync_client.close()
        if async_error is not None:
            raise async_error

    async def embed_query(self, text: str) -> list[float]:
        """
        将一个text向量化

        Args:
            text: 用于当前 Vector Search 的查询文本；必须包含非空白字符。

        Returns:
            与 document embedding 处于同一模型空间的有限浮点数向量。

        Raises:
            ValueError: 输入为空或只包含空白字符。
            OllamaAuthenticationError: 服务拒绝凭据。
            OllamaConnectionError: 无法连接远程服务。
            OllamaTimeoutError: 请求超时。
            OllamaModelNotFoundError: 配置模型不存在。
            OllamaServiceError: 服务返回其他错误。
            EmbeddingResponseError: 向量为空、含非法数值或维度不一致。

        Notes:
            本方法进行一次远程网络与 Embedding I/O，不进行数据库或向量库 I/O。
            query 与 document 使用同一个模型非常重要，否则后续无法可靠比较距离。
        """

        # 1. 前置校验 query 文本非空（不发无效请求） 校验非空
        normalized_text = self._validate_text(text, context="query")
        try:
            # 2. 调 Ollama 把文本变成向量（网络 I/O） aembed_query 只传入单条返回单条
            vector = await self._embeddings.aembed_query(normalized_text)
        except Exception as exc:
            self._raise_mapped_error(exc)
        # 3. 校验返回（数量/有限值/非零范数/维度），并校正内部记录的维度
        validated = self._validate_vectors([vector], expected_count=1)
        await self._record_dimension(len(validated[0]))
        return validated[0]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """
        批量embedding

        按配置批量为 document 文本生成向量并保持输入顺序。

        Args:
            texts: 文档或 Chunk 正文序列；每项必须包含非空白字符。

        Returns:
            与输入一一对应、顺序不变的向量列表。空输入直接返回空列表。

        Raises:
            ValueError: 任一文本为空或只包含空白字符。
            OllamaAuthenticationError: 服务拒绝凭据。
            OllamaConnectionError: 无法连接远程服务。
            OllamaTimeoutError: 请求超时。
            OllamaModelNotFoundError: 配置模型不存在。
            OllamaServiceError: 服务返回其他错误。
            EmbeddingResponseError: 数量、数值或同批/跨批维度不满足契约。

        Notes:
            空列表不会产生网络调用。非空输入进行远程网络与 Embedding I/O，不进行
            数据库或向量库 I/O。batch size 越大通常吞吐越高，但单次延迟、显存占用
            和超时风险也会增加，所以必须使用配置值而非在业务逻辑中写死。
        """

        # 1. 先校验所有文本非空，收集待向量化列表
        normalized_texts = [
            self._validate_text(text, context=f"document[{index}]")
            for index, text in enumerate(texts)
        ]
        if not normalized_texts:
            return []

        # 2. 按配置的 batch_size 分批调 Ollama（防止一次塞太多，延迟/显存/超时失控）
        all_vectors: list[list[float]] = []
        call_dimension: int | None = None
        # 根据配置分批处理
        batch_size = self._settings.embedding_batch_size
        for start in range(0, len(normalized_texts), batch_size):
            batch = normalized_texts[start : start + batch_size]
            try:
                # 调用LangChain ollama 批量向量化接口
                raw_vectors = await self._embeddings.aembed_documents(batch)
            except Exception as exc:
                self._raise_mapped_error(exc)

            # 向量 校验
            vectors = self._validate_vectors(raw_vectors, expected_count=len(batch))

            # 3. 跨批对比维度：不同 HTTP 请求若返回不同维度，说明模型/代理配置漂移，
            #    不能把两个向量空间的数据混在一起
            # 检查"上一批返回的向量是几维"和"这一批返回的是几维"是否一致
            batch_dimension = len(vectors[0])
            # 上一批的向量维度 call_dimension
            if call_dimension is not None and batch_dimension != call_dimension:
                raise EmbeddingResponseError(
                    "Ollama 不同批次返回的嵌入维度不一致："
                    f"期望 {call_dimension}，实际 {batch_dimension}。"
                )
            call_dimension = batch_dimension
            all_vectors.extend(vectors)

        # 4. 记下本次调用确认的真实维度
        await self._record_dimension(call_dimension)
        return all_vectors

    async def probe_dimension(self, text: str = "这是一个向量维度探测") -> int:
        """
        真实第哦啊用，获取当前模型实际输出多少维

        用于测试/运维/未来
        
        使用真实 query 响应长度探测当前模型向量维度。

        Args:
            text: 发送给 Ollama 的短探测文本，不应包含敏感信息。

        Returns:
            服务实际返回的非空向量长度。

        Raises:
            ValueError: 探测文本为空。
            OllamaEmbeddingError: 网络、服务或响应验证失败。

        Notes:
            本方法进行远程网络与 Embedding I/O。模型版本和服务配置可能改变输出维度，
            所以不能把资料中的 BGE-M3 维度硬编码成永久约束。
        """

        vector = await self.embed_query(text)
        return len(vector)

    async def embed_chunks(self, chunks: Sequence[Document]) -> list[ChunkEmbedding]:
        """批量生成 Chunk 向量，并保留每项稳定 Chunk ID。

        "把一批 LangChain Chunk(文档片段)直接变成 (chunk_id, 向量) 对"的高层便捷方法

        Args:
            chunks: ``DocumentChunker`` 输出的 LangChain Chunk Document 序列。
                ``id`` 必须非空；只有 ``page_content`` 会进入 Embedding。

        Returns:
            按 Chunk 输入顺序排列的 ``ChunkEmbedding`` 列表，ID 与向量一一对应。

        Raises:
            ValueError: 任一 Chunk 缺少 ID 或正文为空。
            OllamaEmbeddingError: 远程调用或响应验证失败。

        Notes:
            本方法进行远程网络与 Embedding I/O，但不进行数据库或向量库 I/O。
            Metadata 不进入模型；本阶段返回内存结果后即结束，不保存向量。
        """

        chunk_ids: list[str] = []
        for index, chunk in enumerate(chunks):
            # chunk的id要非空
            if chunk.id is None or not chunk.id.strip():
                raise ValueError(
                    f"在进行嵌入（embedding）之前，必须设置 chunk[{index}].id。"
                )
            chunk_ids.append(chunk.id)

        # 只把 Chunk 的 page_content 交给模型；id/metadata 不进向量（关联靠返回的 chunk_id 保持）。批量生成后按 zip(strict) 保证 id 与向量一一对应。
        # vectors 是一组一组的 vector
        vectors = await self.embed_documents([chunk.page_content for chunk in chunks])
        # 将chunk_ids和vectors 进行配对绑定 
        # zip(chunk_ids, vectors, strict=True)  strict=True强校验，一个chunk_ids就匹配一个vectors
        return [
            ChunkEmbedding(chunk_id=chunk_id, embedding=vector)
            for chunk_id, vector in zip(chunk_ids, vectors, strict=True)
        ]

    @staticmethod
    def _validate_text(text: str, *, context: str) -> str:
        """在远程调用前拒绝空文本，同时保留有效正文的原始空白。"""

        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{context} 文本必须包含非空白字符。")
        return text

    @staticmethod
    def _validate_vectors(
        vectors: Sequence[Sequence[object]], *, expected_count: int
    ) -> list[list[float]]:
        """
        向量校验

        验证向量数量、非空性、有限数值、非零范数与同批维度。

        这些校验是为了在向量进 Qdrant 之前就拦下坏数据：数量不对说明 API 行为异常，
        全零/NAN/维度漂移都会污染向量检索结果。
        """

        # 1. 数量必须与请求一致
        if len(vectors) != expected_count:
            raise EmbeddingResponseError(
                "Ollama 返回了意外的嵌入（embedding）数量："
                f"预期 {expected_count}, 得到 {len(vectors)}."
            )

        validated: list[list[float]] = []
        expected_dimension: int | None = None
        # enumerate(vectors) 遍历 vectors 这个列表的同时，自动给你一个从 0 开始的序号（index） 相比直接for in 就有一个序号返回
        for vector_index, vector in enumerate(vectors):
            # 也就是这一组向量没有东西，是空的
            if not vector:
                raise EmbeddingResponseError(
                    f"Ollama 在索引 {vector_index} 处返回了一个空嵌入向量。"
                )
            normalized_vector: list[float] = []
            for value_index, value in enumerate(vector):
                # bool 虽是 int 子类，却不是有意义的向量坐标，因此也明确拒绝。
                # 向量都是int数字来着
                if isinstance(value, bool) or not isinstance(value, Real):
                    raise EmbeddingResponseError(
                        "Ollama 返回了非数值的嵌入值："
                        f"vector {vector_index}, position {value_index}。"
                    )
                number = float(value)
                # 判断值是不是有限，避免出现无穷大和NaN
                if not math.isfinite(number):
                    raise EmbeddingResponseError(
                        "Ollama 返回了一个非有限值 "
                        f"vector {vector_index}, position {value_index}."
                    )
                normalized_vector.append(number)

            dimension = len(normalized_vector)
            # Cosine 空间需要向量具有方向；全零向量即使数值有限也没有可定义的方向。
            # Qdrant 会按 Cosine 语义归一化有效向量，但不能替应用修复全零模型响应。

            # 一组向量全都是0.0  用的是if all， if any就是有一个为0就返回
            if all(number == 0.0 for number in normalized_vector):
                raise EmbeddingResponseError(
                    f"Ollama 在索引 {vector_index} 处返回了零范数嵌入。"
                )

            # 这一组的向量数量，和上一组不一样，就是出现了偏差
            if expected_dimension is not None and dimension != expected_dimension:
                raise EmbeddingResponseError(
                    "Ollama 在同一批次中返回了不一致的嵌入维度："
                    f"期望 {expected_dimension}，实际 {dimension}，"
                    f"位于索引 {vector_index}。"
                )
            expected_dimension = dimension
            validated.append(normalized_vector)
        return validated

    async def _record_dimension(self, dimension: int | None) -> None:
        """原子记录真实维度，并拒绝同一 Provider 生命周期内的模型空间漂移。"""

        if dimension is None:
            return
        async with self._dimension_lock:
            if self._dimension is not None and dimension != self._dimension:
                raise EmbeddingResponseError(
                    "Ollama 嵌入维度在提供程序生命周期内发生了变化："
                    f"expected {self._dimension}, got {dimension}."
                )
            self._dimension = dimension

    @staticmethod
    def _raise_mapped_error(exc: Exception) -> Never:
        """
        自定义异常封装处理

        把底层异常转换为不含 URL 凭据或 API Key 的稳定错误类别。
        """

        if isinstance(exc, httpx.TimeoutException):
            raise OllamaTimeoutError("Ollama 嵌入请求超时。") from None
        # ollama 0.6.2 的 _request_raw 会把 httpx.ConnectError 转换为内置
        # ConnectionError；同时保留 httpx 类型兼容 fake 和未转换的网络错误。
        if isinstance(exc, (ConnectionError, httpx.ConnectError, httpx.NetworkError)):
            raise OllamaConnectionError(
                "无法连接 Ollama 嵌入服务。"
            ) from None
        if isinstance(exc, ResponseError):
            if exc.status_code in {401, 403}:
                raise OllamaAuthenticationError(
                    "Ollama 嵌入身份验证被拒绝。"
                ) from None
            if exc.status_code == 404:
                raise OllamaModelNotFoundError(
                    "未找到配置的 Ollama 嵌入模型。"
                ) from None
            raise OllamaServiceError(
                f"Ollama 嵌入服务返回 HTTP {exc.status_code}。"
            ) from None
        raise OllamaEmbeddingError(
            f"Ollama 嵌入请求失败：{type(exc).__name__}。"
        ) from None
