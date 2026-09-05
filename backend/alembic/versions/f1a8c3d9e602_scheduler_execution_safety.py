"""增加配置版本、执行者信息、写资源占用和可恢复删除意图。

不改变已有任务配置，不启用清理；失联的历史执行留待人工核实。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "f1a8c3d9e602"
down_revision = "e8b4c2d6a9f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scheduled_jobs", sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("scheduled_job_runs", sa.Column("config_snapshot", JSONB(), nullable=False, server_default="{}"))
    op.add_column("scheduled_job_runs", sa.Column("owner", sa.String(160), nullable=True))
    op.add_column("scheduled_job_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "write_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("resources", JSONB(), nullable=False),
        sa.Column("owner", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_write_operations_run_id", "write_operations", ["run_id"])
    # 旧执行者没有采用资源协议，无法仅凭进程停止确认远端写入已结束。
    # 保守占用两种资源，人工核实后沿同一恢复入口释放，不修改任务配置。
    op.execute(sa.text("""
        INSERT INTO write_operations (id, resources, owner, status, started_at, heartbeat_at, run_id)
        SELECT id, '["sync", "index"]'::jsonb, 'legacy-unconfirmed', 'uncertain',
               started_at, started_at, id
        FROM scheduled_job_runs WHERE status = 'running'
    """))
    op.create_table(
        "document_deletions",
        sa.Column("document_id", sa.Uuid(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("cutoff_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qdrant_deleted", sa.Boolean(), nullable=False),
        sa.Column("error_type", sa.String(128), nullable=True),
    )
    op.execute("UPDATE scheduled_job_runs SET finished_at = started_at WHERE status = 'skipped' AND finished_at IS NULL")


def downgrade() -> None:
    # 未完成占用/删除不能通过降级抹掉，否则旧写入口可能覆盖未确认结果。
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM write_operations) OR EXISTS (SELECT 1 FROM document_deletions)")):
        raise RuntimeError("仍有写操作或删除待办，核实完成后才能降级。")
    op.drop_table("document_deletions")
    op.drop_table("write_operations")
    for column in ("heartbeat_at", "owner", "config_snapshot"):
        op.drop_column("scheduled_job_runs", column)
    op.drop_column("scheduled_jobs", "config_version")
