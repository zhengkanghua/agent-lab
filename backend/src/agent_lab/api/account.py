"""提供当前登录账号对自己的自助 HTTP API：改密码与读写个人偏好。

本层和 ``api/user_admin.py`` 的关键差别是权限与操作对象：那一族要求超级用户、按 id 改别人；
这一族只要求「已登录且启用」，操作对象恒为调用者自己，路径和请求体里都没有账号 id。

改密码的请求体含两个明文密码，所以那条路由挂 ``SanitizedValidationRoute``，把校验失败换成固定
``invalid_request``，绝不回显原始输入；它成功返回 204——改密不需要回传任何账号字段，前端要刷新
身份的话读 ``GET /auth/me`` 就够了。偏好那两条不含密码，读写都需要往返，因此返回完整对象。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.api.error_contract import (
    SanitizedValidationRoute,
    build_error_response,
    build_user_admin_error_response,
)
from agent_lab.auth.dependencies import current_active_user, current_active_user_token
from agent_lab.db.session import get_db_session
from agent_lab.models.user import UserRecord
from agent_lab.schemas.account import (
    AccountErrorResponse,
    AccountPasswordChangeRequest,
)
from agent_lab.schemas.user_preference import (
    UserPreferenceResponse,
    UserPreferenceUpdateRequest,
)
from agent_lab.services.account_service import AccountDomainError, AccountService
from agent_lab.services.user_preference_service import UserPreferenceService


router = APIRouter(
    prefix="/me",
    tags=["auth"],
    route_class=SanitizedValidationRoute,
)


def get_account_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AccountService:
    """用当前请求数据库 Session 构造账号自助 Service。"""

    # 与 ``get_user_admin_service`` 同形：Service 无状态，贵的是 Session，而 Session 本来就
    # 按请求走 ``get_db_session``，Service 跟着它的生命周期，事务边界才不会跨请求串起来。
    return AccountService(session)


# ``response_model=None`` 是必需的，不是冗余：返回标注是 ``Response | JSONResponse`` 这个联合
# 类型，FastAPI 只在标注**本身**是 Response 子类时才判定「没有响应模型」，联合类型过不了那一关，
# 于是它会把整个联合当成待序列化模型，再撞上「204 不允许有响应体」的断言直接启动失败。
# 成功分支是 204 空响应，错误分支各自 ``responses`` 里已声明，本来就不需要模型。
@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses={
        409: {"model": AccountErrorResponse},
        422: {"model": AccountErrorResponse},
        503: {"model": AccountErrorResponse},
    },
    summary="修改当前账号密码",
    description=(
        "校验当前密码后替换为新密码，并撤销该账号在其他设备上的登录；本次请求使用的登录"
        "Cookie 保持有效。"
    ),
)
async def change_own_password(
    body: AccountPasswordChangeRequest,
    identity: Annotated[tuple[UserRecord, str], Depends(current_active_user_token)],
    service: Annotated[AccountService, Depends(get_account_service)],
) -> Response | JSONResponse:
    """改当前账号自己的密码，成功时返回 204。

    依赖用的是 ``current_active_user_token`` 而不是 ``current_active_user``：要保留当前这一个
    会话、只踢其他设备，就得知道本次请求用的是哪个 Token，而那只有认证层知道。
    """

    user, token = identity
    try:
        await service.change_own_password(user, token, body)
    except AccountDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError as error:
        return _database_error(error)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def get_user_preference_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserPreferenceService:
    """用当前请求数据库 Session 构造个人偏好 Service。"""

    return UserPreferenceService(session)


@router.get(
    "/preferences",
    response_model=UserPreferenceResponse,
    status_code=status.HTTP_200_OK,
    responses={503: {"model": AccountErrorResponse}},
    summary="读取当前账号的个人偏好",
    description=(
        "返回当前登录账号的个人偏好。从未配置过的账号拿到的是契约默认值，"
        "而不是 404——「还没配过」是一个正常状态，不是错误。"
    ),
)
async def read_own_preferences(
    user: Annotated[UserRecord, Depends(current_active_user)],
    preferences: Annotated[UserPreferenceService, Depends(get_user_preference_service)],
) -> UserPreferenceResponse | JSONResponse:
    """读取当前登录账号的个人偏好。

    权限门是 ``current_active_user``（普通登录账号即可），不是 ``current_superuser``：
    Agent 对话已对所有登录账号开放，提示词不再是超级用户专有。

    路径里不带账号 id——取值域由登录态决定，从形态上排除「读别人配置」的可能。

    Args:
        user: 当前登录账号。
        preferences: 个人偏好 Service。

    Returns:
        该账号的偏好；未配置时各项取契约默认值；数据库故障时稳定的 503 JSON。

    Notes:
        一次 PostgreSQL 只读查询。
    """

    try:
        return await preferences.get(user.id)
    except SQLAlchemyError as error:
        return _database_error(error)


@router.put(
    "/preferences",
    response_model=UserPreferenceResponse,
    status_code=status.HTTP_200_OK,
    responses={
        422: {"model": AccountErrorResponse},
        503: {"model": AccountErrorResponse},
    },
    summary="保存当前账号的个人偏好",
    description=(
        "整体覆盖当前登录账号的个人偏好，不存在则新建。设置页是「草稿 + 显式保存」的形态，"
        "提交的就是完整一份，所以这里不做部分更新。\n\n"
        "系统提示词作为**新会话**的初始提示词，已经开始的会话不受影响。"
    ),
)
async def replace_own_preferences(
    body: UserPreferenceUpdateRequest,
    user: Annotated[UserRecord, Depends(current_active_user)],
    preferences: Annotated[UserPreferenceService, Depends(get_user_preference_service)],
) -> UserPreferenceResponse | JSONResponse:
    """整体覆盖当前登录账号的个人偏好。

    Args:
        body: 完整的一份偏好，已在 schema 层过边界校验与空白归一化。
        user: 当前登录账号。
        preferences: 个人偏好 Service。

    Returns:
        落库后重新读出的偏好；数据库故障时稳定的 503 JSON。

    Notes:
        一次 PostgreSQL 写入事务。提示词长度上限由 ``UserPreferenceUpdateRequest`` 上的
        ``max_length`` 把关——那道约束原先挂在请求体的 ``system_prompt`` 字段上，随本次改动
        删除了，不在这里补回来服务端就没有真边界。
    """

    try:
        return await preferences.replace(user.id, body)
    except SQLAlchemyError as error:
        return _database_error(error)


def _domain_error(error: AccountDomainError) -> JSONResponse:
    """把账号自助领域错误的稳定 code 映射成 HTTP 状态码并复用统一响应结构。

    只读领域层刻意提供的安全字段（``code`` 与预写中文 ``detail``），不读 ``str(error)``，
    所以密码、Hash 和数据库文本都不会进响应。

    ``current_password_invalid`` 刻意给 422 而不是 401，这一条是硬约束不是口味：前端
    ``api/client.ts`` 见到 401 会触发全局「登录已失效」处理并把人踢回登录页，而这里的语义是
    「你还在登录，只是这一栏填错了」。用 401 的话，输错一次旧密码就会把人登出，表单上的新密码
    也一起丢掉。

    Args:
        error: Service 抛出的预期自助操作失败。

    Returns:
        与其他链路同构的 ``code/detail/retryable`` JSON 响应；未知 code 归为 409。
    """

    status_code = {
        "current_password_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "invalid_password": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "environment_admin_protected": status.HTTP_409_CONFLICT,
    }.get(error.code, status.HTTP_409_CONFLICT)
    return build_error_response(
        status_code,
        error.code,
        error.detail,
        retryable=False,
    )


def _database_error(error: SQLAlchemyError) -> JSONResponse:
    """把数据库故障交给共享错误表映射成稳定 503（只读异常类型）。

    复用账号管理那张表而不是新开一张：故障是同一个（认证库不可用），对外 code 也该是同一个。
    再开一张表只会让「同一种故障两个 code」这件事出现在契约里。

    Args:
        error: 请求期间捕获的 SQLAlchemy 异常。

    Returns:
        含 ``user_admin_database_unavailable`` 的 503 JSON 响应。
    """

    return build_user_admin_error_response(error)
