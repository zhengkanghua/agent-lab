"""同步用量表 ``model_name`` 列的说明。

这一列记的是「这次调用实际使用的那一个模型名」。原说明里那句「主模型与备用模型靠它分辨」
在备用模型删除之后不再成立（见 docs/adr/0046-model-catalog-and-user-model-choice.md），
而列说明会进 DDL、被人直接从库里读到——留着一句已经不存在的概念，等于让人以为库里还分主备。

**只改说明文字，不动类型、约束、索引与数据。** 为什么值得单独一条迁移，而不是「反正程序
不读它」：``alembic check`` 是发现真漂移（缺列、类型不对、少索引）的工具，长期报这一条会
让真问题被噪声盖住；而且下一次 ``revision --autogenerate`` 会把这条说明修改顺手夹带进来。

Revision ID: b3e7d1a94c58
Revises: 39e5bca95230
"""

from alembic import op


revision: str = "b3e7d1a94c58"
down_revision: str | None = "39e5bca95230"
branch_labels: str | None = None
depends_on: str | None = None

# 目标值：与 ORM 上的注解逐字一致。改动这里必须同时改模型，反之亦然——由
# ``tests/test_usage_model_name_comment_migration.py`` 钉住，所以两边不会再静默分叉。
MODEL_NAME_COMMENT = "这次调用实际使用的模型名；NULL 表示取不到。"

# 回滚目标：把库还原成本迁移之前的样子，即建表那条迁移（39e5bca95230）写下的原文。
PREVIOUS_MODEL_NAME_COMMENT = "这次调用实际使用的模型名；主模型与备用模型靠它分辨。NULL 表示取不到。"


def upgrade() -> None:
    """把这句说明写进库。"""

    op.alter_column("usage_records", "model_name", comment=MODEL_NAME_COMMENT)


def downgrade() -> None:
    """换回旧措辞。

    只碰这一列，不整表刷新说明——回滚不该顺手改别人负责的文字。
    """

    op.alter_column("usage_records", "model_name", comment=PREVIOUS_MODEL_NAME_COMMENT)
