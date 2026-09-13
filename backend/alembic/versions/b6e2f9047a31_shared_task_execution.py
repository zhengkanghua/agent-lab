"""公共任务持久受理、策略与投递；保留旧执行身份及未确认写占用。

切换前必须停用旧 scheduler、API 后台执行和文档消费者。旧记录没有参数快照时
明确保留缺失，不使用当前配置伪造失败时的参数。此脚本不处理业务待办或远端存储。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "b6e2f9047a31"
down_revision = "a91b3c7d5e20"
branch_labels = None
depends_on = None

POLICY = '{"max_retries":3,"retry_delay_seconds":30,"history_retention_days":30}'
ACTIVE = "status IN ('queued', 'waiting_resource', 'running', 'retry_wait', 'needs_attention')"


def upgrade():
    op.add_column("write_operations", sa.Column("claim_token", sa.Uuid(),
        comment="任务领取身份；撤销尚未开始的领取时，仅清理该代次的准备占用。"))
    op.add_column("scheduled_jobs", sa.Column("next_run_at", sa.DateTime(timezone=True)))
    op.drop_constraint("fk_scheduled_job_runs_job_id_scheduled_jobs", "scheduled_job_runs", type_="foreignkey")
    op.alter_column("scheduled_job_runs", "job_id", nullable=True)
    op.create_foreign_key("fk_scheduled_job_runs_job_id_scheduled_jobs", "scheduled_job_runs", "scheduled_jobs", ["job_id"], ["id"], ondelete="SET NULL")
    op.alter_column("scheduled_job_runs", "started_at", nullable=True)
    op.alter_column("scheduled_job_runs", "status", type_=sa.String(24))
    for column in (
        sa.Column("source_job_id", sa.Uuid(), comment="原周期配置身份，删除配置后仍保留。"),
        sa.Column("task_type", sa.String(64)),
        sa.Column("task_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("actor", sa.String(160), comment="受理主体的稳定身份，不保存邮箱或凭据。"),
        sa.Column("concurrency_key", sa.String(160), comment="业务声明的未结束执行互斥身份，例如文档待办消费者。"),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("dispatch_after", sa.DateTime(timezone=True)),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("dispatch_error_type", sa.String(128)),
        sa.Column("delivery_generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("delivery_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("wait_reason", sa.String(512)),
        sa.Column("recovery", JSONB(), nullable=False, server_default="{}"),
        sa.Column("policy_snapshot", JSONB()),
        sa.Column("retry_of", sa.Uuid(), sa.ForeignKey("scheduled_job_runs.id", ondelete="RESTRICT")),
    ):
        op.add_column("scheduled_job_runs", column)
    op.execute(sa.text("""
        UPDATE scheduled_job_runs r SET source_job_id = r.job_id,
            task_type = COALESCE(r.config_snapshot->>'task_type', j.task_type),
            actor = 'legacy:unknown', accepted_at = r.started_at,
            available_at = r.started_at, dispatch_after = r.started_at,
            attempts = CASE WHEN r.status = 'skipped' THEN 0 ELSE 1 END,
            recovery = CASE WHEN r.config_snapshot ? 'params' THEN '{}'::jsonb
                ELSE jsonb_build_object('legacy_parameters_unavailable', true) END
        FROM scheduled_jobs j WHERE j.id = r.job_id
    """))
    op.execute(sa.text("UPDATE scheduled_job_runs SET policy_snapshot = CAST(:policy AS jsonb)").bindparams(policy=POLICY))
    op.execute("""UPDATE scheduled_job_runs r SET status = 'needs_attention',
        wait_reason = '旧执行者或远端写入状态尚未确认，请先核实再使用维护入口。',
        finished_at = NULL
        WHERE status = 'running' OR EXISTS (SELECT 1 FROM write_operations w WHERE w.run_id = r.id)
            OR stats->>'needs_attention' = 'true'""")
    op.execute("UPDATE scheduled_job_runs SET finished_at = started_at WHERE status = 'skipped' AND finished_at IS NULL")
    op.execute("UPDATE scheduled_job_runs SET expires_at = finished_at + interval '30 days' WHERE status IN ('succeeded', 'failed', 'skipped')")
    # 旧数据中若有多条未确认执行，必须核实它们，而不是删除历史以凑出唯一约束。
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT source_job_id FROM scheduled_job_runs WHERE """ + ACTIVE + """
            GROUP BY source_job_id HAVING COUNT(*) > 1) THEN
            RAISE EXCEPTION '同一配置存在多条未确认旧执行，请先核实旧执行及写占用再升级';
        END IF;
    END $$""")
    for name in ("task_type", "actor", "accepted_at", "available_at", "dispatch_after", "policy_snapshot"):
        op.alter_column("scheduled_job_runs", name, nullable=False)
    op.alter_column("scheduled_job_runs", "stats", server_default="{}")
    op.create_unique_constraint("uq_job_runs_schedule_event", "scheduled_job_runs", ["source_job_id", "scheduled_for"])
    op.create_index("uq_job_runs_active_job", "scheduled_job_runs", ["source_job_id"], unique=True, postgresql_where=sa.text(ACTIVE))
    op.create_index("uq_job_runs_active_business", "scheduled_job_runs", ["concurrency_key"], unique=True, postgresql_where=sa.text(ACTIVE))
    op.create_index("ix_job_runs_dispatch", "scheduled_job_runs", ["available_at", "dispatch_after"], postgresql_where=sa.text("status IN ('queued', 'waiting_resource', 'retry_wait') AND claim_token IS NULL"))
    op.create_index("ix_scheduled_job_runs_expires_at", "scheduled_job_runs", ["expires_at"])
    op.create_index("ix_scheduled_job_runs_retry_of", "scheduled_job_runs", ["retry_of"])
    op.create_table("task_requests",
        sa.Column("actor", sa.String(160), primary_key=True),
        sa.Column("operation", sa.String(160), primary_key=True),
        sa.Column("request_key", sa.String(128), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_requests_run_id", "task_requests", ["run_id"])
    op.create_table("task_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("policy", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(160), nullable=False),
    )
    op.execute(sa.text("INSERT INTO task_policy VALUES (1, CAST(:policy AS jsonb), now(), 'migration')").bindparams(policy=POLICY))
    op.create_table("task_policy_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous", JSONB(), nullable=False),
        sa.Column("current", JSONB(), nullable=False),
    )
    # 精确字段语义转移到公共契约，清除旧的“级联删除／运行中受理”列注释。
    for table, columns in {"scheduled_jobs": ("id", "key", "task_type", "cron_expr", "params", "enabled"),
                           "scheduled_job_runs": ("id", "job_id", "trigger_type", "status", "started_at", "finished_at", "stats", "error_type")}.items():
        op.drop_table_comment(table)
        for column in columns:
            op.alter_column(table, column, comment=None)


def downgrade():
    raise RuntimeError("公共任务已改变持久受理与历史契约，不支持会丢失回执的自动降级；请恢复切换前备份。")
