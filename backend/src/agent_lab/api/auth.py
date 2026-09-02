"""暴露账号密码登录、退出、当前用户读取和账号自助操作接口。

登录与退出由 FastAPI Users Cookie backend 提供；本模块只增加安全的 ``GET /auth/me``
身份视图，并挂上 ``/auth/me/*`` 的自助子路由。公开注册、密码重置、邮箱验证和用户枚举路由
均不挂载——「忘记密码」需要发信通道且允许未登录者触发，与本项目「封闭内部账号」的前提冲突；
自助改密要求先证明自己知道旧密码，是另一回事。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from agent_lab.api.account import router as account_router
from agent_lab.auth.dependencies import (
    cookie_auth_backend,
    current_active_user,
    fastapi_users,
)
from agent_lab.models.user import UserRecord
from agent_lab.schemas.auth import AuthUserResponse


router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(fastapi_users.get_auth_router(cookie_auth_backend))
# 自助子路由自带 ``route_class=SanitizedValidationRoute``（它的请求体有明文密码），而本
# 路由没有。include 不会改写子路由的 route class：那些 route 对象在 account.py 里就已按它
# 自己的 router 建好了。所以脱敏跟着自助路由走，不会被这里的默认行为吞掉。
router.include_router(account_router)


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
