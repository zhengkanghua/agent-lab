"""定义独立用量库（ADR 0032 里的 ``llmops``）的连接配置。

用量数据放在与业务表不同的数据库里，所以它有自己的连接串、连接池和超时，不与
``config.settings.Settings`` 共用字段。这个模块只读环境、只校验，不建 Engine、不连库。

环境变量前缀用 ``LLMOPS_``（库名），字段名则用「用量」这个词：``LLMOps`` 在本仓库只是这个库
将来独立部署后的平台形态，不是当前概念（见 ``CONTEXT.md`` 的「用量」词条），所以代码里不拿它
命名领域对象，只拿它命名那个真实的库。

**会话时区钉死 UTC，不做成配置项**：用量表的时刻列带时区，写入的就是 UTC 瞬时，如果这里允许
填别的值，写入与查询两边代码里看起来都在用 UTC，筛「某一天」的结果却会整体偏几小时。业务库的
时区是配置项（``DATABASE_TIMEZONE``），因为那里有历史包袱；这是新库，没有理由再给这个自由度。
"""

from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class UsageDatabaseSettings(BaseSettings):
    """用量库的连接参数。

    字段名与 ``Settings`` 保持一致的叫法，但前缀是 ``LLMOPS_``：``database_url`` 对应
    ``LLMOPS_DATABASE_URL``，``echo`` 对应 ``LLMOPS_ECHO``。

    Attributes:
        database_url: 用量库的 SQLAlchemy 异步连接地址。**必填**：这个字段没有默认值，
            少了它配置对象构造就失败，进程起不来（见 ``main.py`` lifespan 的读取点）。
            静默降级成一个「不记账」的进程，正是这条配置要避免的结果。
        echo: 是否把 SQL 输出到日志；仅本地排查时开启。
        connect_timeout: 建立 TCP 连接的等待秒数。
        pool_size: 连接池长期保留的连接数。两个使用者是写入的消费者任务与读用量的 HTTP
            接口，所以它按并发查询数定。
        max_overflow: 连接池耗尽时允许临时增加的连接数。
    """

    database_url: PostgresDsn = Field(
        description="用量库的 SQLAlchemy 异步连接地址，来源于 LLMOPS_DATABASE_URL；必填。",
    )
    echo: bool = Field(
        default=False,
        description="是否把用量库 SQL 输出到日志，来源于 LLMOPS_ECHO；仅建议本地排查时开启。",
    )
    connect_timeout: int = Field(
        default=5,
        ge=1,
        description="建立用量库 TCP 连接时允许等待的秒数，来源于 LLMOPS_CONNECT_TIMEOUT。",
    )
    pool_size: int = Field(
        default=5,
        ge=1,
        description="用量库连接池长期保留的连接数量，来源于 LLMOPS_POOL_SIZE。",
    )
    max_overflow: int = Field(
        default=0,
        ge=0,
        description="用量库连接池耗尽时允许临时增加的最大连接数量，来源于 LLMOPS_MAX_OVERFLOW。",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LLMOPS_",
        extra="ignore",
    )


@lru_cache
def get_usage_database_settings() -> UsageDatabaseSettings:
    """读取并缓存用量库配置（进程内只解析一次）。

    Returns:
        进程内复用的、已完成环境变量解析和约束校验的用量库配置。

    Raises:
        pydantic.ValidationError: ``LLMOPS_DATABASE_URL`` 缺失或不是合法的 PostgreSQL 地址。

    Notes:
        只读配置，不构造 Engine、不建立任何连接。调用方是 API 进程的 lifespan，它需要这个
        异常在「进程起不来」的那一层抛出。
    """

    return UsageDatabaseSettings()  # type: ignore[call-arg]


__all__ = ["UsageDatabaseSettings", "get_usage_database_settings"]
