"""定义超级用户账号管理 API 的输入、输出和稳定错误契约。

这些模型不提供公开注册，也不返回密码 Hash、数据库 Token 或环境密码。所有密码字段
只存在于受超级用户保护的创建/重置请求中，经过校验后立即 Hash。
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class UserAdminResponse(BaseModel):
    """账号管理列表和写操作返回的安全用户视图。"""

    id: UUID = Field(description="内部用户稳定 UUID。")
    email: EmailStr = Field(description="大小写不敏感的登录邮箱。")
    is_active: bool = Field(description="账号是否允许登录和继续使用已有会话。")
    is_superuser: bool = Field(description="账号是否有账号管理和 Pipeline 权限。")
    is_verified: bool = Field(description="账号是否已经由管理员确认。")
    is_environment_admin: bool = Field(
        description="是否由 AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD 托管且不可网页降级。",
    )
    created_at: datetime = Field(description="账号首次写入 PostgreSQL 的时间。")
    updated_at: datetime = Field(description="账号最近一次通过 ORM 更新的时间。")

    model_config = ConfigDict(from_attributes=True)


class UserAdminCreateRequest(BaseModel):
    """超级用户创建一个封闭内部账号的请求。"""

    email: EmailStr = Field(description="新账号登录邮箱。")
    password: str = Field(
        min_length=12,
        max_length=128,
        description="新账号初始密码；只用于当前请求的 Argon2 Hash。",
    )
    is_superuser: bool = Field(
        default=False,
        description="是否同时授予账号管理和 Pipeline 权限。",
    )

    model_config = ConfigDict(extra="forbid")


class UserAdminUpdateRequest(BaseModel):
    """修改普通数据库账号的启用状态或超级用户权限。"""

    is_active: bool | None = Field(default=None, description="新的账号启用状态。")
    is_superuser: bool | None = Field(default=None, description="新的超级用户权限状态。")

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_change(self) -> "UserAdminUpdateRequest":
        """拒绝没有任何实际字段的空更新。"""

        if self.is_active is None and self.is_superuser is None:
            raise ValueError("至少需要提供一个账号字段。")
        return self


class UserAdminPasswordRequest(BaseModel):
    """超级用户重置普通数据库账号密码的请求。"""

    password: str = Field(
        min_length=12,
        max_length=128,
        description="新密码；成功 Hash 后撤销目标账号全部现有登录 Token。",
    )

    model_config = ConfigDict(extra="forbid")


class UserSessionRevocationResponse(BaseModel):
    """批量撤销目标账号数据库登录 Token 的安全统计。"""

    revoked_sessions: int = Field(
        ge=0,
        description="本次从 access_tokens 删除的登录会话数量。",
    )


class UserAdminErrorResponse(BaseModel):
    """账号管理 API 的稳定、脱敏错误结构。"""

    code: str = Field(description="供前端稳定识别的错误代码。")
    detail: str = Field(description="不含密码、Hash、Token 或数据库异常文本的安全说明。")
    retryable: bool = Field(description="相同请求稍后重试是否可能成功。")
