"""SQLAlchemy 声明式模型的公共基类与时间字段。"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的基类；Alembic 从它读取完整表结构。"""


class TimestampMixin:
    """为业务表提供由 PostgreSQL 记录的创建和更新时间。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="记录首次写入 PostgreSQL 的时间。",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="记录最后一次通过 ORM 更新的时间。",
    )
