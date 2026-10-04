"""上游渠道凭据加密主密钥的运行时配置。

主密钥只有一个用途：把后台填写的渠道凭据加密落库、在构造客户端时解密。它与**渠道凭据本身**
是两件事——后者属于某一条渠道（存在 ``llm_providers`` 里、由本密钥加密），前者是所有渠道
凭据共用的那把加密钥匙。

**它缺失或不是合法密钥时不阻断进程启动。** 只想用检索、或者部署里还没有任何需要凭据的渠道时，
进程照样要能起来（与 README 里「只想用检索可以完全不管 LLM 配置」同一条约定）。所以这个模块
只负责读环境，校验发生在真的要用密钥的那一刻（见 ``services.llm_credential_cipher``）。
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmCredentialSettings(BaseSettings):
    """渠道凭据加密的进程级配置。

    Attributes:
        credential_key: 主密钥，来源于 ``LLM_CREDENTIAL_KEY``；必须是 urlsafe base64 编码的
            32 字节 Fernet 密钥。默认空字符串，表示「还没配」——不是启动错误，只让需要加密的
            那次保存返回 503。
    """

    credential_key: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description=(
            "上游渠道凭据的 Fernet 主密钥，来源于 LLM_CREDENTIAL_KEY；必须是 urlsafe base64 "
            "编码的 32 字节。留空表示未配置，此时带凭据的渠道保存不了、但不影响其他功能。"
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LLM_",
        extra="ignore",
    )


@lru_cache
def get_llm_credential_settings() -> LlmCredentialSettings:
    """读取并缓存渠道凭据主密钥配置（进程内只解析一次）。

    Returns:
        进程内复用的配置对象。

    Raises:
        pydantic.ValidationError: 配置对象构造失败；本字段有默认值，正常环境不会触发。

    Notes:
        只读环境与 ``.env``，不校验密钥是否合法（那一步在真正加密时做）、不做任何 I/O。
    """

    return LlmCredentialSettings()


__all__ = ["LlmCredentialSettings", "get_llm_credential_settings"]
