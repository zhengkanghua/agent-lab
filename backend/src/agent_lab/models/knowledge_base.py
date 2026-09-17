"""KnowledgeBase 配置表；稳定业务键与可修改展示名称分别存储。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, String, Text, UniqueConstraint, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_lab.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from agent_lab.models.source import SourceRecord


class KnowledgeBaseRecord(TimestampMixin, Base):
    """一行代表一个逻辑知识库，停用保留配置，不提供物理删除入口。"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("key", name="uq_knowledge_bases_key"),
        CheckConstraint(
            "key ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'",
            name="ck_knowledge_bases_key_format",
        ),
        CheckConstraint("length(trim(name)) > 0", name="ck_knowledge_bases_name_not_blank"),
        {"comment": "逻辑知识库配置，不代表独立数据库或向量 Collection。"},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(64), nullable=False, comment="不可修改的稳定业务键。")
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="知识库展示名称。")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="知识库说明。")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(), comment="是否启用。"
    )
    visibility_revision: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False,
        comment="索引可见性修订；候选写入意图、采用、停止使用与回收时推进。")
    # 库上没有外键约束，join 条件不能靠 ForeignKey 推断，必须与 SourceRecord.knowledge_base
    # 那一侧的显式声明保持一致。
    sources: Mapped[list[SourceRecord]] = relationship(
        back_populates="knowledge_base",
        primaryjoin="KnowledgeBaseRecord.id == SourceRecord.knowledge_base_id",
        foreign_keys="SourceRecord.knowledge_base_id",
    )
