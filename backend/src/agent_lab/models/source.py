"""定义新闻来源及其增量同步 checkpoint 的 PostgreSQL ORM 实体。

本模块位于持久层模型边界，只声明 ``sources`` 表的列、业务唯一键和 ORM relationship；
它不读取 FreshRSS、不推进 checkpoint、不提交事务，也不构建 Document、Chunk 或 Vector。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_lab.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from agent_lab.models.document import DocumentRecord


class SourceRecord(TimestampMixin, Base):
    """sources 表：一个「可动态增加的外部来源」，例如 FreshRSS 里的一条订阅。

    「业务粒度」= 一行代表「某个接入系统（provider）里的一个具体来源（external_id）」
    的组合。``id`` 是 Python 生成的主键；``provider`` 标识接入系统（freshrss_main、
    bls、sec_edgar），``external_id`` 标识该系统内部的来源（feed/2）。两者共同构成
    业务唯一键——因为不同系统可能恰好用相同的 external ID。

    展示名称和 URL 可以变，不参与幂等判断（变更不会新增行）。``sync_checkpoint``
    是增量同步的来源级游标：记录「这个来源上次同步到哪一页」，只用于可靠增量同步，
    不是新闻事实，也不进 Document/Chunk/Embedding。当前不需要给 checkpoint 建索引，
    因为同步总是先按唯一业务键定位来源。

    一个容易混淆的点：``documents`` relationship 是 ORM 对象导航「属性」，不是
    sources 表里的数组列——真实外键在 documents.source_id 那一侧。
    """

    __tablename__ = "sources"

    # 数据库唯一约束是并发导入时的最终防线；仅在应用代码中“先查再写”仍可能
    # 因两个事务同时执行而插入重复来源。
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_id",
            name="uq_sources_provider_external_id",
        ),
        {"comment": "外部新闻、政策、财报等文档来源。"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        comment="Python 服务生成的来源主键。",
    )
    provider: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="数据提供方稳定标识，例如 freshrss_main、bls、sec_edgar。",
    )
    external_id: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="来源在提供方中的标识，例如 FreshRSS 的 feed/2。",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="来源展示名称。",
    )
    feed_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="RSS/Atom 地址；非 Feed 来源可以为空。",
    )
    home_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="来源主页地址。",
    )
    sync_checkpoint: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment=(
            "该来源最近一次成功持久化 FreshRSS 分页的 continuation 游标；"
            "仅用于可靠增量同步，不进入文档索引或向量。"
        ),
    )
    sync_checkpoint_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="该来源增量同步游标最近一次成功推进的时间；未同步时为空。",
    )

    # 一对多 ORM 导航属性，不是 sources 表中的数组字段。实际外键保存在
    # documents.source_id，删除/更新策略由该外键和业务 Service 共同控制。
    documents: Mapped[list[DocumentRecord]] = relationship(
        back_populates="source",
    )
