"""通过 current Alias 执行只读 Qdrant 向量搜索。

接收vector和查询条件，返回chunk

向量搜索 = 拿 query 向量（已经是数字了）去向量库里找「最相似」的新闻 Chunk Point。
本模块位于 Qdrant 基础设施层，职责有四：
1. 把应用层的过滤条件（来源/类型/标签/时间）翻译成 Qdrant 的 Filter 结构；
2. 调 AsyncQdrantClient.query_points 发起一次只读查询；
3. 把远程错误分类成稳定异常（认证/连接/超时/目标缺失/配置/响应契约）；
4. 校验返回的每个 Point/Payload 是否符合 v1 契约，转成强类型结果。

本模块是「Qdrant 响应可信度」的信任边界：Qdrant 返回的内容一律当作外部不可信输入，
Point/Payload 契约和文档分组的跨 Chunk 不变量都在这里一次验干净。上层的 Service 与
Pydantic 响应模型不再重复这些校验——它们拿到的数据来自本模块，重复校验不增加安全性，
只会让契约变更需要改多处。

它不调用 Ollama、不读 PostgreSQL、不创建/切换 Alias、不写 Point、不生成 LLM 回答；
物理 Collection 名在本模块不可见——所有查询只能访问 current Alias。
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any, Never
from uuid import UUID

import httpx
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from agent_lab.config.qdrant import QdrantSettings
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.schemas.vector_search import (
    VectorSearchFilters,
    VectorSearchResult,
)


class QdrantVectorSearchError(RuntimeError):
    """Qdrant 只读检索失败的公共异常基类。"""


class QdrantSearchAuthenticationError(QdrantVectorSearchError):
    """Qdrant 拒绝当前凭据，异常文本不会包含 API Key 或响应正文。"""


class QdrantSearchConnectionError(QdrantVectorSearchError):
    """无法连接到配置的 Qdrant 服务。"""


class QdrantSearchTimeoutError(QdrantVectorSearchError):
    """Qdrant 查询超过配置的请求等待时间。"""


class QdrantSearchTargetNotFoundError(QdrantVectorSearchError):
    """current Alias 或它所指向的 Collection 不存在。"""


class QdrantSearchConfigurationError(QdrantVectorSearchError):
    """current Alias 下的 Collection 无法接受当前索引规格查询。"""


class QdrantSearchServiceError(QdrantVectorSearchError):
    """Qdrant 返回未单独分类的服务端或协议错误。"""


class QdrantSearchResponseError(QdrantVectorSearchError):
    """Qdrant 返回的 Point ID、score 或 Payload 不符合搜索响应契约。"""


@dataclass(frozen=True, slots=True)
class QdrantDocumentSearchGroup:
    """Qdrant grouped query 返回的一篇新闻及其相关 Chunk。

    该值对象仍处在基础设施层，保留每个 Chunk 的完整 ``VectorSearchResult``，由
    应用 Service 再映射成公开的 ``DocumentSearchResult``。

    构造它的 ``search_groups`` 已经保证：``matches`` 非空、按 score 降序、组内每个
    Payload 的 document_id 都等于本组 ``document_id``、组内 chunk_id 互不重复、文档级
    元数据组内一致。因此持有该对象的代码可以直接取 ``matches[0]`` 当 best match，
    也可以直接用任一 Chunk 的文档级字段代表整篇文档，无需再次校验。
    """

    document_id: UUID
    matches: tuple[VectorSearchResult, ...]


class QdrantVectorSearch:
    """只通过 current Alias 查询新闻 Chunk，并返回强类型结果。

    实例与应用进程同生命周期，持有可并发复用的异步 Qdrant client 引用，不保存每次
    请求的向量/过滤条件/结果（无状态）。一次 search 只发一个 query_points 只读请求。

    三个「区别」帮你定位它的职责边界：
    - 和 Ollama Provider 的区别：它只收「已经生成好的」query 向量，不管文本；
    - 和 lifecycle 的区别：它永不碰物理 Collection 或 Alias 本身；
    - 和 Service 的区别：它不知道原始 query 文本长什么样。
    """

    # 这些 Payload 字段在 VectorSearchResult 里的类型不是 str（UUID / datetime），
    # 因此 Pydantic 的宽松解析会接受多种输入形态，无法替我们守住「阶段 2 一定写成
    # JSON 字符串」这条约定。详见 _validate_payload_json_types。
    _STRING_ENCODED_PAYLOAD_FIELDS = (
        "document_id",
        "source_id",
        "previous_chunk_id",
        "next_chunk_id",
        "published_at",
        "source_updated_at",
    )
    # 同一个 document 分组内，这些字段描述的是「文档级」事实而不是「Chunk 级」事实，
    # 因此组内每个 Chunk 必须完全一致；Service 只取第一个 Chunk 的值代表整篇文档。
    _GROUP_CONSISTENT_PAYLOAD_FIELDS = (
        "content_hash",
        "title",
        "url",
        "source_name",
        "published_at",
        "authors",
        "labels",
        "chunk_count",
    )

    def __init__(
        self,
        client: AsyncQdrantClient,
        settings: QdrantSettings,
        spec: VectorIndexSpec,
    ) -> None:
        """绑定异步 client、current Alias 和响应规格，不进行网络 I/O。

        Args:
            client: 生产使用的 AsyncQdrantClient，测试可注入只实现 query_points 的 fake。
            settings: 提供 current Alias 与单次请求 timeout；物理名称不会保存到本组件。
            spec: 用于核对结果 Payload 的 Schema 版本和 Embedding 模型。
        """

        self._client = client
        self._collection_alias = settings.collection_alias
        self._request_timeout_seconds = settings.request_timeout_seconds
        self._spec = spec

    @property
    def collection_name(self) -> str:
        """返回实际查询名称；该值始终是 current Alias。"""

        return self._collection_alias

    @property
    def index_spec(self) -> VectorIndexSpec:
        """返回查询结果必须遵守的不可变 VectorIndexSpec。"""

        return self._spec

    async def search(
        self,
        query_vector: Sequence[Real],
        *,
        top_k: int,
        score_threshold: float | None,
        filters: VectorSearchFilters,
    ) -> list[VectorSearchResult]:
        """通过 current Alias 执行一次 dense Vector 最近邻查询。

        Args:
            query_vector: 上层已按 VectorIndexSpec 校验的 query Embedding；不会记录或
                包含在异常中，也不会随响应返回。
            top_k: 最多返回的 Qdrant Point 数，已经由请求模型限制为 1..100。
            score_threshold: 可选 Cosine score 下限；``None`` 表示不设置。
            filters: 已校验的 Payload 过滤条件；不同字段用 AND，labels 用 MatchAny。

        Returns:
            与 Qdrant ``response.points`` 完全相同顺序的 Pydantic 搜索结果列表。

        Raises:
            QdrantSearchAuthenticationError: Qdrant 返回 401 或 403。
            QdrantSearchConnectionError: DNS、TCP、TLS 或网络连接失败。
            QdrantSearchTimeoutError: 客户端或网关查询超时。
            QdrantSearchTargetNotFoundError: current Alias 或 Collection 不存在。
            QdrantSearchConfigurationError: Collection Vector 配置无法接受当前 query。
            QdrantSearchServiceError: 其他远程服务或客户端错误。
            QdrantSearchResponseError: 返回 Point/Payload 不符合当前 v1 契约。

        Notes:
            本方法不执行 PostgreSQL、Ollama 或 Embedding I/O，只执行一次 Qdrant 只读
            query_points 网络 I/O。它不调用 upsert/delete/create_collection/update_alias，
            不自动创建缺失目标，也不在 Python 中过滤、重排或聚合结果。
        """

        # 1、把应用过滤条件翻译成 Qdrant Filter（无过滤时返回 None）
        query_filter = self._build_filter(filters)
        # 2、发起一次只读查询：current Alias + query 向量 + 过滤 + Top-K
        try:
            # 一次只读查询
            response = await self._client.query_points(
                collection_name=self._collection_alias,
                query=[float(value) for value in query_vector],
                query_filter=query_filter,
                limit=top_k,
                # 要 payload（展示用）
                with_payload=True,
                # 不要向量（节省带宽）
                with_vectors=False,
                score_threshold=score_threshold,
                timeout=self._request_timeout_seconds,
            )
        except Exception as exc:
            self._raise_mapped_error(exc)

        # 3、校验响应结构：必须有一个 points 列表
        points = getattr(response, "points", None)
        if not isinstance(points, list):
            raise QdrantSearchResponseError(
                "Qdrant 查询响应必须包含 points 列表。"
            )
        # 4、逐个 Point 校验并转成强类型结果；不重新排序——Qdrant 的 score 顺序就是公开契约
        return [self._map_point(point, index) for index, point in enumerate(points)]

    async def search_groups(
        self,
        query_vector: Sequence[Real],
        *,
        document_limit: int,
        matches_per_document: int,
        score_threshold: float | None,
        filters: VectorSearchFilters,
    ) -> list[QdrantDocumentSearchGroup]:
        """通过 Qdrant 正式 grouped query 返回按文档分组的相关 Chunk。

        分组在 Qdrant 侧按 ``document_id`` 完成，不在 Python 里对 top_k 结果二次去重。

        Args:
            query_vector: 上层已按 VectorIndexSpec 校验的 query Embedding。
            document_limit: 最多返回的不同文档数，直接传给 Qdrant ``limit``。
            matches_per_document: 每组最多返回的 Chunk 数，直接传给 ``group_size``。
            score_threshold: 可选原始 Cosine score 下限。
            filters: 与原始 Chunk 搜索完全相同的 Payload 过滤条件。

        Returns:
            按每组最高 score 降序排列、组内按 score 降序排列的强类型分组列表。

        Raises:
            QdrantSearchResponseError: grouped 响应缺少 groups、组 ID 非 UUID、分组为空、
                重复分组、组内 document_id 不一致、组内 chunk_id 重复、组内文档级元数据
                不一致，或任一 Point 不符合 Payload 契约。
            QdrantVectorSearchError: Qdrant 认证、连接、超时、目标或服务错误。

        Notes:
            本方法只执行一次 ``query_points_groups`` 只读网络 I/O，不访问 PostgreSQL，
            不执行 Point 写入、Alias 生命周期或自动重试。``group_by`` 使用已有的
            ``document_id`` keyword Payload index，避免前端在有限 top_k 上错误去重。

            本方法是分组不变量的唯一后端防线：Qdrant 响应属于不可信输入，必须在此
            一次验干净。下游 Service 只做纯映射、``DocumentSearchResult`` 只做字段级
            约束，都不再重复校验这些跨 Chunk 的关系。
        """

        query_filter = self._build_filter(filters)
        try:
            response = await self._client.query_points_groups(
                collection_name=self._collection_alias,
                group_by="document_id",
                query=[float(value) for value in query_vector],
                # 过滤条件
                query_filter=query_filter,
                limit=document_limit,
                group_size=matches_per_document,
                with_payload=True,
                with_vectors=False,
                score_threshold=score_threshold,
                timeout=self._request_timeout_seconds,
            )
        except Exception as exc:
            self._raise_mapped_error(exc)

        groups = getattr(response, "groups", None)
        if not isinstance(groups, list):
            raise QdrantSearchResponseError(
                "Qdrant 分组查询响应必须包含 groups 列表。"
            )

        mapped_groups: list[QdrantDocumentSearchGroup] = []
        seen_document_ids: set[UUID] = set()
        for group_index, group in enumerate(groups):
            group_id = getattr(group, "id", None)
            try:
                document_id = UUID(str(group_id))
            except (AttributeError, TypeError, ValueError):
                raise QdrantSearchResponseError(
                    f"Qdrant 第 {group_index} 处文档分组的 id 不是 UUID。"
                ) from None
            if document_id in seen_document_ids:
                raise QdrantSearchResponseError(
                    f"Qdrant 分组响应在第 {group_index} 处重复出现了同一文档。"
                )
            seen_document_ids.add(document_id)

            hits = getattr(group, "hits", None)
            if not isinstance(hits, list) or not hits:
                raise QdrantSearchResponseError(
                    f"Qdrant 第 {group_index} 处的文档分组没有命中结果。"
                )
            mapped_matches: list[VectorSearchResult] = []
            seen_chunk_ids: set[UUID] = set()
            for hit_index, point in enumerate(hits):
                result = self._map_point(point, hit_index)
                if result.document_id != document_id:
                    raise QdrantSearchResponseError(
                        f"Qdrant 第 {group_index} 处文档分组中的 Point "
                        "具有不同的 document_id。"
                    )
                # 同一个 Chunk 在一组里出现两次会让 best_match 和 additional_matches
                # 重复展示同一段正文，且下游按 chunk_id 去重会得到比声明更少的片段。
                if result.chunk_id in seen_chunk_ids:
                    raise QdrantSearchResponseError(
                        f"Qdrant 第 {group_index} 处文档分组重复出现了同一个 chunk_id。"
                    )
                seen_chunk_ids.add(result.chunk_id)
                mapped_matches.append(result)

            # 文档级字段必须组内一致：Service 只取第一个 Chunk 的值代表整篇文档，
            # 若组内不一致就会把 A 版本正文的标题/哈希安到 B 版本的片段上。
            first_match = mapped_matches[0]
            for match in mapped_matches[1:]:
                if any(
                    getattr(match, field) != getattr(first_match, field)
                    for field in self._GROUP_CONSISTENT_PAYLOAD_FIELDS
                ):
                    raise QdrantSearchResponseError(
                        f"Qdrant 第 {group_index} 处文档分组内的文档元数据不一致。"
                    )

            mapped_matches.sort(key=lambda result: result.score, reverse=True)
            mapped_groups.append(
                QdrantDocumentSearchGroup(
                    document_id=document_id,
                    matches=tuple(mapped_matches),
                )
            )

        # 排序键取负分数而不是 reverse=True：需要「第一个键降序、第二个键升序」的混合
        # 方向。reverse=True 会把 document_id 也一起翻成 Z→A，而给分数取负号只翻转分数
        # 这一个键。第二个键存在的意义是确定性：两组最高分浮点相等时，按文档 ID 字典序
        # 固定顺序，保证同样输入永远产出同样输出（测试可断言、分页不跳动）。
        mapped_groups.sort(
            key=lambda group: (-group.matches[0].score, str(group.document_id))
        )
        return mapped_groups

    @staticmethod
    def _build_filter(filters: VectorSearchFilters) -> models.Filter | None:
        """把应用过滤契约转换成 Qdrant must 条件。"""

        # 逐个可选条件翻译成 Qdrant 的 FieldCondition，最终拼成一个 must（AND）Filter
        conditions: list[models.FieldCondition] = []
        if filters.source_id is not None:
            conditions.append(
                models.FieldCondition(
                    key="source_id",
                    match=models.MatchValue(value=str(filters.source_id)),
                )
            )
        if filters.source_provider is not None:
            conditions.append(
                models.FieldCondition(
                    key="source_provider",
                    match=models.MatchValue(value=filters.source_provider),
                )
            )
        if filters.document_type is not None:
            conditions.append(
                models.FieldCondition(
                    key="document_type",
                    match=models.MatchValue(value=filters.document_type.value),
                )
            )
        if filters.labels:
            # Qdrant 对 array keyword Payload 的 MatchAny 表示数组中至少一个元素命中；
            # 空 labels 不创建条件，从而明确保持“不过滤”而不是意外返回零条。
            conditions.append(
                models.FieldCondition(
                    key="labels",
                    match=models.MatchAny(any=list(filters.labels)),
                )
            )
        if filters.published_from is not None or filters.published_to is not None:
            conditions.append(
                models.FieldCondition(
                    key="published_at",
                    range=models.DatetimeRange(
                        gte=filters.published_from,
                        lte=filters.published_to,
                    ),
                )
            )
        return models.Filter(must=conditions) if conditions else None

    def _map_point(self, point: Any, result_index: int) -> VectorSearchResult:
        """校验一个 ScoredPoint，并在不泄露正文的情况下报告字段错误。"""

        # 1、Point ID 必须是合法 UUID（它同时就是 Chunk ID）
        point_id = getattr(point, "id", None)
        try:
            canonical_point_id = UUID(str(point_id))
        except (AttributeError, TypeError, ValueError):
            raise QdrantSearchResponseError(
                f"Qdrant 结果第 {result_index} 项的 Point ID 不是 UUID。"
            ) from None

        # 2、score 必须是有限数值（NaN/Infinity 不能进结果）
        score = getattr(point, "score", None)
        if isinstance(score, bool) or not isinstance(score, Real):
            raise QdrantSearchResponseError(
                f"Qdrant 结果第 {result_index} 项的分数不是数值。"
            )
        numeric_score = float(score)
        if not math.isfinite(numeric_score):
            raise QdrantSearchResponseError(
                f"Qdrant 结果第 {result_index} 项的分数不是有限值。"
            )

        # 3、Payload 必须是对象：先做 JSON 类型预检，再交给 Pydantic 完整校验
        payload = getattr(point, "payload", None)
        # payload 必须是"字典类"结构
        if not isinstance(payload, Mapping):
            raise QdrantSearchResponseError(
                f"Qdrant 结果第 {result_index} 项缺少对象形式的 Payload。"
            )
        # 浅拷贝一份（后面要往里加字段，不动原件）
        values = dict(payload)
        self._validate_payload_json_types(values, result_index)
        # 4、补上 Chunk ID 和 score，交给 Pydantic 按 v1 契约整体校验
        values.update(
            {
                "chunk_id": canonical_point_id,
                "score": numeric_score,
            }
        )
        try:
            # model_validate 就是将原始数据转为对象，并且走完整的校验
            result = VectorSearchResult.model_validate(values)
        except ValidationError as exc:
            fields = sorted(
                {
                    str(error["loc"][0])
                    for error in exc.errors(include_url=False, include_context=False)
                    if error["loc"]
                }
            )
            field_context = ", ".join(fields) if fields else "unknown fields"
            # Pydantic ValidationError 的默认文本包含 input_value，可能把完整新闻正文
            # 带进日志；这里只公开字段名和结果位置，不保留第三方原始异常文本。
            raise QdrantSearchResponseError(
                f"Qdrant 结果第 {result_index} 项违反了响应契约，"
                f"涉及字段：{field_context}。"
            ) from None

        # 5、核对 Schema 版本和 Embedding 模型：防止搜到别的索引空间的数据。
        # schema 版本直接读 Payload：它不进 VectorSearchResult（响应里恒等于当前 spec
        # 版本，对调用方是常量），所以这里是它唯一的把关点，缺失或不匹配都要拒绝。
        if values.get("index_schema_version") != self._spec.schema_version:
            raise QdrantSearchResponseError(
                f"Qdrant 结果第 {result_index} 项使用了非预期的索引 "
                "schema 版本。"
            )
        if result.embedding_model != self._spec.embedding_model:
            raise QdrantSearchResponseError(
                f"Qdrant 结果第 {result_index} 项使用了非预期的 "
                "嵌入模型。"
            )
        return result

    @classmethod
    def _validate_payload_json_types(
        cls,
        payload: Mapping[str, Any],
        result_index: int,
    ) -> None:
        """补上 Pydantic 覆盖不到的那一类 Payload 类型漂移检查。

        为什么这里还需要手工检查——Pydantic 管不到的是「目标类型不是 str」的字段：
        ``document_id``/``source_id``/``*_chunk_id`` 声明为 ``UUID``，
        ``published_at``/``source_updated_at`` 声明为 ``datetime``。Pydantic 的宽松模式
        会接受真正的 ``UUID`` 对象，也会把整数/浮点数当成 Unix timestamp 解析成
        ``datetime``。而阶段 2 mapper 约定这些值一律写成 JSON 字符串，所以一个被写坏
        或来自旧 Schema 的 Payload 会被 Pydantic 悄悄「修复」成看似合法的搜索结果。

        其余字段不在这里重复检查：str/AnyHttpUrl/DocumentType 字段上 Pydantic 的 ``str``
        解析已经拒绝全部非字符串 JSON 类型；``chunk_index``/``chunk_count`` 用了
        ``strict=True``；``authors``/``labels`` 有要求 JSON array 的 before-validator。
        缺失字段同样交给结果模型统一报告。
        """

        for field in cls._STRING_ENCODED_PAYLOAD_FIELDS:
            if field in payload and not isinstance(payload[field], str):
                raise QdrantSearchResponseError(
                    f"Qdrant 结果第 {result_index} 项违反了响应契约，"
                    f"涉及字段：{field}。"
                )

    @staticmethod
    def _raise_mapped_error(exc: Exception) -> Never:
        """按认证、连接、超时、目标和配置分类，且不传播远程响应正文。"""

        if isinstance(exc, UnexpectedResponse):
            status_code = exc.status_code
            if status_code in {401, 403}:
                raise QdrantSearchAuthenticationError(
                    "Qdrant 检索身份验证被拒绝。"
                ) from None
            if status_code in {408, 504}:
                raise QdrantSearchTimeoutError("Qdrant 检索请求超时。") from None
            if status_code == 404:
                raise QdrantSearchTargetNotFoundError(
                    "未找到当前 Qdrant Alias 或其指向的集合。"
                ) from None
            if status_code == 400:
                raise QdrantSearchConfigurationError(
                    "当前 Qdrant 集合拒绝了查询向量配置。"
                ) from None
            raise QdrantSearchServiceError(
                f"Qdrant 检索服务返回 HTTP {status_code}。"
            ) from None

        source = exc.source if isinstance(exc, ResponseHandlingException) else exc
        if isinstance(source, ValidationError):
            raise QdrantSearchResponseError(
                "Qdrant 返回的响应违反了客户端响应契约。"
            ) from None
        if isinstance(source, (TimeoutError, httpx.TimeoutException)):
            raise QdrantSearchTimeoutError("Qdrant 检索请求超时。") from None
        if isinstance(
            source,
            (ConnectionError, httpx.ConnectError, httpx.NetworkError),
        ):
            raise QdrantSearchConnectionError(
                "无法连接 Qdrant 检索服务。"
            ) from None
        # 内存 client 对缺失 Collection/Alias 使用 ValueError；远程 REST 使用上面的 404。
        # 只用它判断类别，不把可能包含环境细节的原始 message 转发给调用方。
        if isinstance(source, ValueError) and "not found" in str(source).lower():
            raise QdrantSearchTargetNotFoundError(
                "未找到当前 Qdrant Alias 或其指向的集合。"
            ) from None
        if isinstance(source, ValueError):
            raise QdrantSearchConfigurationError(
                "当前 Qdrant 集合拒绝了检索配置。"
            ) from None
        raise QdrantSearchServiceError("Qdrant 检索请求失败。") from None
