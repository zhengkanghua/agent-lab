"""上游渠道配置表；一行渠道是一份「模型从哪来、凭据是什么」的连接参数。

凭据以 Fernet 密文落在 ``credential_ciphertext``，明文只在「请求体 → 加密 → 写库」这一条
短路径上出现；读取接口只回「已配置 / 未配置」。主密钥来自 ``LLM_CREDENTIAL_KEY``，它挡的是
「库被单独读走」（备份、只读副本、只读账号），挡不住「服务器被拿下」。

本表只有停用，没有删除入口（与 ``KnowledgeBase`` 的「停用保留数据」一致）。
"""

from uuid import UUID, uuid4

from sqlalchemy import Boolean, String, Text, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column

from agent_lab.db.base import Base, TimestampMixin


class LlmProviderRecord(TimestampMixin, Base):
    """一行代表一个上游渠道：显示名、接入类型、地址、凭据密文、启用状态。

    显示名**不加唯一约束**：它只用于后台识别，渠道之间靠 id 引用（挂在本渠道下的模型按
    ``provider_id`` 指向它），重名不产生歧义，加了约束反而会让管理员在名字撞车时改不动配置。
    """

    __tablename__ = "llm_providers"
    __table_args__ = (
        {"comment": "上游渠道配置：模型目录里接入类型、地址与凭据的事实来源。"},
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4,
        comment="Python 服务生成的上游渠道主键。",
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="渠道展示名称，可修改，不要求唯一。",
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment=(
            "接入类型，取值见 config.llm.LlmProvider（openai_compatible 或 ollama），"
            "决定构造客户端走哪个分支；openai_compatible 要求凭据非空，ollama 允许为空。"
        ),
    )
    base_url: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="上游 HTTP API 根地址；OpenAI 兼容中转站通常需要带 /v1 后缀。",
    )
    credential_ciphertext: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment=(
            "接入凭据的 Fernet 密文，主密钥来自 LLM_CREDENTIAL_KEY；为空表示这条渠道没有凭据。"
            "明文不落库、不进日志。"
        ),
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true(),
        comment="是否启用；停用的渠道保留配置，只是不再参与选择。",
    )
