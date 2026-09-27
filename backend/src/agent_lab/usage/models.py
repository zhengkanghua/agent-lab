"""用量库的表结构：一张只追加的事实表。

**它有自己的元数据（``UsageBase``），不能挂到业务库的 ``db.base.Base`` 上。** 两边各有一套
迁移链：用量表的模型一旦出现在业务侧 metadata 里，业务库的 ``alembic check`` 会把它当成
「库里少了一张表」而在业务库里建出来，或者反过来把它当成多余的表删掉——两种都会破坏分库的
前提（见 ``docs/adr/0032-usage-data-in-separate-database.md``）。

表只追加，不修改、不删除：没有 ``created_at`` / ``updated_at``，写入的就是事实本身，
唯一的时间是这次调用的发生时刻。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UsageBase(DeclarativeBase):
    """用量库所有模型的基类；用量侧的 Alembic 环境从它读取表结构。"""


class UsageRecordRow(UsageBase):
    """一条用量记录；一次模型调用一行。

    列与 ``usage.contracts.UsageRecord`` 一一对应，只多一个自增主键。账号、会话、运行三列
    都是**不透明引用**，库上没有外键约束（跨库也不可能有）——它们指向的账号或会话被删除时，
    账本不跟着删，理由见 ``CONTEXT.md`` 的「用量记录」词条。

    Attributes:
        id: 自增主键。分页排序用它做「同一毫秒」的稳定次序（发生时刻倒序、主键倒序），
            因为同一账号的多个会话并发写入时会落在同一毫秒。
        call_id: 这条记录自己的标识，跨进程唯一；唯一约束挡的是「同一条记录被重复写入」。
    """

    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("call_id", name="uq_usage_records_call_id"),
        # 两条索引正好覆盖现有的三种查询：不带模型筛选的明细与汇总、带模型筛选的明细与汇总，
        # 以及模型名列表要的「本账号下有哪些模型」。
        Index("ix_usage_records_user_occurred", "user_id", "occurred_at"),
        Index("ix_usage_records_user_model_occurred", "user_id", "model_name", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="自增主键；只用于分页时的稳定次序。",
    )
    call_id: Mapped[UUID] = mapped_column(
        Uuid,
        nullable=False,
        comment="这条用量记录自己的标识，跨进程唯一；重复写入被唯一约束挡下。",
    )
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        comment="归属账号的逻辑引用；库上无外键约束，账号被删除时本行不删。NULL 表示归属未知。",
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        comment="所属 Agent 会话的逻辑引用；库上无外键约束，会话被删除时本行不删。NULL 表示归属未知。",
    )
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        comment="所属运行（一次提问到最终回答）的逻辑引用；本期没有按它筛选的入口，因此不建索引。",
    )
    model_name: Mapped[str | None] = mapped_column(
        String(128),
        comment="这次调用实际使用的模型名；主模型与备用模型靠它分辨。NULL 表示取不到。",
    )
    input_tokens: Mapped[int] = mapped_column(
        nullable=False,
        comment="上游报的输入 token；已包含缓存命中的部分，因此不能与输出、缓存相加当消耗。",
    )
    output_tokens: Mapped[int] = mapped_column(
        nullable=False,
        comment="上游报的输出 token。",
    )
    cached_tokens: Mapped[int | None] = mapped_column(
        comment="上游报的缓存命中 token；NULL 表示上游没报这一项，与「报了 0」是两件事。",
    )
    total_tokens: Mapped[int] = mapped_column(
        nullable=False,
        comment="上游报的合计 token；上游没报时由输入加输出补齐。",
    )
    duration_ms: Mapped[int] = mapped_column(
        nullable=False,
        comment="从调用进入包装层到结束之间的单调时钟差值毫秒数。",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="结束方式：completed 为正常跑完，failed 为上游报错、超时或被取消。",
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="这批数字的来源：upstream 为上游自报（哪怕总量是 0），missing 为上游没报。",
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="调用进入包装层的 UTC 时刻；列带时区，会话时区也钉成 UTC。",
    )


__all__ = ["UsageBase", "UsageRecordRow"]
