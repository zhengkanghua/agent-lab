"""任务队列的执行参数；共享 Redis 连接由 config.redis 提供。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TaskQueueSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_prefix="TASK_QUEUE_", extra="ignore")
    name: str = Field(default="agent-lab", min_length=1)
    visibility_timeout: int = Field(default=3600, ge=1)
    publish_timeout_seconds: float = Field(default=2, gt=0)
    redelivery_seconds: float = Field(default=5, ge=1)
    beat_poll_seconds: float = Field(default=0.5, ge=0.1, le=1)
    maintenance_seconds: float = Field(default=5, ge=1)


@lru_cache
def get_task_queue_settings():
    return TaskQueueSettings()
