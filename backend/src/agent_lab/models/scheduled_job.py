"""周期配置与公共任务执行的持久身份；删除配置不删除已受理工作。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_lab.db.base import Base, TimestampMixin

ACTIVE_SQL = "status IN ('queued', 'waiting_resource', 'running', 'retry_wait', 'needs_attention')"


class ScheduledJobRecord(TimestampMixin, Base):
    """一条周期配置；key 和任务类型不变，版本与下一计划点在同一事务推进。"""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (UniqueConstraint("key", name="uq_scheduled_jobs_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    cron_expr: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False)
    config_version: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # FK 只用于仍存在的配置；历史中的 source_job_id 和快照不随删除改变。
    runs: Mapped[list["JobRunRecord"]] = relationship(back_populates="job", passive_deletes="all")


class JobRunRecord(Base):
    """一次持久受理，共用编号涵盖等待与自动重试；人工重试另建记录。

    config_snapshot 保存受理时的类型、参数和来源配置，policy_snapshot 保存执行策略。
    claim_token 是数据库领取身份，delivery_generation 使旧消息失效；网络发布成功后
    仍按 dispatch_after 补投，避免 Redis 丢消息让执行失去去向。
    """

    __tablename__ = "scheduled_job_runs"
    __table_args__ = (
        UniqueConstraint("source_job_id", "scheduled_for", name="uq_job_runs_schedule_event"),
        Index("uq_job_runs_active_job", "source_job_id", unique=True, postgresql_where=text(ACTIVE_SQL)),
        Index("uq_job_runs_active_business", "concurrency_key", unique=True, postgresql_where=text(ACTIVE_SQL)),
        Index("ix_job_runs_dispatch", "available_at", "dispatch_after", postgresql_where=text("status IN ('queued', 'waiting_resource', 'retry_wait') AND claim_token IS NULL")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("scheduled_jobs.id", ondelete="SET NULL"), index=True)
    source_job_id: Mapped[UUID | None] = mapped_column(Uuid, comment="原周期配置身份，删除配置后仍保留。")
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    task_version: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False, comment="受理主体的稳定身份，不保存邮箱或凭据。")
    concurrency_key: Mapped[str | None] = mapped_column(String(160), comment="业务声明的未结束执行互斥身份，例如文档待办消费者。")
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dispatch_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_error_type: Mapped[str | None] = mapped_column(String(128))
    delivery_generation: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False)
    delivery_count: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    claim_token: Mapped[UUID | None] = mapped_column(Uuid)
    owner: Mapped[str | None] = mapped_column(String(160))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wait_reason: Mapped[str | None] = mapped_column(String(512))
    recovery: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(128))
    config_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", nullable=False)
    policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    retry_of: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("scheduled_job_runs.id", ondelete="RESTRICT"), index=True)

    job: Mapped[ScheduledJobRecord | None] = relationship(back_populates="runs")


class TaskRequestRecord(Base):
    """永久保留的小型请求回执；详情过期也不能把旧请求当成新工作。"""

    __tablename__ = "task_requests"
    actor: Mapped[str] = mapped_column(String(160), primary_key=True)
    operation: Mapped[str] = mapped_column(String(160), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskPolicyRecord(Base):
    """公共默认策略唯一行；修改记录另外保存，不复制周期任务配置。"""

    __tablename__ = "task_policy"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)


class TaskPolicyChangeRecord(Base):
    """超级用户修改默认策略的留痕，包含修改前后的策略值。"""

    __tablename__ = "task_policy_changes"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous: Mapped[dict] = mapped_column(JSONB, nullable=False)
    current: Mapped[dict] = mapped_column(JSONB, nullable=False)


__all__ = ["JobRunRecord", "ScheduledJobRecord", "TaskRequestRecord", "TaskPolicyRecord", "TaskPolicyChangeRecord"]
