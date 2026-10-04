"""定义生成式 LLM 与 LangSmith 追踪的独立运行时配置。

生成式 LLM 负责「读了检索结果之后用自然语言回答」，和 Embedding 是两件不同的事：
Embedding 把文本变成向量供比较距离（见 ``config.ollama_embedding``），本模块配置的模型
产出文字。

**本模块只装不属于「某一个模型」的那几项**：温度、单次请求超时、User-Agent、会话记忆连接池
大小。接入类型、地址、凭据与模型名已经退休、不再从环境变量读——它们属于某一个模型，由
后台的模型目录（``llm_providers`` / ``llm_models``）配，多个模型各有各的一份（见
``docs/adr/0046-model-catalog-and-user-model-choice.md``）。

本模块只从环境读取并校验这几项，不发起网络请求、不构造客户端（构造在 ``agent.chat_model``）、
不持有连接，也不包含 Embedding、Qdrant 或 checkpointer 的数据库配置。
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmProvider(StrEnum):
    """可选的生成式模型接入方式。

    两个分支的差别只在「用哪个客户端类、认证怎么带」，对上层完全透明：模型目录里的一行
    渠道存的就是它（``llm_providers.provider``），构造客户端的 ``agent.chat_model.build_chat_model``
    按它分支；``schemas.llm_providers`` 与 ``api/llm_providers`` 只把它当取值读写，其余代码
    只拿到 ``BaseChatModel``。新增第三种 provider 时只改构造那一个函数。
    """

    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"


class LlmSettings(BaseSettings):
    """调用生成式 LLM 所需的**进程级**配置。

    进程内解析一次并被所有请求共享。它只保存对所有模型都相同的参数（温度、超时、
    User-Agent、会话记忆连接池大小），因此 ``repr`` 里不会出现任何凭据：渠道凭据在模型目录里，
    以密文落库（见 ``services.llm_credential_cipher``）。
    """

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
            "客户端。它不属于某一个模型，所以留在环境变量里（与温度、超时同理）。"
            "之所以需要这个开关：部分 OpenAI 兼容中转站会按 User-Agent 拦截通用 "
            "SDK 流量，openai SDK 默认发的 'OpenAI/Python x.y.z' 会被判作 403 "
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
    追踪的开与关由 ``api/agent_chat.py`` 把本配置注入每次对话、``agent/streaming.py`` 用
    ``langsmith.run_helpers.tracing_context`` 按运行显式打开，全程不修改 ``os.environ``。
    因此改这些值需要重启进程才生效。
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

    为什么和 Embedding 配置分开：这些参数只在 Agent 对话时需要（其中连接池那项也只服务会话
    记忆），拆开可以让「只做检索」或「只用数据库」的代码路径不必要求 LLM 配置齐全。

    Returns:
        进程内复用的、已完成环境变量解析和约束校验的配置。

    Raises:
        pydantic.ValidationError: 温度、超时或连接池大小不满足约束。

    Notes:
        读取配置不进行网络、模型、数据库或向量库 I/O，也不读任何模型凭据（那些在模型目录里）。
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
