"""定义定时任务及其执行历史的 PostgreSQL ORM 实体。

本模块位于持久层模型边界，只声明 ``scheduled_jobs`` 与 ``scheduled_job_runs`` 两张表的
列、唯一键和 ORM relationship；它不解析 cron、不启动调度器、不执行 FreshRSS/Qdrant I/O。

两张表的关系：``scheduled_jobs`` 一行是一条「定时任务」（任务类型、cron、参数、启停），
是调度器配置的唯一事实来源（见 docs/adr/0014-in-process-apscheduler-with-db-as-source-of-truth.md）；
``scheduled_job_runs`` 一行是一次「任务执行」（到点触发或手动触发各一条），只记脱敏统计，
不记异常文本。任务删除时执行历史随外键级联删除。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_lab.db.base import Base, TimestampMixin


class ScheduledJobRecord(TimestampMixin, Base):
    """scheduled_jobs 表：一条「定时任务」配置。

    「业务粒度」= 一行代表一个可独立启停、配置和观察的周期任务。``key`` 是人写的
    业务唯一键（短横线小写 slug），既是唯一约束也是调度器内部的 job id，所以创建后
    不允许修改；``task_type`` 决定到点后执行哪段代码，取值清单由代码注册表定义
    （见 ``scheduled_task_registry``），不是数据库数据。``cron_expr`` 存 5 段 cron
    字符串原样；解释时区是进程级配置（SCHEDULER_TIMEZONE），不按任务存。

    ``params`` 是该任务类型的执行参数（JSON 对象）；合法形状由注册表里的 pydantic
    模型在写入前校验，数据库层不做结构约束——参数形状是代码契约，不是数据契约。
    """

    __tablename__ = "scheduled_jobs"

    __table_args__ = (
        UniqueConstraint(
            "key",
            name="uq_scheduled_jobs_key",
        ),
        {"comment": "定时任务配置：调度器加载与任务执行历史的事实来源。"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        comment="Python 服务生成的定时任务主键。",
    )
    key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="业务唯一键（短横线小写），创建后不可改；同时是调度器 job id。",
    )
    task_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="任务类型标识，取值由代码注册表定义，例如 freshrss_sync、index_pending。",
    )
    cron_expr: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="5 段 cron 表达式原样字符串；解释时区由进程级 SCHEDULER_TIMEZONE 决定。",
    )
    params: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="任务类型的执行参数（JSON 对象），形状由注册表的 pydantic 模型校验。",
    )
    enabled: Mapped[bool] = mapped_column(
        nullable=False,
        comment="是否参与 cron 调度；停用的任务保留配置但不到点执行。",
    )

    # 一对多 ORM 导航属性，不是 scheduled_jobs 表里的数组列。实际外键在
    # scheduled_job_runs.job_id，删除任务时执行历史由数据库级联删除。
    runs: Mapped[list[JobRunRecord]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class JobRunRecord(Base):
    """scheduled_job_runs 表：定时任务的一次执行记录。

    「业务粒度」= 一行代表一次到点触发或手动触发的执行尝试。生命周期由调度器包装器
    维护：开始执行前插入 ``running`` 行，结束后更新成败与统计；上一轮还没跑完时到点的
    触发只插一条 ``skipped`` 行（只有 started_at，没有 finished_at），不排队。

    ``stats`` 与手动流水线响应同口径脱敏：只有数量和按异常类型聚合计数；批次级失败时
    会带 ``error_reason``（稳定的失败原因枚举，如 ``login_rejected``，无正文无凭据），
    skipped 记录只有 reason。历史不无界增长：每次执行收尾会把超出保留条数的旧记录裁掉。
    """

    __tablename__ = "scheduled_job_runs"

    __table_args__ = (
        {"comment": "定时任务执行历史：一次触发一条，只记脱敏统计。"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        comment="Python 服务生成的任务执行记录主键。",
    )
    job_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("scheduled_jobs.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属定时任务 id；任务删除时执行记录级联删除。",
        index=True,
    )
    trigger_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="触发方式：scheduled（cron 到点）或 manual（管理端手动触发）。",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="执行状态：running、succeeded、failed 或 skipped。",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="执行开始（或被跳过判定发生）的 UTC 时刻。",
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="执行结束的 UTC 时刻；running 与 skipped 状态下为空。",
    )
    stats: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="脱敏执行统计（数量与按异常类型的聚合计数，失败记录含 error_reason 枚举），结构与手动流水线同口径。",
    )
    error_type: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="批次级失败的异常类名；只存类型名，不存异常文本。成功与跳过时为空。",
    )

    # 多对一 ORM 导航属性；真实外键在本表 job_id 一侧。
    job: Mapped[ScheduledJobRecord] = relationship(
        back_populates="runs",
    )


__all__ = ["JobRunRecord", "ScheduledJobRecord"]
