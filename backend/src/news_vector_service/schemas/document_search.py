"""定义按新闻文档聚合的只读语义搜索与全文响应契约。

本模块位于 HTTP/应用边界的 Pydantic 层，只描述调用方输入和服务输出；它不生成
Embedding、不访问 Qdrant 或 PostgreSQL，也不负责把多个 Chunk 分组。分组由 Qdrant
搜索组件完成，完整正文由独立的文档详情接口按需读取。
"""

from datetime import datetime
from numbers import Real
from typing import Any
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from news_vector_service.schemas.vector_search import (
    MAX_QUERY_CHARACTERS,
    VectorSearchFilters,
)


DEFAULT_DOCUMENT_LIMIT = 10
MAX_DOCUMENT_LIMIT = 100
DEFAULT_MATCHES_PER_DOCUMENT = 3
MAX_MATCHES_PER_DOCUMENT = 20


class DocumentSearchRequest(BaseModel):
    """一次按新闻文档分组的只读语义搜索请求。

    ``document_limit`` 限制不同新闻的数量，``matches_per_document`` 限制每篇新闻
    返回的高分相关片段数量。query 只进入一次 query Embedding，不写入数据库或 Qdrant。
    """

    query: str = Field(
        max_length=MAX_QUERY_CHARACTERS,
        repr=False,
        description=(
            "用户提交的检索文本，不可为空、纯空白或超过 4096 个 Unicode 字符；原文"
            "可能敏感，仅用于 query Embedding，不保存为 Payload。"
        ),
    )
    document_limit: int = Field(
        default=DEFAULT_DOCUMENT_LIMIT,
        ge=1,
        le=MAX_DOCUMENT_LIMIT,
        strict=True,
        description=(
            "本次最多返回的不同新闻文档数量；默认 10，范围 1..100，由 Qdrant 分组"
            "查询的 limit 使用。"
        ),
    )
    matches_per_document: int = Field(
        default=DEFAULT_MATCHES_PER_DOCUMENT,
        ge=1,
        le=MAX_MATCHES_PER_DOCUMENT,
        strict=True,
        description=(
            "每篇新闻最多返回的高分相关 Chunk 数量；默认 3，范围 1..20，不表示"
            "该新闻的全部物理 Chunk。"
        ),
    )
    score_threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
        description=(
            "可选的原始 Cosine score 下限；必须是 [-1, 1] 的有限数值，不是概率或"
            "百分比。"
        ),
    )
    filters: VectorSearchFilters = Field(
        default_factory=VectorSearchFilters,
        description=(
            "复用 VectorSearchFilters 的来源、类型、标签和发布时间条件；条件在"
            "Qdrant 候选集内执行。"
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
        """在任何 Embedding 网络调用前拒绝空白 query。"""

        if not value.strip():
            raise ValueError("query 不能只包含空白字符")
        return value

    @field_validator("score_threshold", mode="before")
    @classmethod
    def require_numeric_threshold(cls, value: Any) -> Any:
        """拒绝 bool、字符串等会被宽松转换成浮点数的 threshold。"""

        if value is not None and (isinstance(value, bool) or not isinstance(value, Real)):
            raise ValueError("score_threshold 必须是数值型 Cosine 分数")
        return value


class DocumentSearchMatch(BaseModel):
    """文档分组中一个与本次 query 相关的 Chunk 命中。

    正文和序号来自 Qdrant Payload；它只代表本次搜索返回的相关片段，不承诺覆盖
    PostgreSQL 正文中的所有 Chunk。
    """

    chunk_id: UUID = Field(
        description="来自 Qdrant Point ID 的稳定 Chunk UUID，不可空。",
    )
    score: float = Field(
        allow_inf_nan=False,
        description="Qdrant 返回的原始有限 Cosine score，不是概率或百分比。",
    )
    page_content: str = Field(
        min_length=1,
        description="来自 Qdrant Payload 的非空相关 Chunk 纯文本。",
    )
    chunk_index: int = Field(
        ge=0,
        strict=True,
        description="Chunk 在该正文版本中的零起始序号。",
    )
    chunk_count: int = Field(
        ge=1,
        strict=True,
        description="该正文版本的 Chunk 总数，用于展示片段位置。",
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("page_content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        """拒绝只包含空白的 Chunk 正文。"""

        if not value.strip():
            raise ValueError("page_content 必须包含非空白文本")
        return value

    @model_validator(mode="after")
    def validate_chunk_position(self) -> "DocumentSearchMatch":
        """保证 Chunk 序号位于声明的总数范围内。"""

        if self.chunk_index >= self.chunk_count:
            raise ValueError("chunk_index 必须小于 chunk_count")
        return self


class DocumentSearchResult(BaseModel):
    """一篇新闻的聚合搜索结果。

    ``best_match`` 是该文档本次命中中 score 最高的片段；``additional_matches`` 只
    保存本次搜索返回的其他相关片段，不是文章全部物理 Chunk。完整正文通过
    ``GET /documents/{document_id}`` 按需读取。
    """

    document_id: UUID = Field(
        description="关联 PostgreSQL documents.id 的新闻文档 UUID。",
    )
    content_hash: str = Field(
        pattern=r"^[0-9a-fA-F]{64}$",
        description="Qdrant 命中版本的正文 SHA-256，用于和全文接口返回值校验。",
    )
    title: str = Field(
        min_length=1,
        description="来自 Qdrant Payload 的新闻标题。",
    )
    url: AnyHttpUrl = Field(
        description="来自 Qdrant Payload 的 HTTP(S) 原文地址。",
    )
    source_name: str = Field(
        min_length=1,
        description="来自 Qdrant Payload 的来源展示名称。",
    )
    published_at: datetime | None = Field(
        default=None,
        description="来自 Qdrant Payload 的可空带时区发布时间。",
    )
    authors: list[str] = Field(
        description="来自 Qdrant Payload 的作者字符串列表，可为空。",
    )
    labels: list[str] = Field(
        description="来自 Qdrant Payload 的标签字符串列表，可为空。",
    )
    chunk_count: int = Field(
        ge=1,
        strict=True,
        description="该正文版本的物理 Chunk 总数。",
    )
    best_score: float = Field(
        allow_inf_nan=False,
        description="该新闻最高相关 Chunk 的原始 Cosine score，不是概率。",
    )
    best_match: DocumentSearchMatch = Field(
        description="该新闻 score 最高的相关片段。",
    )
    additional_matches: list[DocumentSearchMatch] = Field(
        default_factory=list,
        description=(
            "同一新闻本次搜索返回的其他高分相关片段；有限集合，不命名为 all_chunks，"
            "也不代表文章全部物理 Chunk。"
        ),
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("title", "source_name")
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        """拒绝类型正确但只包含空白的展示字段。"""

        if not value.strip():
            raise ValueError("文档元数据字符串不能为空白")
        return value

    @field_validator("authors", "labels", mode="before")
    @classmethod
    def require_string_lists(cls, value: Any) -> Any:
        """要求列表字段保持 Qdrant JSON array 类型。"""

        if not isinstance(value, list):
            raise ValueError("文档元数据列表字段必须是 JSON 数组")
        return value

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """保证发布时间存在时带有时区。"""

        if value is not None and value.utcoffset() is None:
            raise ValueError("published_at 必须包含时区信息")
        return value

    @model_validator(mode="after")
    def validate_group_contract(self) -> "DocumentSearchResult":
        """保证 best match、片段总数和排序元数据彼此一致。"""

        matches = [self.best_match, *self.additional_matches]
        if self.best_score != self.best_match.score:
            raise ValueError("best_score 必须等于 best_match.score")
        if any(match.chunk_count != self.chunk_count for match in matches):
            raise ValueError("所有匹配结果必须使用同一文档的 chunk_count")
        if len({match.chunk_id for match in matches}) != len(matches):
            raise ValueError("文档检索结果的 chunk_id 必须唯一")
        if any(
            matches[index].score < matches[index + 1].score
            for index in range(len(matches) - 1)
        ):
            raise ValueError("文档检索结果必须按得分降序排列")
        return self


class DocumentDetailResponse(BaseModel):
    """从 PostgreSQL 按需读取的一篇新闻完整纯正文响应。"""

    document_id: UUID = Field(description="PostgreSQL documents.id。")
    content_hash: str = Field(
        pattern=r"^[0-9a-fA-F]{64}$",
        description="当前 PostgreSQL 正文的 SHA-256，用于和搜索索引版本校验。",
    )
    revision: int = Field(
        ge=1,
        strict=True,
        description="当前文档的业务 revision；来自 documents.index_revision。",
    )
    title: str = Field(min_length=1, description="当前 PostgreSQL 新闻标题。")
    url: AnyHttpUrl = Field(description="当前 PostgreSQL 原文地址。")
    source_name: str = Field(min_length=1, description="关联 source 的展示名称。")
    published_at: datetime | None = Field(
        default=None,
        description="当前 PostgreSQL 声明的可空带时区发布时间。",
    )
    authors: list[str] = Field(description="当前 PostgreSQL 作者列表，可为空。")
    labels: list[str] = Field(description="当前 PostgreSQL 标签列表，可为空。")
    content_text: str = Field(
        min_length=1,
        description="PostgreSQL documents.content_text 的完整清洗纯文本。",
    )

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("published_at")
    @classmethod
    def require_detail_timezone(cls, value: datetime | None) -> datetime | None:
        """保证详情发布时间存在时带有时区。"""

        if value is not None and value.utcoffset() is None:
            raise ValueError("published_at 必须包含时区信息")
        return value

    @field_validator("title", "source_name", "content_text")
    @classmethod
    def require_detail_text(cls, value: str) -> str:
        """拒绝详情契约中的空白文本。"""

        if not value.strip():
            raise ValueError("文档详情文本必须包含非空白字符")
        return value


__all__ = [
    "DEFAULT_DOCUMENT_LIMIT",
    "DEFAULT_MATCHES_PER_DOCUMENT",
    "MAX_DOCUMENT_LIMIT",
    "MAX_MATCHES_PER_DOCUMENT",
    "DocumentDetailResponse",
    "DocumentSearchMatch",
    "DocumentSearchRequest",
    "DocumentSearchResult",
]
