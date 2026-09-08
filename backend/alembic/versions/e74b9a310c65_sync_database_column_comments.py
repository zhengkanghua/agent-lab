"""同步第一阶段遗留的字段说明，使迁移结果与 ORM 元数据一致。"""

from alembic import op

revision = "e74b9a310c65"
down_revision = "d63e0891f752"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 只更新 PostgreSQL 列说明，不改变字段类型、约束或业务数据。
    op.alter_column("documents", "knowledge_base_id", comment="Document 实际归属的 KnowledgeBase。")
    op.alter_column("documents", "source_id", comment="可选的外部来源；人工或文件 Document 可以为空。")
    op.alter_column("documents", "external_id", comment="可选的来源内稳定外部标识。")
    op.alter_column("documents", "url", comment="可选原始文档地址。")
    op.alter_column("documents", "mime_type", comment="文档内容格式，例如 text/plain、application/pdf。")
    op.alter_column(
        "scheduled_job_runs", "stats",
        comment="脱敏执行统计（数量与按异常类型的聚合计数，失败记录含 error_reason 枚举），结构与手动流水线同口径。",
    )
    op.alter_column("sources", "knowledge_base_id", comment="来源当前绑定的 KnowledgeBase；为空表示尚未配置。")


def downgrade() -> None:
    op.alter_column("documents", "knowledge_base_id", comment=None)
    op.alter_column("documents", "source_id", comment="关联 sources.id。")
    op.alter_column("documents", "external_id", comment="文档在来源中的唯一标识，例如 FreshRSS article id。")
    op.alter_column("documents", "url", comment="原始文档地址。")
    op.alter_column("documents", "mime_type", comment=None)
    op.alter_column(
        "scheduled_job_runs", "stats",
        comment="脱敏执行统计（数量与按异常类型的聚合计数），结构与手动流水线同口径。",
    )
    op.alter_column("sources", "knowledge_base_id", comment=None)
