"""定义 Vector Search 的请求、过滤和结果契约（Pydantic 模型）。

Vector Search（向量检索）把用户 query 变成向量，与 Qdrant 里存好的新闻 Chunk 向量
比较相似度，返回最相似的若干条。本模块只定义「输入长什么样、输出长什么样」，
不做任何实际工作：不生成向量、不构造 Qdrant 过滤条件、不访问网络或数据库、
不生成 LLM 回答。

先记一组会反复出现的术语（后面字段描述里不再重复解释）：
- Point：Qdrant 里一条存储记录 = 一个向量 + 一堆附加字段；
- Payload：Point 上附加的普通 JSON 字段（标题、URL、发布时间等），检索时可按它过滤；
- keyword 匹配：字符串精确相等（不是模糊或包含关系）；
- MatchAny：标签过滤的 OR 语义，命中任意一个就算通过；
- gte/lte：大于等于 / 小于等于（Qdrant 时间过滤的区间写法）；
- Cosine score：余弦相似度分数，范围 [-1, 1]，通常越大越相似；
- 带时区 datetime：带 UTC 偏移的时间，如 2025-01-01T08:00:00+08:00；
- 三重身份：LangChain Chunk（切分后的文档片段）、Qdrant Point（向量库存储对象）、
  PostgreSQL 文档（业务事实来源）是同一篇新闻在三个层级的身份，
  结果字段把它们的稳定关联显式呈现给调用方。
"""

from datetime import datetime
from numbers import Real
from typing import Any
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from agent_lab.domain.enums import DocumentType


DEFAULT_TOP_K = 10
MAX_TOP_K = 100
MAX_QUERY_CHARACTERS = 4096


class VectorSearchFilters(BaseModel):
    """一组可选的检索过滤条件，交给 Qdrant 在向量排序之前先筛掉不匹配的 Point。

    为什么过滤要在 Qdrant 里做而不是取回结果后用 Python 筛：向量库直接在候选集上
    执行过滤，避免把大量数据拉回内存。多个条件之间是 AND（都要满足），labels
    内部是 OR（命中任意一个标签即可）。空 labels 明确表示不过滤；时间范围是包含
    端点的 gte/lte，缺 published_at 的 Point 一旦启用时间过滤就不命中。

    实例只存在于一次搜索请求的内存中，字段来自调用方而不是 PostgreSQL 查询。
    """

    source_id: UUID | None = Field(
        default=None,
        description=(
            "可选来源过滤值，来自调用方；格式为 PostgreSQL sources.id UUID，Qdrant "
            "按 Payload source_id 精确匹配，用于限定单一来源。"
        ),
    )
    source_provider: str | None = Field(
        default=None,
        description=(
            "可选提供方过滤值，来自调用方；非空 keyword 字符串，Qdrant 按 Payload "
            "source_provider 精确匹配，例如 freshrss_main。"
        ),
    )
    document_type: DocumentType | None = Field(
        default=None,
        description=(
            "可选文档类型过滤值，来自调用方；必须是 DocumentType 合法字符串，Qdrant "
            "按 Payload document_type 精确匹配。"
        ),
    )
    labels: tuple[str, ...] = Field(
        default=(),
        description=(
            "可选标签 keyword 列表，来自调用方；空列表表示不过滤，非空时 Qdrant "
            "MatchAny 要求 Point labels 至少包含其中一个值，不要求包含全部值。"
        ),
    )
    published_from: datetime | None = Field(
        default=None,
        description=(
            "可选发布时间下界，来自调用方；必须是带时区 datetime，Qdrant 对 Payload "
            "published_at 执行包含端点的 gte 过滤。"
        ),
    )
    published_to: datetime | None = Field(
        default=None,
        description=(
            "可选发布时间上界，来自调用方；必须是带时区 datetime，Qdrant 对 Payload "
            "published_at 执行包含端点的 lte 过滤。"
        ),
    )

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    @field_validator("source_provider")
    @classmethod
    def normalize_optional_keyword(cls, value: str | None) -> str | None:
        """去除可选 keyword 两端空白，并拒绝纯空白过滤值。

        Args:
            value: 调用方传入的 source provider，或 ``None``。

        Returns:
            ``None`` 或可直接用于 Qdrant keyword 精确匹配的字符串。

        Raises:
            ValueError: 提供了只含空白的 keyword。
        """

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("source_provider 不能只包含空白字符")
        return normalized

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """规范化标签并保持首次出现顺序，空列表仍表示不过滤。

        Args:
            values: 调用方提供的标签序列。

        Returns:
            去除两端空白且去重后的不可变标签元组。

        Raises:
            ValueError: 任一标签只包含空白字符。
        """

        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            label = value.strip()
            if not label:
                raise ValueError("labels 不能包含空值")
            if label not in seen:
                normalized.append(label)
                seen.add(label)
        return tuple(normalized)

    @field_validator("published_from", "published_to", mode="before")
    @classmethod
    def reject_numeric_datetime(cls, value: Any) -> Any:
        """拒绝会被 Pydantic 猜成 Unix timestamp 的数值时间。

        Args:
            value: 调用方传入的 datetime、ISO 字符串、数值或 ``None``。

        Returns:
            原值，交给 Pydantic 继续解析并由下一层 validator 检查时区。

        Raises:
            ValueError: 时间使用整数、浮点数或布尔值，无法显式表达来源时区。
        """

        if isinstance(value, (bool, int, float)):
            raise ValueError(
                "发布时间过滤条件必须是带时区的 datetime，不能是时间戳"
            )
        return value

    @field_validator("published_from", "published_to")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """拒绝没有 UTC offset 的时间边界。

        Args:
            value: Pydantic 已解析的可选 datetime。

        Returns:
            原始带时区 datetime，或 ``None``。

        Raises:
            ValueError: 时间存在但没有时区信息。
        """

        if value is not None and value.utcoffset() is None:
            raise ValueError("发布时间过滤条件必须包含时区信息")
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> "VectorSearchFilters":
        """保证发布时间下界不晚于上界。

        Returns:
            已确认时间范围顺序合法的当前过滤对象。

        Raises:
            ValueError: ``published_from`` 晚于 ``published_to``。
        """

        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_from > self.published_to
        ):
            raise ValueError("published_from 不能晚于 published_to")
        return self


class VectorSearchRequest(BaseModel):
    """一次只读语义检索请求（不含生成式回答参数）。

    请求对象由 API 边界为每次调用创建，校验完成后可安全传给并发共享的
    ``VectorSearchService``。query 被设为不进 repr（不显示在调试输出里），避免
    日志意外记录完整敏感文本；它只会交给 query Embedding，不会写入 PostgreSQL
    或 Qdrant。
    """

    query: str = Field(
        max_length=MAX_QUERY_CHARACTERS,
        repr=False,
        description=(
            "用户提交的检索文本，不可为空、纯空白或超过 4096 个 Unicode 字符；原文"
            "可能敏感，不写日志或异常，仅用于 Ollama query Embedding，不保存为 Payload。"
        ),
    )
    top_k: int = Field(
        default=DEFAULT_TOP_K,
        ge=1,
        le=MAX_TOP_K,
        strict=True,
        description=(
            "调用方请求的最大 Chunk 命中数；不可空，默认 10、范围 1..100，用于限制 "
            "Qdrant limit 和单次返回正文体积。"
        ),
    )
    score_threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
        description=(
            "可选 Qdrant Cosine score 下限；为空时不设阈值，存在时必须是 [-1, 1] "
            "有限数值。它不是概率，生产阈值需用真实新闻评测后决定。"
        ),
    )
    filters: VectorSearchFilters = Field(
        default_factory=VectorSearchFilters,
        description=(
            "调用方提供的可选 Payload 过滤集合；不可空对象，各条件在 Qdrant 中执行，"
            "不会先取大量结果再由 Python 筛选。"
        ),
    )

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    @field_validator("query")
    @classmethod
    def require_non_whitespace_query(cls, value: str) -> str:
        """在任何 Embedding 网络调用前拒绝空白 query。

        Args:
            value: 调用方提交的原始 query。

        Returns:
            保留原始有效空白的 query，避免擅自改变模型输入。

        Raises:
            ValueError: query 只包含空白字符。
        """

        if not value.strip():
            raise ValueError("query 不能只包含空白字符")
        return value

    @field_validator("score_threshold", mode="before")
    @classmethod
    def require_numeric_threshold(cls, value: Any) -> Any:
        """拒绝 bool 和字符串等会被宽松转换成浮点数的 threshold。

        Args:
            value: 调用方传入的可选 Cosine score threshold。

        Returns:
            ``None`` 或真实数值，随后由 Field 检查有限性与 ``[-1, 1]`` 范围。

        Raises:
            ValueError: 值不是非 bool 的实数。
        """

        if value is not None and (
            isinstance(value, bool) or not isinstance(value, Real)
        ):
            raise ValueError("score_threshold 必须是数值型 Cosine 分数")
        return value


class VectorSearchResult(BaseModel):
    """一条按 Qdrant score 排序的命中结果（一个新闻 Chunk）。

    对象生命周期只覆盖搜索响应。注意三个身份的对应：point_id 是 Qdrant 存储层
    主键，chunk_id 是 LangChain 切分层主键，当前 v1 契约里两者是同一个稳定 UUID；
    document_id / source_id 关联 PostgreSQL 业务实体，但本对象本身不是 ORM 记录。
    page_content 来自 Qdrant Payload，它曾经作为 document Embedding 的输入；
    其他 Payload 字段不进入 Embedding。
    """

    point_id: UUID = Field(
        description=(
            "来自 Qdrant ScoredPoint.id 的必需 UUID；不可空，是向量库 Point 主键，"
            "用于稳定定位本次命中的存储对象。"
        ),
    )
    chunk_id: UUID = Field(
        description=(
            "由 Qdrant Point ID 映射的必需稳定 Chunk UUID；不可空，与 point_id 相同，"
            "用于表达它在 LangChain Chunk 层的身份。"
        ),
    )
    score: float = Field(
        allow_inf_nan=False,
        description=(
            "来自 Qdrant ScoredPoint.score 的必需有限数值；当前 Cosine Collection 中"
            "通常越高越相似，用于保持 Qdrant 原始排序，不是概率或百分比。"
        ),
    )
    page_content: str = Field(
        min_length=1,
        description=(
            "来自 Qdrant Point Payload.page_content 的必需非空 Chunk 正文；这是该 "
            "document embedding 的原始文本，用于展示命中内容。"
        ),
    )
    document_id: UUID = Field(
        description=(
            "来自 Qdrant Point Payload.document_id 的必需 UUID；不可空，关联 PostgreSQL "
            "documents.id，供回查完整新闻或后续显式聚合。"
        ),
    )
    content_hash: str = Field(
        pattern=r"^[0-9a-fA-F]{64}$",
        description=(
            "来自 Qdrant Point Payload.content_hash 的必需 64 位 SHA-256 十六进制；"
            "不可空，用于识别命中 Chunk 所属的正文版本。"
        ),
    )
    chunk_index: int = Field(
        ge=0,
        strict=True,
        description=(
            "来自 Qdrant Point Payload.chunk_index 的必需非负整数；不可空，表示 Chunk "
            "在父新闻中的从零开始顺序。"
        ),
    )
    chunk_count: int = Field(
        ge=1,
        strict=True,
        description=(
            "来自 Qdrant Point Payload.chunk_count 的必需正整数；不可空，表示该正文版本"
            "的 Chunk 总数，用于判断相邻关系和展示进度。"
        ),
    )
    title: str = Field(
        min_length=1,
        description=(
            "来自 Qdrant Point Payload.title 的必需非空新闻标题；不可空，只用于结果展示"
            "和回查，不参与当前 query Vector 比较。"
        ),
    )
    url: AnyHttpUrl = Field(
        description=(
            "来自 Qdrant Point Payload.url 的必需 HTTP(S) 原文地址；不可空，用于回到"
            "来源页面，不进入 Embedding。"
        ),
    )
    published_at: datetime | None = Field(
        default=None,
        description=(
            "来自 Qdrant Point Payload.published_at 的可选带时区发布时间；Payload 缺失"
            "时为 None，用于展示和时间过滤，不会用抓取时间伪造。"
        ),
    )
    source_updated_at: datetime | None = Field(
        default=None,
        description=(
            "来自 Qdrant Point Payload.source_updated_at 的可选带时区来源更新时间；"
            "Payload 缺失时为 None，仅供版本审计或展示。"
        ),
    )
    document_type: DocumentType = Field(
        description=(
            "来自 Qdrant Point Payload.document_type 的必需业务枚举；不可空，用于展示"
            "命中文档类型并对应精确过滤值。"
        ),
    )
    source_id: UUID = Field(
        description=(
            "来自 Qdrant Point Payload.source_id 的必需 UUID；不可空，关联 PostgreSQL "
            "sources.id 并对应来源过滤条件。"
        ),
    )
    source_provider: str = Field(
        min_length=1,
        description=(
            "来自 Qdrant Point Payload.source_provider 的必需非空 keyword；不可空，"
            "标识接入提供方并对应精确过滤条件。"
        ),
    )
    source_name: str = Field(
        min_length=1,
        description=(
            "来自 Qdrant Point Payload.source_name 的必需非空展示名称；不可空，用于向"
            "调用方说明新闻来源。"
        ),
    )
    source_external_id: str = Field(
        min_length=1,
        description=(
            "来自 Qdrant Point Payload.source_external_id 的必需非空外部来源标识；"
            "不可空，用于审计接入系统中的来源身份。"
        ),
    )
    document_external_id: str = Field(
        min_length=1,
        description=(
            "来自 Qdrant Point Payload.document_external_id 的必需非空外部文档标识；"
            "不可空，用于审计来源系统中的文章身份。"
        ),
    )
    authors: list[str] = Field(
        description=(
            "来自 Qdrant Point Payload.authors 的必需字符串列表；不可为 null、允许空列表，"
            "用于结果展示，不进入当前 Embedding。"
        ),
    )
    labels: list[str] = Field(
        description=(
            "来自 Qdrant Point Payload.labels 的必需 keyword 字符串列表；不可为 null、"
            "允许空列表，用于展示并支持 MatchAny 标签过滤。"
        ),
    )
    previous_chunk_id: UUID | None = Field(
        default=None,
        description=(
            "来自 Qdrant Point Payload.previous_chunk_id 的可选稳定 Chunk UUID；首个 Chunk "
            "缺失时为 None，用于显式读取相邻上下文，本阶段不会自动扩展。"
        ),
    )
    next_chunk_id: UUID | None = Field(
        default=None,
        description=(
            "来自 Qdrant Point Payload.next_chunk_id 的可选稳定 Chunk UUID；末个 Chunk "
            "缺失时为 None，用于显式读取相邻上下文，本阶段不会自动扩展。"
        ),
    )
    index_schema_version: str = Field(
        min_length=1,
        description=(
            "来自 Qdrant Point Payload.index_schema_version 的必需非空版本，例如 v1；"
            "不可空，用于确认结果属于当前 VectorIndexSpec。"
        ),
    )
    embedding_model: str = Field(
        min_length=1,
        description=(
            "来自 Qdrant Point Payload.embedding_model 的必需非空模型名；不可空，用于"
            "确认命中 Vector 与 query embedding 位于同一模型空间。"
        ),
    )

    model_config = ConfigDict(extra="ignore", frozen=True)

    @field_validator(
        "page_content",
        "title",
        "source_provider",
        "source_name",
        "source_external_id",
        "document_external_id",
        "index_schema_version",
        "embedding_model",
    )
    @classmethod
    def reject_blank_required_strings(cls, value: str) -> str:
        """拒绝类型正确但只包含空白的必需 Payload 字符串。

        Args:
            value: Pydantic 已确认类型为字符串的必需结果字段。

        Returns:
            保留原始有效空白的字符串，避免改变 Qdrant 展示内容。

        Raises:
            ValueError: 字符串不包含任何非空白字符。
        """

        if not value.strip():
            raise ValueError("检索结果的必填字符串不能为空白")
        return value

    @field_validator("authors", "labels", mode="before")
    @classmethod
    def require_json_string_list(cls, value: Any) -> Any:
        """要求列表字段保持 Qdrant JSON array 契约，不接受其他可迭代对象。

        Args:
            value: Qdrant Payload 中原始 ``authors`` 或 ``labels`` 值。

        Returns:
            原始 JSON array，随后由 Pydantic 校验其中每项都是字符串。

        Raises:
            ValueError: 值不是 JSON array 对应的 Python ``list``。
        """

        if not isinstance(value, list):
            raise ValueError("Qdrant Payload 列表字段必须是 JSON 数组")
        return value

    @field_validator("published_at", "source_updated_at")
    @classmethod
    def require_result_timezone(cls, value: datetime | None) -> datetime | None:
        """保证可选 Qdrant 时间值存在时含时区。

        Args:
            value: Pydantic 从 Payload ISO 字符串解析出的可选 datetime。

        Returns:
            原始带时区 datetime，Payload 缺失时返回 ``None``。

        Raises:
            ValueError: 时间存在但没有 UTC offset。
        """

        if value is not None and value.utcoffset() is None:
            raise ValueError("Qdrant Payload 的 datetime 必须包含时区信息")
        return value

    @model_validator(mode="after")
    def validate_chunk_relationship(self) -> "VectorSearchResult":
        """保证 Chunk 顺序与总数及 Point/Chunk 身份一致。

        Returns:
            已确认可按 Chunk 关系导航的当前结果对象。

        Raises:
            ValueError: ``chunk_index`` 越界，或 Point ID 与 Chunk ID 不一致。
        """

        if self.chunk_index >= self.chunk_count:
            raise ValueError("chunk_index 必须小于 chunk_count")
        if self.point_id != self.chunk_id:
            raise ValueError("point_id 与 chunk_id 必须指向同一个 Chunk")
        return self


__all__ = [
    "DEFAULT_TOP_K",
    "MAX_QUERY_CHARACTERS",
    "MAX_TOP_K",
    "VectorSearchFilters",
    "VectorSearchRequest",
    "VectorSearchResult",
]
