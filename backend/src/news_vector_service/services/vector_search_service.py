"""编排「用户 query → Qdrant Chunk 命中」的只读向量检索用例。

先解释向量检索的思路：文章和用户问题都要先变成「同一空间里的数字向量」，然后
比大小——越相似的向量夹角越小。所以搜索分两步：1) 用和入库时完全相同的模型把
query 变成 1024 维向量；2) 拿这个向量去 Qdrant 里找最相似的新闻 Chunk（按
Cosine 相似度打分排序）。

本模块位于应用 Service 层，负责把这两步按正确顺序串起来：调用 Provider 的
``embed_query``、按 ``VectorIndexSpec`` 校验 query 向量、再委托只使用 current Alias
的 Qdrant 组件。它不调用 document embedding、不读取或修改 PostgreSQL、不写 Qdrant、
不改变 processing_status，也不执行 Retriever、生成式 LLM 或 RAG 问答——搜索就是
纯读取，不做任何写操作。
"""

import math
from collections.abc import Sequence
from numbers import Real

from news_vector_service.pipeline.ollama_embedding_provider import (
    OllamaEmbeddingProvider,
)
from news_vector_service.qdrant.index_spec import (
    VectorIndexConfigurationError,
    VectorIndexSpec,
)
from news_vector_service.qdrant.search import QdrantVectorSearch
from news_vector_service.qdrant.search import (
    QdrantDocumentSearchGroup,
    QdrantSearchResponseError,
)
from news_vector_service.schemas.document_search import (
    DocumentSearchMatch,
    DocumentSearchRequest,
    DocumentSearchResult,
)
from news_vector_service.schemas.vector_search import (
    VectorSearchRequest,
    VectorSearchResult,
)


class QueryVectorValidationError(ValueError):
    """query Embedding 无法作为当前 VectorIndexSpec 的有限非零 Vector。"""


class VectorSearchService:
    """把「query 向量化」和「Qdrant 查询」两件事按正确顺序串起来的用例层。

    为什么需要这一层：直接把 Provider 和 Qdrant 组件暴露给 API 层，调用方就要自己
    记住「先 embed_query 再 search」「向量必须 1024 维」「模型必须一致」这些规则。
    本类把这些规则固化在代码里，API 层只需传一个请求对象。实例与应用进程同生命
    周期、由多个并发请求共享；它不保存 query 或结果状态（无状态，天然并发安全）。
    Ollama Provider 只负责文本到向量，Qdrant 组件只负责过滤/查询，本类只负责编排。
    """

    def __init__(
        self,
        *,
        embedding_provider: OllamaEmbeddingProvider,
        vector_search: QdrantVectorSearch,
        spec: VectorIndexSpec,
    ) -> None:
        """绑定同一模型空间中的 Provider、Qdrant 查询组件和索引规格。

        为什么构造时就校验一致性：query 向量和入库向量必须来自同一个模型、同一个
        维度，否则「拿 A 模型的向量去 B 模型的索引里搜」会得到毫无意义的结果。
        这类配置错误越早暴露越好——宁可服务起不来，也不要在线上搜出垃圾结果。

        Args:
            embedding_provider: 只为本用例调用 ``embed_query`` 的 Ollama Provider。
            vector_search: 只通过 current Alias 执行 query_points 的 Qdrant 组件。
            spec: query/document Vector 共用的模型、维度、Distance 与 Schema 契约。

        Raises:
            VectorIndexConfigurationError: Provider 模型或 Qdrant component 规格不一致。

        Notes:
            构造过程不执行 PostgreSQL、Ollama/Embedding 或 Qdrant I/O，也不写外部数据。
        """

        if embedding_provider.embedding_model != spec.embedding_model:
            raise VectorIndexConfigurationError(
                "Ollama 查询嵌入模型与 VectorIndexSpec 不一致："
                f"期望 {spec.embedding_model!r}，实际 "
                f"{embedding_provider.embedding_model!r}。"
            )
        if vector_search.index_spec != spec:
            raise VectorIndexConfigurationError(
                "Qdrant Vector Search 与 "
                "VectorSearchService 使用的 VectorIndexSpec 不一致。"
            )
        self._embedding_provider = embedding_provider
        self._vector_search = vector_search
        self._spec = spec

    async def search(
        self,
        request: VectorSearchRequest,
    ) -> list[VectorSearchResult]:
        """把 query 变成向量，再返回 Qdrant 按相似度排好序的 Chunk 命中。

        执行顺序（两步都成功才算一次完整搜索）：
        1. embed_query(query)：调 Ollama 把问题文本变成 1024 维向量（网络 I/O）；
        2. _validate_query_vector：检查向量维度、数值合法性，拦下坏数据；
        3. vector_search.search(...)：拿向量查 Qdrant current Alias，返回按 score
           排序的命中。

        为什么先校验再查：把「上游返回了垃圾向量」这类问题拦在 Qdrant 调用之前，
        错误信息更明确，也不会浪费一次向量库查询。

        Args:
            request: 已由 Pydantic 校验的 query、Top-K、可选 threshold 和 Payload filters。

        Returns:
            与 Qdrant score 顺序一致的 ``VectorSearchResult`` 列表；同一新闻的多个 Chunk
            可以分别出现，本阶段不聚合、不按时间重排或加权。

        Raises:
            ValueError: 请求不是 ``VectorSearchRequest``，或 query Vector 数值不合法。
            OllamaEmbeddingError: query Embedding 认证、连接、超时、模型或响应失败。
            QdrantVectorSearchError: Qdrant 认证、连接、超时、目标、配置或响应失败。

        Notes:
            本方法不执行 PostgreSQL I/O。它先执行一次 Ollama/Embedding 网络 I/O，
            成功校验后再执行一次 Qdrant current Alias 只读 I/O；不执行任何写操作，
            不自动创建 Collection、不切换 Alias、不修改 processing_status。
        """

        if not isinstance(request, VectorSearchRequest):
            raise TypeError("请求必须是经过验证的 VectorSearchRequest。")
        validated_vector = await self._embed_and_validate_query(request.query)
        # 拿向量去 Qdrant current Alias 查询，保持 Qdrant 的 score 顺序返回
        return await self._vector_search.search(
            validated_vector,
            top_k=request.top_k,
            score_threshold=request.score_threshold,
            filters=request.filters,
        )

    async def search_documents(
        self,
        request: DocumentSearchRequest,
    ) -> list[DocumentSearchResult]:
        """把 query 向量化后按新闻文档分组返回相关片段。

        执行顺序与 ``search`` 相同，但第二步调用 Qdrant 正式 grouped query：
        ``document_limit`` 控制不同文档数量，``matches_per_document`` 控制每组相关
        Chunk 数。该方法不访问 PostgreSQL，因此不会产生全文查询的 N+1 开销。

        Args:
            request: 已由 Pydantic 校验的文档搜索请求。

        Returns:
            按每篇新闻最高 Chunk score 降序排列的 ``DocumentSearchResult`` 列表。

        Raises:
            TypeError: 请求不是 ``DocumentSearchRequest``。
            QueryVectorValidationError: query Embedding 不符合当前索引规格。
            QdrantVectorSearchError: grouped query 上游失败或响应契约非法。

        Notes:
            只执行一次 Ollama query Embedding 和一次 Qdrant grouped 只读查询；完整正文
            必须由调用方稍后请求 ``GET /documents/{document_id}`` 才访问 PostgreSQL。
        """

        if not isinstance(request, DocumentSearchRequest):
            raise TypeError("请求必须是经过验证的 DocumentSearchRequest。")
        validated_vector = await self._embed_and_validate_query(request.query)
        groups = await self._vector_search.search_groups(
            validated_vector,
            document_limit=request.document_limit,
            matches_per_document=request.matches_per_document,
            score_threshold=request.score_threshold,
            filters=request.filters,
        )
        return [self._map_document_group(group) for group in groups]

    async def _embed_and_validate_query(self, query: str) -> list[float]:
        """
        复用 query Embedding 与 VectorIndexSpec 校验，避免多个搜索用例分叉。
        调用 Ollama 把用户的问题(query)文本变成向量（一串 1024个浮点数），并对向量做合法性检查。
        """

        # 这里是唯一的 query Embedding 入口；调用方不会把原文写入日志或外部存储。
        # 这里就是把query转向量
        query_vector = await self._embedding_provider.embed_query(query)
        # 向量再去校验看看有没有问题，没问题返回
        return self._validate_query_vector(query_vector)

    @staticmethod
    def _map_document_group(group: QdrantDocumentSearchGroup) -> DocumentSearchResult:
        """把基础设施分组映射成公开文档 DTO，并校验组内业务元数据一致。"""

        if not group.matches:
            raise QdrantSearchResponseError("Qdrant 文档分组不能为空。")
        first = group.matches[0]
        comparable_fields = (
            "content_hash",
            "title",
            "url",
            "source_name",
            "published_at",
            "authors",
            "labels",
            "chunk_count",
        )
        for match in group.matches[1:]:
            if any(getattr(match, field) != getattr(first, field) for field in comparable_fields):
                raise QdrantSearchResponseError(
                    "Qdrant 文档分组内的文档元数据不一致。"
                )

        matches = [
            DocumentSearchMatch(
                chunk_id=match.chunk_id,
                score=match.score,
                page_content=match.page_content,
                chunk_index=match.chunk_index,
                chunk_count=match.chunk_count,
            )
            for match in group.matches
        ]
        return DocumentSearchResult(
            document_id=group.document_id,
            content_hash=first.content_hash,
            title=first.title,
            url=first.url,
            source_name=first.source_name,
            published_at=first.published_at,
            authors=list(first.authors),
            labels=list(first.labels),
            chunk_count=first.chunk_count,
            best_score=matches[0].score,
            best_match=matches[0],
            additional_matches=matches[1:],
        )

    def _validate_query_vector(self, vector: Sequence[Real]) -> list[float]:
        """在 Qdrant I/O 前验证 query 向量，不把具体坐标写进错误信息。

        校验点：必须是数字序列（拒绝字符串/字节）、维度与索引规格一致、每个值是
        有限数字（拒绝 NaN/Infinity）、L2 范数不为 0。为什么查 L2 范数：一个全零
        向量和任何向量的余弦相似度都无意义，必须在查询前拦截。


        拿这个向量去 Qdrant 向量库里找最相似的 Top-K 条，并带上top_k（返回几条）、score_threshold（最低相似度门槛，低于就不返回）、filters（来源/类型/时间过滤）。

        """

        if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)):
            raise QueryVectorValidationError(
                "查询嵌入响应必须是数值向量序列。"
            )
        if len(vector) != self._spec.dimension:
            raise QueryVectorValidationError(
                f"查询向量维度不匹配：期望 {self._spec.dimension}，"
                f"实际 {len(vector)}。"
            )
        normalized: list[float] = []
        for index, value in enumerate(vector):
            if isinstance(value, bool) or not isinstance(value, Real):
                raise QueryVectorValidationError(
                    f"查询向量第 {index} 位不是数值。"
                )
            number = float(value)
            if not math.isfinite(number):
                raise QueryVectorValidationError(
                    f"查询向量第 {index} 位不是有限值。"
                )
            normalized.append(number)
        l2_norm = math.hypot(*normalized)
        if l2_norm == 0.0:
            raise QueryVectorValidationError("查询向量的 L2 范数为零。")
        if not math.isfinite(l2_norm):
            raise QueryVectorValidationError("查询向量的 L2 范数不是有限值。")
        # Qdrant Cosine Collection 负责 normalization；这里只验证，不重复改变模型输出。
        return normalized
