"""建立「可用模型」表 add llm models。

模型目录的第二步：上一条迁移建了「上游渠道」（模型从哪来、凭据是什么），本迁移建挂在渠道
下面的**可用模型**（上游模型名、展示名、上下文窗口、是否默认、是否启用），用户在会话里挑的
就是这些条目。

三个库上对象，都各守一件事：

1. ``provider_id`` 那一列**不建外键约束**（业务层维护的逻辑外键，见根 AGENTS.md 的「业务与
   数据约束」第 3 条与 ADR 0028），列说明里写明了这一点；
2. ``uq_llm_models_provider_upstream_name``：同一渠道内上游模型名唯一，管理员不用靠记；
3. ``uq_llm_models_single_default``：**部分唯一索引**，全目录最多一条 ``is_default`` 为真。
   这是「目录里恰好有一个默认」在并发下的兜底——两个请求同时设默认时，落败的一方会撞上它。
   索引不属于「不建外键约束」那条限制（同一条约束第 4 条）。索引不写 ``comment``，全仓一致。

表结构与 ORM 逐列对齐（含列说明）：``agent_lab.models.llm_model.LlmModelRecord``。真库上那一步
的验收靠 ``alembic check``——它会逐列比对库与模型。

Revision ID: d2f5a8c31b76
Revises: e9b3c7a41d58
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d2f5a8c31b76"
down_revision: str | Sequence[str] | None = "e9b3c7a41d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 ``llm_models`` 一张表与它的一条部分唯一索引；没有存量数据需要迁移或回填。

    库里此刻一行可用模型都没有，所以不会有存量行撞上「最多一条默认」那条索引。
    """

    op.create_table(
        "llm_models",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="Python 服务生成的可用模型主键。",
        ),
        sa.Column(
            "provider_id",
            sa.Uuid(),
            nullable=False,
            comment="所属上游渠道 id。业务层维护的逻辑外键，库上无约束。",
        ),
        sa.Column(
            "upstream_model_name",
            sa.String(length=255),
            nullable=False,
            comment="上游那一侧真实存在的模型名，直接交给模型客户端；同一渠道内不可重复。",
        ),
        sa.Column(
            "display_name",
            sa.String(length=255),
            nullable=True,
            comment="展示名称，只用于界面；留空时界面回落到上游模型名。",
        ),
        sa.Column(
            "context_window",
            sa.Integer(),
            nullable=False,
            comment=(
                "上游模型的上下文窗口 token 数，必填；压缩什么时候触发按它的比例算，"
                "一轮里单次工具输出能放多长也取它的比例。填小了会提前压缩并截断工具输出。"
            ),
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                "是否是没选模型时使用的默认条目；全目录最多一条为真（部分唯一索引兜底），"
                "只能被「把另一条设为默认」这条路径改写。"
            ),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
            comment="是否启用；停用的模型保留配置，只是不再出现在可选列表里。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="记录首次写入 PostgreSQL 的时间。",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="记录最后一次通过 ORM 更新的时间。",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_id",
            "upstream_model_name",
            name="uq_llm_models_provider_upstream_name",
        ),
        comment="可用模型：模型目录里用户能选到的条目，挂在上游渠道下面。",
    )
    op.create_index(
        "uq_llm_models_single_default",
        "llm_models",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    """删除这条索引与这张表。

    回滚的代价：后台配好的可用模型连同「哪一条是默认」一起消失，会话里选过模型的那些选择也
    就没有对应的条目了（选择记在会话行上，本迁移不动它）。环境变量那一侧不受影响——本迁移
    没有动过它们。
    """

    op.drop_index("uq_llm_models_single_default", table_name="llm_models")
    op.drop_table("llm_models")
