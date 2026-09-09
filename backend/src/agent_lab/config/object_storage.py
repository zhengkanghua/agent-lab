"""MinIO/S3 兼容对象存储的进程配置。"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ObjectStorageSettings(BaseSettings):
    endpoint: str | None = Field(default=None, description="MinIO/S3 endpoint，例如 http://minio:9000。")
    bucket: str = Field(default="agent-lab-documents", min_length=1)
    region: str = Field(default="us-east-1", min_length=1)
    access_key: str | None = None
    secret_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    addressing_style: str = Field(default="path", pattern=r"^(path|virtual)$")
    required: bool = Field(default=True, description="启用文档原件持久化时是否要求 endpoint 已配置。")
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_prefix="S3_", extra="ignore")


@lru_cache
def get_object_storage_settings() -> ObjectStorageSettings:
    return ObjectStorageSettings()
