"""声明账号级个人偏好的 PostgreSQL 实体。

本模块位于 SQLAlchemy 持久层，只保存「某个账号的自身操作默认值」（两个检索数量参数）；不保存
系统提示词（它只有内置那一份，不再存任何地方），也不保存全局业务配置（那张表按全站一份）。

``user_id`` 是**逻辑外键**：列、类型、索引在，库上没有 ``FOREIGN KEY`` 约束。注销账号
**不删这一行**：个人偏好是这个人在系统里留下的记录，不是账号的附属数据，账号注销后它原样保留
（根因与代价见 ``docs/adr/0034-account-deletion-is-soft-delete.md``）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, text
from sqlalchemy.orm import Mapped, mapped_column

from agent_lab.db.base import Base, TimestampMixin


class UserPreferenceRecord(TimestampMixin, Base):
    """user_preferences 表：一行一个账号的个人偏好。

    ``user_id`` 既是主键也是账号标识——一个账号最多一行，不需要额外的唯一约束。这一形状
    让「读某个账号的偏好」永远是一次主键查询，也让「未配置」就只有一种表现：没有行。

    **为什么是一列一字段而不是 key-value 窄表。** 偏好的字段集合由业务决定、对所有账号
    相同，新增一项是一次业务决策，本来就该走迁移；key-value 会把「未配置」表达成「行不
    存在」，既隐式又把行数按配置项数放大。
    """

    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint("document_limit >= 1", name="ck_user_preferences_document_limit_positive"),
        CheckConstraint(
            "matches_per_document >= 1",
            name="ck_user_preferences_matches_positive",
        ),
        {"comment": "账号级个人偏好；一行一个账号，注销账号时这一行保留。"},
    )

    user_id: Mapped[UUID] = mapped_column(
        primary_key=True,
        comment="该偏好所属的 users.id；业务层维护的逻辑外键，库上无约束。注销账号时这一行保留。",
    )
    document_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("10"),
        comment="检索默认返回的文档数；写入前由应用层按契约边界归一化。",
    )
    matches_per_document: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("3"),
        comment="每篇文档默认保留的片段数；写入前由应用层按契约边界归一化。",
    )


__all__ = ["UserPreferenceRecord"]
