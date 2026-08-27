"""FreshRSS API 的配置定义。

FreshRSS 配置单独成组，避免在数据库阶段就强制要求 FreshRSS 密钥存在。
只有真正创建 FreshRSS 客户端时，调用方才需要读取本配置。
"""

from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FreshRSSSettings(BaseSettings):
    """访问 FreshRSS Google Reader/Fever 兼容 API 所需的配置。"""

    provider_key: str = Field(
        default="freshrss_main",
        min_length=1,
        description="FreshRSS 实例稳定标识，用于区分未来可能接入的多个实例。",
    )
    api_base_url: AnyHttpUrl = Field(
        description="FreshRSS API 根地址，例如 https://host.example/api/。",
    )
    username: str = Field(
        description="FreshRSS 用户名。",
    )
    api_password: SecretStr = Field(
        description="FreshRSS 用户配置中的 API 密码，不是数据库密码。",
    )
    request_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        description="单次 FreshRSS HTTP 请求的总超时秒数。",
    )
    verify_ssl: bool = Field(
        default=True,
        description="是否校验 FreshRSS HTTPS 证书；生产环境应保持开启。",
    )
    sync_categories: tuple[str, ...] = Field(
        min_length=1,
        description=(
            "允许本服务同步的 FreshRSS 分类白名单。未出现在该列表中的分类，"
            "即使存在于 FreshRSS 阅读列表中，也不会被业务同步流程读取。"
        ),
    )

    @field_validator("sync_categories")
    @classmethod
    def validate_sync_categories(cls, categories: tuple[str, ...]) -> tuple[str, ...]:
        """清理分类名称，并拒绝空名称或重复配置。

        Pydantic Settings 会先把环境变量中的 JSON 数组解析成元组，再调用本方法。
        使用元组而不是列表，是为了表达应用启动后不应在运行时随意修改白名单。

        Args:
            categories: Pydantic 已从环境变量解析出的分类名称元组。

        Returns:
            去除名称两端空白且保持原配置顺序的分类元组。

        Raises:
            ValueError: 分类包含空名称或规范化后存在重复项时抛出。
        """

        normalized_categories = tuple(category.strip() for category in categories)
        if any(not category for category in normalized_categories):
            raise ValueError("FreshRSS 同步分类不能包含空名称")
        if len(set(normalized_categories)) != len(normalized_categories):
            raise ValueError("FreshRSS 同步分类不能重复")
        return normalized_categories

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="FRESHRSS_",
        extra="ignore",
    )


@lru_cache
def get_freshrss_settings() -> FreshRSSSettings:
    """读取并缓存 FreshRSS 配置。

    该函数暂时不会被应用入口调用，因此缺少 FreshRSS 配置不会影响当前阶段
    的数据库健康检查。

    Returns:
        进程内复用的、已完成环境变量解析和校验的 FreshRSS 配置。
    """

    return FreshRSSSettings()  # type: ignore[call-arg]
