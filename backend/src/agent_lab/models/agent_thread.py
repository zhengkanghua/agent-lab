"""声明 Agent 会话归属的 PostgreSQL 实体。

本模块位于 SQLAlchemy 持久层，只保存「某个会话属于哪个账号」以及列表页要显示的标题和时间；
**不保存任何消息内容**。历史消息归 LangGraph checkpointer 自己的四张表（见 ADR 0004），
本表与那四张表之间没有外键——它们不由本项目的 ORM 定义，加外键等于把结构依赖写进 schema。

两处刻意的取舍，改动前先读 docs/adr/0009-agent-thread-ownership-in-own-table.md：

1. 不存消息副本，也不存「最后一条回答的摘要」。那些内容在 checkpointer 里已经有一份，
   存第二份就是双真源；而且 ``SummarizationMiddleware`` 会压缩历史，副本压不到，
   于是界面显示的和模型实际看到的对不上。
2. 不存轮数。维护计数列要求每轮都写、且失败的运行也得算对，数字很容易飘；去 checkpointer
   表里数则违背上面那条解耦。列表页不显示轮数就不需要这个数。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, desc
from sqlalchemy.orm import Mapped, mapped_column

from agent_lab.db.base import Base


class AgentThreadRecord(Base):
    """agent_threads 表：一个 Agent 会话的归属与列表元信息。

    一行对应一个会话（``CONTEXT.md`` 的「会话（thread）」）。``thread_id`` 与 checkpointer
    用的那个 id 同值，所以本表既是归属真源，也是「这个 id 是谁开的」唯一可查处。

    刻意不继承 ``TimestampMixin``：它给的是 ``created_at`` 加 ``updated_at``，而这里需要的第二个
    时间是「最后一次有人在这个会话里提问」，语义不是「ORM 最后一次更新」。混用会让排序键
    在将来某次无关的字段更新后被悄悄改写。
    """

    __tablename__ = "agent_threads"
    __table_args__ = (
        # 列表查询固定是「我的 + 按最近活跃倒序」，所以索引是这两列的复合、且第二列降序。
        # 只给 user_id 建单列索引的话，PostgreSQL 得把该用户的全部会话取出来排完再分页。
        Index(
            "ix_agent_threads_user_id_last_active_at",
            "user_id",
            desc("last_active_at"),
        ),
        {"comment": "Agent 会话的账号归属与列表元信息；不含消息内容。"},
    )

    thread_id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        comment="会话 id，与 LangGraph checkpointer 的 thread_id 同值；由服务端生成。",
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="该会话所属的 users.id；删除账号时级联删除归属记录。",
    )
    title: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        comment="会话标题，取首条提问截断而来；创建后不再改写。",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="会话创建时间，即第一次提问被受理的时刻。",
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="最后一次在本会话提问的时刻；会话列表的排序键。",
    )


__all__ = ["AgentThreadRecord"]
