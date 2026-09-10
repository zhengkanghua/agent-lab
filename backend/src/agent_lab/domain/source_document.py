"""外部数据进入业务流程后的统一文档模型。

``SourceDocument`` 是 Pydantic 内存对象，不是 PostgreSQL 表模型，也不是
LangChain 的 ``Document``。它负责隔离 FreshRSS、BLS、SEC 等外部格式差异。
"""

from datetime import datetime
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from agent_lab.domain.enums import DocumentType


class SourceInfo(BaseModel):
    """描述文档来自哪个数据提供方及其下属来源。"""

    provider: str = Field(
        min_length=1,
        description="数据提供方稳定标识，例如 freshrss_main、bls、sec_edgar。",
    )
    external_id: str = Field(
        min_length=1,
        description="来源在提供方中的标识，例如 FreshRSS 的 feed/2。",
    )
    name: str = Field(min_length=1, description="用于展示和过滤的来源名称。")
    feed_url: AnyHttpUrl | None = Field(default=None, description="RSS/Atom 地址。")
    home_url: AnyHttpUrl | None = Field(default=None, description="来源主页地址。")

    model_config = ConfigDict(frozen=True)


class ImageReference(BaseModel):
    """正文中的图片引用；当前只保存引用信息，不保存图片二进制。"""

    url: AnyHttpUrl = Field(description="原站图片地址。")
    alt_text: str | None = Field(default=None, description="图片替代文本。")
    title: str | None = Field(default=None, description="图片标题。")

    model_config = ConfigDict(frozen=True)


class SourceDocument(BaseModel):
    """来源条目的身份、元数据与原始字节；正文解析在可靠保存之后进行。"""

    external_id: str = Field(
        min_length=1,
        description="文档在数据提供方中的唯一标识。",
    )
    knowledge_base_id: UUID | None = Field(
        default=None,
        description="导入目标 KnowledgeBase；来源发现阶段尚未绑定时为空。",
    )
    document_type: DocumentType = Field(
        default=DocumentType.ARTICLE,
        description="文档业务类型，用于后续过滤和选择处理规则。",
    )
    mime_type: str = Field(
        default="text/html", min_length=1,
        description="原始字节的格式；FreshRSS 选定正文为 text/html。",
    )
    title: str = Field(description="来源标题；空标题由保存后的质量检查转人工处理。")
    url: AnyHttpUrl = Field(description="文档原始页面地址。")
    published_at: datetime | None = Field(
        default=None,
        description="来源声明的发布时间，存在时必须带时区。",
    )
    source_updated_at: datetime | None = Field(
        default=None,
        description="数据提供方声明的文档更新时间，存在时必须带时区。",
    )
    source: SourceInfo = Field(description="文档来源信息。")
    authors: tuple[str, ...] = Field(default=(), description="规范化后的作者列表。")
    labels: tuple[str, ...] = Field(default=(), description="业务标签，不含已读等状态标签。")
    raw_bytes: bytes = Field(repr=False, description="实际收到并选作正文的原始字节。")
    images: tuple[ImageReference, ...] = Field(
        default=(),
        description="正文中提取的图片引用。",
    )

    model_config = ConfigDict(frozen=True)

    @field_validator("published_at", "source_updated_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        """拒绝没有时区的时间，避免在入库时错误解释来源时间。

        Args:
            value: Pydantic 已解析的发布时间或来源更新时间。

        Returns:
            原始的带时区时间；输入为 ``None`` 时仍返回 ``None``。

        Raises:
            ValueError: 时间存在但没有时区信息时抛出。
        """

        if value is not None and value.utcoffset() is None:
            raise ValueError("datetime 值必须包含时区信息")
        return value
