"""定义本地账号创建输入与当前登录用户的公开 HTTP 契约。

创建模型只供可信 CLI 调用，不挂载公开注册路由；响应模型只返回身份和授权所需字段，
绝不包含密码 Hash、Cookie 或数据库 Token。
"""

from datetime import datetime
from uuid import UUID

from fastapi_users import schemas
from pydantic import EmailStr, Field


class AuthUserCreate(schemas.BaseUserCreate):
    """管理员通过本地 CLI 创建账号时使用的受约束输入。"""

    email: EmailStr = Field(
        description="内部账号的登录邮箱；规范化后按大小写不敏感方式保持唯一。",
    )
    password: str = Field(
        min_length=12,
        max_length=128,
        description="仅在创建调用内短暂存在的明文密码；不会写入日志或数据库。",
    )


class AuthUserResponse(schemas.BaseUser[UUID]):
    """返回给已登录浏览器的最小用户身份和权限视图。"""

    id: UUID = Field(description="当前登录用户的稳定 UUID。")
    email: EmailStr = Field(description="当前登录账号的邮箱标识。")
    is_active: bool = Field(description="账号当前是否启用；该接口只返回启用账号。")
    is_superuser: bool = Field(description="账号是否有权执行手动 Pipeline。")
    is_verified: bool = Field(description="账号是否已由管理员确认。")
    is_environment_admin: bool = Field(
        description="账号是否由服务端 AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD 托管。",
    )
    # 自助账号页要显示「这个号是什么时候建的」，好让人确认自己登的是不是以为的那个账号。
    # 只加 created_at，不加 updated_at：后者会随任何一次改密或状态变更跳动，对账号主人没有
    # 可解释的含义，反而像是「有人动过我的号」。要看变更历史是审计的活，不是这个视图的。
    created_at: datetime = Field(description="账号在 PostgreSQL 中首次写入的时间。")
