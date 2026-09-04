"""定时任务调度器的进程级配置。

本模块只把 ``SCHEDULER_*`` 环境变量解析为有类型、可校验的设置，不启动调度器、
不访问数据库。调度器的启停决策发生在应用 lifespan；cron 表达式的解释时区由
``timezone`` 决定——数据库存储一律 UTC，这里只是「cron 字符串 → 具体时刻」的
翻译规则（见 docs/adr/0014-in-process-apscheduler-with-db-as-source-of-truth.md）。
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SchedulerSettings(BaseSettings):
    """定时任务调度器的运行配置。

    ``enabled`` 默认 False：开发、测试环境默认不带自动调度。生产容器部署下
    该值由 docker-compose 覆盖（backend 容器强制 false，调度器容器强制 true），
    见 docs/adr/0017-scheduler-runs-in-a-dedicated-process.md；它只对非容器的
    裸进程部署有意义。
    """

    enabled: bool = Field(
        default=False,
        description=(
            "是否在应用启动时开启 cron 自动调度，来源于 SCHEDULER_ENABLED；"
            "关闭时管理 API 仍可用（可手动触发），只是不到点自动执行。"
        ),
    )
    timezone: str = Field(
        default="Asia/Shanghai",
        min_length=1,
        description=(
            "cron 表达式的解释时区（IANA 名称），来源于 SCHEDULER_TIMEZONE；"
            "只影响「0 9 * * *」这类字符串翻译成哪个时刻，数据库存储仍是 UTC。"
        ),
    )
    misfire_grace_seconds: int = Field(
        default=600,
        ge=1,
        description=(
            "错过的 cron 触发在多少秒内允许补跑，来源于 SCHEDULER_MISFIRE_GRACE_SECONDS；"
            "超过宽限就放弃这一次，等下一轮。"
        ),
    )
    run_history_retention: int = Field(
        default=50,
        ge=1,
        description=(
            "每个定时任务保留的最近任务执行记录条数，来源于 SCHEDULER_RUN_HISTORY_RETENTION；"
            "每次执行收尾时裁掉更早的记录。"
        ),
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """校验时区是可解析的 IANA 名称，拒绝拼错的字符串。

        Args:
            value: 环境变量中的原始时区名。

        Returns:
            去除首尾空白后的时区名。

        Raises:
            ValueError: 名称不能被 zoneinfo 识别（如拼错的 Asia/Shanghai）。
        """

        from zoneinfo import ZoneInfo

        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except Exception as exc:
            raise ValueError(
                f"SCHEDULER_TIMEZONE 不是可识别的 IANA 时区名称：{normalized!r}"
            ) from exc
        return normalized

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SCHEDULER_",
        extra="ignore",
    )


@lru_cache
def get_scheduler_settings() -> SchedulerSettings:
    """读取并缓存调度器配置，不执行任何 I/O。

    Returns:
        已完成时区校验的进程级调度器配置。
    """

    return SchedulerSettings()


__all__ = ["SchedulerSettings", "get_scheduler_settings"]
