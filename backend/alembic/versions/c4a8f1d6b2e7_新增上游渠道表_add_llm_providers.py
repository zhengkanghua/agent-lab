"""建立「上游渠道」配置表 add llm providers。

模型目录的第一步：平台此前只有一个模型（地址与凭据全在环境变量 ``LLM_*`` 里），这次开始
把「模型从哪来、凭据是什么」搬到后台可维护的表里。本迁移**只建这一张表**，不建挂渠道下面的
「可用模型」，也不动环境变量（那两步分别是后面的改动，各自有验收条件）。

表结构与 ORM 逐列对齐（含列说明）：``agent_lab.models.llm_provider.LlmProviderRecord``。
真库上那一步的验收靠 ``alembic check``——它会逐列比对库与模型。

Revision ID: c4a8f1d6b2e7
Revises: f2b9c7d41a08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c4a8f1d6b2e7"
down_revision: str | Sequence[str] | None = "f2b9c7d41a08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 ``llm_providers`` 一张表；没有存量数据需要迁移或回填。"""

    op.create_table(
        "llm_providers",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="Python 服务生成的上游渠道主键。",
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
            comment="渠道展示名称，可修改，不要求唯一。",
        ),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            comment=(
                "接入类型，取值见 config.llm.LlmProvider（openai_compatible 或 ollama），"
                "决定构造客户端走哪个分支；openai_compatible 要求凭据非空，ollama 允许为空。"
            ),
        ),
        sa.Column(
            "base_url",
            sa.Text(),
            nullable=False,
            comment="上游 HTTP API 根地址；OpenAI 兼容中转站通常需要带 /v1 后缀。",
        ),
        sa.Column(
            "credential_ciphertext",
            sa.Text(),
            nullable=True,
            comment=(
                "接入凭据的 Fernet 密文，主密钥来自 LLM_CREDENTIAL_KEY；为空表示这条渠道没有凭据。"
                "明文不落库、不进日志。"
            ),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
            comment="是否启用；停用的渠道保留配置，只是不再参与选择。",
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
        comment="上游渠道配置：模型目录里接入类型、地址与凭据的事实来源。",
    )


def downgrade() -> None:
    """删除这张表。

    回滚的代价：后台配好的渠道与它们的加密凭据一起消失，得重新填一遍（凭据明文平台不留第二
    份，从备份里也还原不回来）。环境变量那一侧不受影响——本迁移没有动过它们。
    """

    op.drop_table("llm_providers")
