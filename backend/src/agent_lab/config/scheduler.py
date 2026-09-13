"""统一 cron 解释时区；API 不提供进程内调度开关。"""

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SchedulerSettings(BaseSettings):
    timezone: str = "Asia/Shanghai"
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_prefix="SCHEDULER_", extra="ignore")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value):
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("调度时区必须是有效的 IANA 时区。") from None
        return normalized


@lru_cache
def get_scheduler_settings():
    return SchedulerSettings()
