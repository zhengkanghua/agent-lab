"""暴露账号密码登录、退出和当前用户读取接口。

登录与退出由 FastAPI Users Cookie backend 提供；本模块只增加安全的 ``GET /auth/me``
身份视图。公开注册、密码重置、邮箱验证和用户枚举路由均不挂载。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from agent_lab.auth.dependencies import (
    cookie_auth_backend,
    current_active_user,
    fastapi_users,
)
from agent_lab.models.user import UserRecord
from agent_lab.schemas.auth import AuthUserResponse


router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(fastapi_users.get_auth_router(cookie_auth_backend))


@router.get(
    "/me",
    response_model=AuthUserResponse,
    summary="读取当前登录账号",
    description="根据 HttpOnly 登录 Cookie 返回当前启用账号的最小身份和权限字段。",
)
async def get_current_auth_user(
    user: Annotated[UserRecord, Depends(current_active_user)],
) -> UserRecord:
    """返回当前已认证用户，由响应模型排除密码 Hash 等内部列。

    Args:
        user: FastAPI Users 从数据库 Token 解析并确认启用的账号。

    Returns:
        当前 ORM 用户；FastAPI 只按 ``AuthUserResponse`` 序列化安全字段。

    Notes:
        认证依赖会执行一次 PostgreSQL Token/用户查询；本函数本身不额外访问数据库，
        不读取 Cookie 明文，也不执行新闻、Embedding 或 Qdrant I/O。
    """

    return user
