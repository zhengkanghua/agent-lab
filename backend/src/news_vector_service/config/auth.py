"""解析浏览器账号密码认证的 Cookie 与会话生命周期配置。

本模块只负责把 ``AUTH_*`` 环境变量转换成有类型的进程级设置；不查询用户、不校验
密码、不创建 Session，也不决定具体路由权限。认证运行时由 ``auth.dependencies``
组装，生产部署必须通过 HTTPS 使用 Secure Cookie。
"""

from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """浏览器 Cookie 和数据库登录 Token 的运行时约束。"""

    cookie_name: str = Field(
        default="news_auth",
        pattern=r"^[A-Za-z0-9_-]+$",
        description="登录 Cookie 名称，来源于 AUTH_COOKIE_NAME，不包含空格或分隔符。",
    )
    cookie_secure: bool = Field(
        default=True,
        description=(
            "是否只通过 HTTPS 发送登录 Cookie，来源于 AUTH_COOKIE_SECURE；生产必须为 true，"
            "只有本地 HTTP 开发可以临时设为 false。"
        ),
    )
    cookie_samesite: Literal["lax", "strict"] = Field(
        default="strict",
        description=(
            "浏览器 SameSite 策略，来源于 AUTH_COOKIE_SAMESITE；当前同域账号密码登录默认"
            "使用 strict，避免跨站请求携带登录 Cookie。"
        ),
    )
    session_lifetime_seconds: int = Field(
        default=8 * 60 * 60,
        ge=300,
        le=30 * 24 * 60 * 60,
        description=(
            "数据库登录 Token 与 Cookie 的共同有效期秒数，来源于 "
            "AUTH_SESSION_LIFETIME_SECONDS；默认 8 小时。"
        ),
    )
    admin_email: EmailStr | None = Field(
        default=None,
        description=(
            "环境托管保底超级管理员邮箱，来源于 AUTH_ADMIN_EMAIL；与密码必须同时配置。"
        ),
    )
    admin_password: SecretStr | None = Field(
        default=None,
        description=(
            "环境托管保底超级管理员明文密码，来源于 AUTH_ADMIN_PASSWORD；仅用于启动时"
            "同步 Argon2 Hash，SecretStr 禁止在 repr 和日志中显示明文。"
        ),
    )

    @model_validator(mode="after")
    def validate_environment_admin(self) -> "AuthSettings":
        """要求保底管理员邮箱/密码成对出现并复用账号密码强度边界。

        Returns:
            配置完整且密码满足最小长度、最大长度和非邮箱同值约束的当前实例。

        Raises:
            ValueError: 仅配置邮箱或密码、密码为空/越界，或与邮箱完全相同时抛出。
        """

        password = (
            self.admin_password.get_secret_value()
            if self.admin_password is not None
            else None
        )
        email_configured = self.admin_email is not None
        password_configured = password is not None
        if email_configured != password_configured:
            raise ValueError(
                "AUTH_ADMIN_EMAIL 与 AUTH_ADMIN_PASSWORD 必须同时配置。"
            )
        if not email_configured:
            return self

        assert password is not None
        if not 12 <= len(password) <= 128:
            raise ValueError("AUTH_ADMIN_PASSWORD 必须包含 12 到 128 个字符。")
        if password.casefold() == str(self.admin_email).casefold():
            raise ValueError("AUTH_ADMIN_PASSWORD 不能与 AUTH_ADMIN_EMAIL 相同。")
        return self

    @property
    def environment_admin_configured(self) -> bool:
        """是否同时提供了可同步的保底管理员邮箱和密码。"""

        return self.admin_email is not None and self.admin_password is not None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AUTH_",
        extra="ignore",
    )


@lru_cache
def get_auth_settings() -> AuthSettings:
    """读取并缓存认证配置，不执行数据库或网络 I/O。

    Returns:
        已校验 Cookie 安全属性和会话有效期的进程级配置。
    """

    return AuthSettings()
