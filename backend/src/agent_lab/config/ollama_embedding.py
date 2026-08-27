"""定义 Ollama Embedding 层的独立运行时配置。

Embedding 会把文本映射成供机器比较语义距离的数值向量；它不会生成摘要，也不会
回答问题。本模块只负责从环境读取并校验 Ollama 地址、模型、凭据、超时和批量大小，
不发起网络请求、不生成向量，也不包含 Qdrant、生成式 LLM 或 RAG 配置。
"""

from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# BaseSettings 与 BaseModel相比，除了校验，还会从.env中读取
class OllamaEmbeddingSettings(BaseSettings):
    """访问远程 Ollama Embedding 模型所需的进程级配置。

    该对象通常在进程内创建一次并由 Provider 复用。它只保存连接参数，不持有网络
    连接。API Key 使用 ``SecretStr``，因此配置对象的 ``repr`` 和校验输出不会显示
    明文；真正构造客户端 header 时才会在一个受控位置读取秘密值。
    """

    # AnyHttpUrl 强制必须合法的url
    base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("https://ollama.example.com"),
        description=(
            "Ollama HTTP API 根地址，来源于 OLLAMA_BASE_URL；必须是合法的 HTTP(S) "
            "URL，不能为空，Provider 会把它交给官方 Ollama 客户端。"
        ),
    )
    embedding_model: str = Field(
        default="bge-m3:567m",
        min_length=1,
        description=(
            "用于 document 与 query Embedding 的同一个 Ollama 模型名称，来源于 "
            "OLLAMA_EMBEDDING_MODEL；去除首尾空白后不能为空。"
        ),
    )
    api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description=(
            "反向代理可选 API Key，来源于 OLLAMA_API_KEY；允许为空，明文只在集中构造 "
            "Authorization header 时短暂读取，不得写入日志或异常。"
        ),
    )
    embedding_request_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        description=(
            "单次远程 Embedding HTTP 请求的总超时秒数，来源于 "
            "OLLAMA_EMBEDDING_REQUEST_TIMEOUT_SECONDS；必须大于零。"
        ),
    )
    embedding_batch_size: int = Field(
        default=16,
        gt=0,
        description=(
            "每次发送给 Ollama 的 document 文本上限，来源于 "
            "OLLAMA_EMBEDDING_BATCH_SIZE；必须大于零，用于平衡吞吐、延迟、显存和超时风险。"
        ),
    )

    @field_validator("embedding_model")
    @classmethod
    def normalize_embedding_model(cls, model: str) -> str:
        """去除模型名称两端空白，并拒绝纯空白名称。

        Args:
            model: 从默认值或环境变量解析出的 Ollama 模型名称。

        Returns:
            可直接传给 ``OllamaEmbeddings`` 的规范化模型名称。

        Raises:
            ValueError: 模型名称只包含空白字符时抛出。
        """

        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("Ollama 嵌入模型不能为空白")
        return normalized_model

    # 从 .env 文件读
    # env_prefix="OLLAMA_" 每个字段自动对应环境变量 OLLAMA_xxx
    # extra="ignore" 环境里多出的 OLLAMA_* 忽略，不报错
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="OLLAMA_",
        extra="ignore",
    )


def build_ollama_headers(api_key: SecretStr) -> dict[str, str]:
    """集中构造远程 Ollama 反向代理认证 header。

    Args:
        api_key: 已由 Pydantic Settings 保护的可选密钥。

    Returns:
        密钥为空时返回空字典；非空时返回 Bearer ``Authorization`` header。

    Notes:
        Ollama 原生 API 不规定 API Key 协议。当前反向代理尚未提供可发现的认证契约，
        因此这里采用常见的 ``Authorization: Bearer <key>`` 默认约定。若部署实际使用
        ``X-API-Key`` 或其他方案，只修改本函数即可，业务层和 Provider 不应拼装 header。
    """

    secret = api_key.get_secret_value().strip()
    return {"Authorization": f"Bearer {secret}"} if secret else {}


# @lru_cache 模块级别的缓存
@lru_cache
def get_ollama_embedding_settings() -> OllamaEmbeddingSettings:
    """读取并缓存独立的 Ollama Embedding 配置（进程内只解析一次）。

    为什么把 OLLAMA 配置单独拆一个 Settings 而不是塞进通用 Settings：
    这些凭据只在真正调 Ollama 时才需要，拆开可以让「只用数据库（如健康检查）」
    的代码路径不必要求 Ollama 配置齐全。

    Returns:
        进程内复用的、已完成环境变量解析和约束校验的配置。

    Raises:
        pydantic.ValidationError: URL、模型、超时或批量大小不满足字段约束时抛出。

    Notes:
        读取配置不进行网络、Embedding、数据库或向量库 I/O。
    """

    return OllamaEmbeddingSettings()
