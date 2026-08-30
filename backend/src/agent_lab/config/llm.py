"""定义生成式 LLM 与 LangSmith 追踪的独立运行时配置。

生成式 LLM 负责「读了检索结果之后用自然语言回答」，和 Embedding 是两件不同的事：
Embedding 把文本变成向量供比较距离（见 ``config.ollama_embedding``），本模块配置的模型
产出文字。本模块只从环境读取并校验 provider、地址、模型名、凭据、超时和采样温度，
不发起网络请求、不构造客户端（构造在 ``agent.chat_model``）、不持有连接，也不包含
Embedding、Qdrant 或 checkpointer 的数据库配置。
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmProvider(StrEnum):
    """可选的生成式模型接入方式。

    两个分支的差别只在「用哪个客户端类、认证怎么带」，对上层完全透明：
    ``agent.chat_model.build_chat_model`` 是唯一读取本枚举的地方，其余代码只拿到
    ``BaseChatModel``。新增第三种 provider 时只改那一个函数。
    """

    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"


class LlmSettings(BaseSettings):
    """调用生成式 LLM 所需的进程级配置。

    进程内解析一次并被所有请求共享。它只保存连接参数，不持有 HTTP 连接或模型客户端。
    API Key 用 ``SecretStr`` 包住，因此配置对象的 ``repr`` 和 Pydantic 校验输出都不会
    显示明文；只有构造客户端那一处会读取秘密值。
    """

    provider: LlmProvider = Field(
        default=LlmProvider.OPENAI_COMPATIBLE,
        description=(
            "生成式模型接入方式，来源于 LLM_PROVIDER；只能是 openai_compatible 或 "
            "ollama，决定 build_chat_model 走哪个客户端分支。"
        ),
    )
    base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("https://api.openai.com/v1"),
        description=(
            "生成式模型的 HTTP API 根地址，来源于 LLM_BASE_URL；必须是合法 HTTP(S) "
            "URL。OpenAI 兼容中转站通常需要带 /v1 后缀，Ollama 分支填 Ollama 服务根地址。"
        ),
    )
    api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description=(
            "生成式模型的 API Key，来源于 LLM_API_KEY；openai_compatible 分支必须非空，"
            "Ollama 分支允许为空。明文只在构造客户端时读取一次，不得写入日志或异常。"
        ),
    )
    model: str = Field(
        default="gpt-4o-mini",
        min_length=1,
        description=(
            "主模型名称，来源于 LLM_MODEL；去除首尾空白后不能为空，必须是所配 "
            "base_url 那一侧真实存在的模型名。"
        ),
    )
    fallback_model: str = Field(
        default="gpt-4o-mini",
        min_length=1,
        description=(
            "主模型连续失败后降级使用的备用模型名称，来源于 LLM_FALLBACK_MODEL；"
            "允许与 model 相同（此时降级只等于多一次重试）。"
        ),
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        allow_inf_nan=False,
        description=(
            "采样温度，来源于 LLM_TEMPERATURE；范围 0..2。新闻问答要求可复现且少编造，"
            "所以默认 0；它不影响是否调用工具。"
        ),
    )
    request_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description=(
            "单次模型 HTTP 请求的总超时秒数，来源于 LLM_REQUEST_TIMEOUT_SECONDS；"
            "必须大于零。它约束一次模型调用，不是整个运行（run）的时长上限。"
        ),
    )
    user_agent: str = Field(
        default="agent-lab",
        description=(
            "调用生成式模型时发送的 User-Agent，来源于 LLM_USER_AGENT；留空表示不覆盖、"
            "沿用底层 SDK 的默认值。默认值让请求如实报出自己是本项目，而不是伪装成别的"
            "客户端。之所以需要这个开关：部分 OpenAI 兼容中转站会按 User-Agent 拦截通用 "
            "SDK 流量，openai SDK 默认发的 'OpenAI/Python x.y.z' 会被判为 403 "
            "PermissionDeniedError（消息形如 'Your request was blocked.'），而同一个 Key "
            "换个 User-Agent 就能正常调用——凭据没问题，被拒的是客户端身份。"
        ),
    )
    checkpoint_pool_size: int = Field(
        default=4,
        ge=1,
        le=32,
        strict=True,
        description=(
            "会话记忆专用 PostgreSQL 连接池的最大连接数，来源于 LLM_CHECKPOINT_POOL_SIZE；"
            "范围 1..32。它和 SQLAlchemy 的业务连接池是两套独立连接，不共享。"
        ),
    )

    @field_validator("model", "fallback_model")
    @classmethod
    def normalize_model_name(cls, model: str) -> str:
        """去除模型名两端空白，并拒绝纯空白名称。

        Args:
            model: 从默认值或环境变量解析出的模型名称。

        Returns:
            可直接交给模型客户端的规范化名称。

        Raises:
            ValueError: 名称只包含空白字符时抛出。
        """

        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("生成式模型名称不能为空白")
        return normalized_model

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LLM_",
        extra="ignore",
    )


class LangSmithSettings(BaseSettings):
    """LangSmith 追踪的进程级配置。

    字段名刻意对齐 LangSmith SDK 自己的环境变量（``LANGSMITH_TRACING`` 等），这样照着
    官方文档配置就能生效，不需要在两套命名之间换算。

    但读取方式和 SDK 不同：SDK 走 ``os.environ`` 且带 ``lru_cache``，而本项目用
    pydantic-settings 读 ``.env``，值不会进入 ``os.environ``，所以 SDK 自己看不到它们。
    追踪的开与关由 ``agent.runtime`` 用 ``langsmith.run_helpers.tracing_context`` 显式
    传入，全程不修改 ``os.environ``。因此改这些值需要重启进程才生效。
    """

    tracing: bool = Field(
        default=False,
        description=(
            "是否把运行轨迹上报 LangSmith，来源于 LANGSMITH_TRACING；默认关闭。"
            "开启意味着提问内容和检索到的新闻正文会离开本机、发往境外云服务。"
        ),
    )
    api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        description=(
            "LangSmith API Key，来源于 LANGSMITH_API_KEY；tracing 为 true 时必须非空，"
            "否则追踪会静默失败。明文只在构造 langsmith.Client 时读取。"
        ),
    )
    project: str = Field(
        default="agent-lab",
        min_length=1,
        description=(
            "轨迹归属的 LangSmith 项目名，来源于 LANGSMITH_PROJECT；项目不存在时由"
            "LangSmith 侧自动创建。"
        ),
    )
    endpoint: AnyHttpUrl = Field(
        default=AnyHttpUrl("https://api.smith.langchain.com"),
        description=(
            "LangSmith API 根地址，来源于 LANGSMITH_ENDPOINT；自托管 LangSmith 时改这里。"
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LANGSMITH_",
        extra="ignore",
    )


@lru_cache
def get_llm_settings() -> LlmSettings:
    """读取并缓存生成式 LLM 配置（进程内只解析一次）。

    为什么和 Embedding 配置分开：这些凭据只在 Agent 对话时需要，拆开可以让「只做检索」
    或「只用数据库」的代码路径不必要求 LLM 配置齐全——没配中转站也能正常用检索接口。

    Returns:
        进程内复用的、已完成环境变量解析和约束校验的配置。

    Raises:
        pydantic.ValidationError: provider、URL、模型名、温度、超时或连接池大小不满足约束。

    Notes:
        读取配置不进行网络、模型、数据库或向量库 I/O。
    """

    return LlmSettings()


@lru_cache
def get_langsmith_settings() -> LangSmithSettings:
    """读取并缓存 LangSmith 追踪配置（进程内只解析一次）。

    Returns:
        进程内复用的追踪开关、凭据、项目名和端点。

    Raises:
        pydantic.ValidationError: 项目名为空或端点不是合法 URL。

    Notes:
        读取配置不进行任何网络 I/O，也不构造 langsmith.Client。
    """

    return LangSmithSettings()


__all__ = [
    "LangSmithSettings",
    "LlmProvider",
    "LlmSettings",
    "get_langsmith_settings",
    "get_llm_settings",
]
