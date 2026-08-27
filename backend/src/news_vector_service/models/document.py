"""定义持久层统一新闻文档及其 Qdrant 索引状态。

本模块位于 SQLAlchemy models 层，负责 ``documents`` 表的业务字段、主外键、约束、
查询索引和派生向量副本状态；不负责抓取 FreshRSS、不构建 LangChain Document、不
生成 Embedding，也不保存 Chunk 或 Vector。Qdrant Point 可由这里的正文、来源关系和
``VectorIndexSpec`` 重建，PostgreSQL 仍是新闻业务事实来源。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from news_vector_service.db.base import Base, TimestampMixin
from news_vector_service.domain.enums import DocumentType, ProcessingStatus

if TYPE_CHECKING:
    from news_vector_service.models.source import SourceRecord


def enum_values(enum_class: type[DocumentType] | type[ProcessingStatus]) -> list[str]:
    """让 SQLAlchemy 在数据库中保存 StrEnum 的 value，而不是成员名称。

    Args:
        enum_class: 文档类型或处理状态的 ``StrEnum`` 类。

    Returns:
        按枚举声明顺序排列的字符串 value，供 SQLAlchemy ``Enum`` 使用。
    """

    return [member.value for member in enum_class]


class DocumentRecord(TimestampMixin, Base):
    """documents 表：持久化「清洗正文 + 来源关联 + 当前处理状态 + 索引快照」。

    「业务粒度」= 一行代表「某个来源下面的一篇逻辑文档」。``id`` 是主键；
    ``source_id + external_id`` 是业务唯一键（幂等去重据此判断）；``source_id``
    外键指向 sources.id。``processing_status`` 索引供 Worker 扫描待处理任务用，
    ``published_at`` 索引供按时间的新闻范围查询用。``source`` relationship 只是
    ORM 导航属性，不是额外数据库列。

    一个关键设计：「派生副本」与「业务事实」分开。``index_revision`` 表示当前业务
    字段「应该写入」的 Qdrant 版本；indexed 系列字段记录「最近一次完整成功」写入
    的版本快照。表本身不存 LangChain Chunk 或 Embedding——它们可以由 content_text、
    来源字段和索引规格重建，真正的向量/Chunk Payload 存在 Qdrant。
    """

    __tablename__ = "documents"

    # Python Enum 负责应用层合法值，CheckConstraint 在绕过 ORM 写数据库时继续
    # 保护数据；两个索引分别服务待处理任务扫描和按时间范围查询。
    __table_args__ = (
        CheckConstraint(
            "document_type IN ('article', 'press_release', 'economic_release', "
            "'filing', 'research_report', 'policy_document')",
            name="ck_documents_document_type",
        ),
        CheckConstraint(
            "processing_status IN ('pending', 'processing', 'indexed', 'failed')",
            name="ck_documents_processing_status",
        ),
        CheckConstraint(
            "index_revision >= 1",
            name="ck_documents_index_revision_positive",
        ),
        CheckConstraint(
            "indexed_revision IS NULL OR indexed_revision >= 1",
            name="ck_documents_indexed_revision_positive",
        ),
        UniqueConstraint(
            "source_id",
            "external_id",
            name="uq_documents_source_external_id",
        ),
        Index("ix_documents_processing_status", "processing_status"),
        Index("ix_documents_published_at", "published_at"),
        {"comment": "Python 服务管理的统一文档及其当前处理状态。"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        comment="Python 服务生成的文档主键。",
    )
    source_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
        comment="关联 sources.id。",
    )
    external_id: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="文档在来源中的唯一标识，例如 FreshRSS article id。",
    )
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(
            DocumentType,
            name="document_type_values",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
        default=DocumentType.ARTICLE,
        server_default=DocumentType.ARTICLE.value,
        comment="文档类型，以受代码约束的字符串保存。",
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="文档标题。")
    url: Mapped[str] = mapped_column(Text, nullable=False, comment="原始文档地址。")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="来源声明的发布时间。",
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="数据提供方声明的文档更新时间。",
    )
    authors: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
        comment="规范化作者列表。",
    )
    labels: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
        comment="业务标签列表，不包含 FreshRSS 已读等状态。",
    )
    image_urls: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
        comment="正文图片 URL 列表，不保存图片二进制。",
    )
    content_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="清洗后的完整正文纯文本。",
    )
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="规范化正文的 SHA-256，用于检测内容变化。",
    )
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(
            ProcessingStatus,
            name="processing_status_values",
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
        ),
        nullable=False,
        default=ProcessingStatus.PENDING,
        server_default=ProcessingStatus.PENDING.value,
        comment="当前处理状态，以受代码约束的字符串保存。",
    )
    index_revision: Mapped[int] = mapped_column(
        default=1,
        server_default=text("1"),
        nullable=False,
        comment="需要写入 Qdrant 的文档版本号；任何可索引字段变化都会递增。",
    )
    indexed_revision: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Qdrant 最近一次完整成功写入对应的文档版本号。",
    )
    indexed_content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Qdrant 最近一次完整成功写入对应的规范正文 SHA-256。",
    )
    indexed_schema_version: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="Qdrant 最近一次成功写入使用的向量索引 Schema 版本。",
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="当前一次向量索引 Worker 开始处理的时间。",
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="当前文档版本最近一次完整写入 Qdrant 的时间。",
    )
    last_processing_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="最近一次索引失败的脱敏、限长错误说明，不保存密钥或完整正文。",
    )

    # relationship 只描述 ORM 对象导航，不会新增数据库列；真正的数据库关联由
    # source_id 外键承担。Pipeline 使用该属性前必须 eager-load，避免异步懒加载。
    source: Mapped[SourceRecord] = relationship(back_populates="documents")
