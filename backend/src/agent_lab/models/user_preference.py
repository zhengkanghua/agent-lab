"""声明账号级个人偏好的 PostgreSQL 实体。

本模块位于 SQLAlchemy 持久层，只保存「某个账号的自身操作默认值」；不保存会话级设定
（那是 ``agent_threads.system_prompt``），也不保存全局业务配置（那张表按全站一份）。

``user_id`` 是**逻辑外键**：列、类型、索引在，库上没有 ``FOREIGN KEY`` 约束，删账号时由
``UserAdminService.delete_user`` 在同一事务里显式删除这一行（见 ADR 0028）。配置尤其不能
指望运维命令兜底——会话有 ``prune-orphan-threads``，配置没有对应的清理命令。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from agent_lab.db.base import Base, TimestampMixin


class UserPreferenceRecord(TimestampMixin, Base):
    """user_preferences 表：一行一个账号的个人偏好。

    ``user_id`` 既是主键也是账号标识——一个账号最多一行，不需要额外的唯一约束。这一形状
    让「读某个账号的偏好」永远是一次主键查询，也让「未配置」在写入前表现为**没有行**、
    在读出来之后表现为 ``NULL``。

    **为什么是一列一字段而不是 key-value 窄表。** 偏好的字段集合由业务决定、对所有账号
    相同，新增一项是一次业务决策，本来就该走迁移；key-value 会把「未配置」表达成「行不
    存在」，既隐式又把行数按配置项数放大。

    ``system_prompt`` 为 ``NULL`` 表示该账号没配过、使用服务端内置默认提示词。它在**新建
    会话**时被读出来写进 ``agent_threads.system_prompt`` 作为会话级快照，之后的续聊不再
    回读本表——这样用户在设置页改提示词只影响新开的会话。
    """

    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint("document_limit >= 1", name="ck_user_preferences_document_limit_positive"),
        CheckConstraint(
            "matches_per_document >= 1",
            name="ck_user_preferences_matches_positive",
        ),
        {"comment": "账号级个人偏好；一行一个账号，删账号时由业务层清理。"},
    )

    user_id: Mapped[UUID] = mapped_column(
        primary_key=True,
        comment="该偏好所属的 users.id；业务层维护的逻辑外键，库上无约束。删账号时由业务层清理。",
    )
    system_prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="自定义系统提示词；为空表示使用服务端内置默认提示词。",
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
