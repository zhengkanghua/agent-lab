"""增加定时任务表 add scheduled jobs。

Revision ID: e8b4c2d6a9f1
Revises: c3f8a1b6e492
Create Date: 2026-09-02 10:00:00.000000

该 migration 创建 ``scheduled_jobs``（定时任务配置）与 ``scheduled_job_runs``（任务执行
历史）两张表，并插入两条启用的种子任务（freshrss-sync 每 10 分钟、index-pending 每
5 分钟），参数与手动流水线接口的默认值一致。调度器的设计与取舍见
docs/adr/0014-in-process-apscheduler-with-db-as-source-of-truth.md。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "e8b4c2d6a9f1"
down_revision: str | Sequence[str] | None = "c3f8a1b6e492"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 种子任务用固定 UUID：迁移「写下来就不再变」，随机生成会让每次重建库得到不同主键，
# 排查问题时无法对照。key 有唯一约束 + ON CONFLICT DO NOTHING，重复执行（例如从旧库
# 补跑迁移）不会插入第二行。
SEED_FRESHRSS_SYNC_ID = "6b2f1c8e-3a4d-4e5f-9a0b-1c2d3e4f5a6b"
SEED_INDEX_PENDING_ID = "7c3e2d9f-4b5a-4f60-ab1c-2d3e4f5a6b7c"


def upgrade() -> None:
    """创建定时任务与执行历史两张表，并插入两条启用的种子任务。"""

    op.create_table(
        "scheduled_jobs",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="Python 服务生成的定时任务主键。",
        ),
        sa.Column(
            "key",
            sa.String(length=64),
            nullable=False,
            comment="业务唯一键（短横线小写），创建后不可改；同时是调度器 job id。",
        ),
        sa.Column(
            "task_type",
            sa.String(length=64),
            nullable=False,
            comment="任务类型标识，取值由代码注册表定义，例如 freshrss_sync、index_pending。",
        ),
        sa.Column(
            "cron_expr",
            sa.String(length=64),
            nullable=False,
            comment="5 段 cron 表达式原样字符串；解释时区由进程级 SCHEDULER_TIMEZONE 决定。",
        ),
        sa.Column(
            "params",
            JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="任务类型的执行参数（JSON 对象），形状由注册表的 pydantic 模型校验。",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            comment="是否参与 cron 调度；停用的任务保留配置但不到点执行。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="记录首次写入 PostgreSQL 的时间。",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="记录最后一次通过 ORM 更新的时间。",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_jobs")),
        sa.UniqueConstraint("key", name="uq_scheduled_jobs_key"),
        comment="定时任务配置：调度器加载与任务执行历史的事实来源。",
    )
    op.create_table(
        "scheduled_job_runs",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="Python 服务生成的任务执行记录主键。",
        ),
        sa.Column(
            "job_id",
            sa.Uuid(),
            nullable=False,
            comment="所属定时任务 id；任务删除时执行记录级联删除。",
        ),
        sa.Column(
            "trigger_type",
            sa.String(length=16),
            nullable=False,
            comment="触发方式：scheduled（cron 到点）或 manual（管理端手动触发）。",
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            comment="执行状态：running、succeeded、failed 或 skipped。",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="执行开始（或被跳过判定发生）的 UTC 时刻。",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="执行结束的 UTC 时刻；running 与 skipped 状态下为空。",
        ),
        sa.Column(
            "stats",
            JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="脱敏执行统计（数量与按异常类型的聚合计数），结构与手动流水线同口径。",
        ),
        sa.Column(
            "error_type",
            sa.String(length=128),
            nullable=True,
            comment="批次级失败的异常类名；只存类型名，不存异常文本。成功与跳过时为空。",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["scheduled_jobs.id"],
            name=op.f("fk_scheduled_job_runs_job_id_scheduled_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_job_runs")),
        comment="定时任务执行历史：一次触发一条，只记脱敏统计。",
    )
    op.create_index(
        op.f("ix_scheduled_job_runs_job_id"),
        "scheduled_job_runs",
        ["job_id"],
        unique=False,
    )

    # 种子任务的 cron 与参数默认值老板已确认；
    # enabled=true：部署完成即开始自动同步与索引，不想自动跑可在管理端停用或关闭
    # SCHEDULER_ENABLED 总开关。
    #
    # 参数必须显式 CAST 成 uuid/jsonb：bindparams 从 Python 字符串值推断出 String 类型时，
    # psycopg 会把占位符渲染成 %(sync_id)s::VARCHAR，直接插 uuid 列会报 DatatypeMismatch
    # （真实部署抓到过一次；离线 --sql 渲染会把参数内联成字面量，查不出这类问题）。
    # 显式 CAST(varchar AS uuid/jsonb) 与参数类型无关，怎么渲染都成立。
    seed_sql = sa.text(
        """
        INSERT INTO scheduled_jobs (id, key, task_type, cron_expr, params, enabled)
        VALUES
            (CAST(:sync_id AS uuid), 'freshrss-sync', 'freshrss_sync', '*/10 * * * *',
             CAST(:sync_params AS jsonb), true),
            (CAST(:index_id AS uuid), 'index-pending', 'index_pending', '*/5 * * * *',
             CAST(:index_params AS jsonb), true)
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        seed_sql.bindparams(
            sync_id=SEED_FRESHRSS_SYNC_ID,
            sync_params='{"limit_per_source": 2}',
            index_id=SEED_INDEX_PENDING_ID,
            index_params='{"batch_size": 20, "stale_after_minutes": 60}',
        )
    )


def downgrade() -> None:
    """删除定时任务两张表；任务执行历史随表一起消失，新闻业务数据不受影响。"""

    op.drop_table("scheduled_job_runs")
    op.drop_table("scheduled_jobs")
