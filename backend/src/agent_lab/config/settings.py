"""应用配置定义。

本模块使用 Pydantic Settings 将环境变量转换成有类型、可校验的 Python 对象。
业务代码应依赖 ``Settings``，而不是在各处直接调用 ``os.getenv``。
"""

from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Agent Lab 的运行时配置。

    字段名会自动映射到同名的大写环境变量。例如 ``database_url`` 对应
    ``DATABASE_URL``。Pydantic 会负责类型转换和启动时校验。
    """

    database_url: PostgresDsn = Field(
        description="SQLAlchemy 使用的 PostgreSQL 连接地址。",
    )
    database_echo: bool = Field(
        default=False,
        description="是否把 SQL 语句输出到日志；仅建议在本地排查时开启。",
    )
    database_connect_timeout: int = Field(
        default=5,
        ge=1,
        description="建立 PostgreSQL TCP 连接时允许等待的秒数。",
    )
    database_health_check_timeout: float = Field(
        default=5.0,
        gt=0,
        description="健康检查等待 PostgreSQL 响应的总秒数。",
    )
    database_timezone: str = Field(
        default="UTC",
        pattern=r"^[A-Za-z0-9_+\-/]+$",
        description="数据库会话时区；项目统一使用 UTC。",
    )
    database_pool_size: int = Field(
        default=5,
        ge=1,
        description="连接池长期保留的连接数量。",
    )
    database_max_overflow: int = Field(
        default=10,
        ge=0,
        description="连接池耗尽时允许临时增加的最大连接数量。",
    )

    # .env 只用于本地开发；部署环境可以直接注入同名环境变量。
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """创建并缓存进程级配置对象。

    ``Settings()`` 会从外部环境读取必填字段，因此静态类型检查器无法在调用
    位置看到 ``database_url``。这里的忽略只处理这一项静态误报，不会跳过
    Pydantic 的运行时校验。

    Returns:
        进程内复用的、已完成环境变量解析和校验的应用配置。
    """

    return Settings()  # type: ignore[call-arg]
