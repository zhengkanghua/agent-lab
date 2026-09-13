"""项目共用 Redis 的连接配置；凭据只在服务端使用。"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedisSettings(BaseSettings):
    """各 Redis 使用方共用连接，键命名及读写策略由使用方管理。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_prefix="REDIS_", extra="ignore")
    url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    password: SecretStr = SecretStr("")


@lru_cache
def get_redis_settings() -> RedisSettings:
    return RedisSettings()
