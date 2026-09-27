"""创建用量记录表。

用量库（llmops）的第一张表，也是唯一一张：一次模型调用一行，只追加、不修改、不删除。
它由这套独立的迁移环境管理，业务库的迁移链看不到它，见
docs/adr/0032-usage-data-in-separate-database.md。

两条索引覆盖三种查询：不带模型筛选的明细与汇总、带模型筛选的明细与汇总、以及模型名列表要的
「本账号下有哪些模型」。运行标识只存列、不建索引——本期没有按它筛选的入口。
"""

from alembic import op
import sqlalchemy as sa

revision = "39e5bca95230"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="自增主键；只用于分页时的稳定次序。"),
        sa.Column("call_id", sa.Uuid(), nullable=False, comment="这条用量记录自己的标识，跨进程唯一；重复写入被唯一约束挡下。"),
        sa.Column("user_id", sa.Uuid(), nullable=True, comment="归属账号的逻辑引用；库上无外键约束，账号被删除时本行不删。NULL 表示归属未知。"),
        sa.Column("thread_id", sa.Uuid(), nullable=True, comment="所属 Agent 会话的逻辑引用；库上无外键约束，会话被删除时本行不删。NULL 表示归属未知。"),
        sa.Column("run_id", sa.Uuid(), nullable=True, comment="所属运行（一次提问到最终回答）的逻辑引用；本期没有按它筛选的入口，因此不建索引。"),
        sa.Column("model_name", sa.String(length=128), nullable=True, comment="这次调用实际使用的模型名；主模型与备用模型靠它分辨。NULL 表示取不到。"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, comment="上游报的输入 token；已包含缓存命中的部分，因此不能与输出、缓存相加当消耗。"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, comment="上游报的输出 token。"),
        sa.Column("cached_tokens", sa.Integer(), nullable=True, comment="上游报的缓存命中 token；NULL 表示上游没报这一项，与「报了 0」是两件事。"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, comment="上游报的合计 token；上游没报时由输入加输出补齐。"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, comment="从调用进入包装层到结束之间的单调时钟差值毫秒数。"),
        sa.Column("status", sa.String(length=16), nullable=False, comment="结束方式：completed 为正常跑完，failed 为上游报错、超时或被取消。"),
        sa.Column("source", sa.String(length=16), nullable=False, comment="这批数字的来源：upstream 为上游自报（哪怕总量是 0），missing 为上游没报。"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, comment="调用进入包装层的 UTC 时刻；列带时区，会话时区也钉成 UTC。"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id", name="uq_usage_records_call_id"),
    )
    op.create_index("ix_usage_records_user_occurred", "usage_records", ["user_id", "occurred_at"], unique=False)
    op.create_index("ix_usage_records_user_model_occurred", "usage_records", ["user_id", "model_name", "occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_usage_records_user_model_occurred", table_name="usage_records")
    op.drop_index("ix_usage_records_user_occurred", table_name="usage_records")
    op.drop_table("usage_records")
