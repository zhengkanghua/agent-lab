"""保存上传文件身份，并允许明确 ID 的文档删除意图。"""

from alembic import op
import sqlalchemy as sa

revision = "c49a70d2e831"
down_revision = "b38f9a7c6d21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column(
        "upload_filename", sa.String(255), nullable=True,
        comment="上传文件的原始文件名；非空表示上传资料，不作为唯一键。",
    ))
    op.alter_column(
        "document_deletions", "cutoff_date", nullable=True,
        comment="保留期清理的截止时刻；为空表示用户按明确 ID 删除上传文档。",
    )


def downgrade() -> None:
    # 未完成的人工删除不能被降级静默丢弃或误改成按时间清理。
    # 检查留在 SQL 内，在线执行和离线生成的降级脚本遵守相同边界。
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM document_deletions WHERE cutoff_date IS NULL) THEN
                RAISE EXCEPTION '请先完成上传文档的删除待办，再降级。';
            END IF;
        END;
        $$
    """))
    op.alter_column("document_deletions", "cutoff_date", nullable=False, comment=None)
    op.drop_column("documents", "upload_filename")
