"""写操作的持久占用与跨库删除待办；不依赖可裁剪的任务执行历史。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent_lab.db.base import Base


class WriteOperationRecord(Base):
    """一次同步、索引或清理的资源占用，失联后保留供人工确认。"""

    __tablename__ = "write_operations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    resources: Mapped[list] = mapped_column(JSONB, nullable=False)
    owner: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)


class DocumentDeletionRecord(Base):
    """已经确认的删除意图；Document 消失后仍可保留，不设置级联外键。"""

    __tablename__ = "document_deletions"

    document_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    revision: Mapped[int] = mapped_column(nullable=False)
    cutoff_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="保留期清理的截止时刻；为空表示用户按明确 ID 删除上传文档。",
    )
    retention_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    qdrant_deleted: Mapped[bool] = mapped_column(default=False, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
