"""提供仅超级用户可访问的内部账号管理 HTTP API。

本层校验 OpenAPI 输入、调用 UserAdminService，并把预期领域错误或数据库故障转换成稳定
脱敏响应；不开放注册，不返回密码 Hash、环境 Secret 或 access token。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.db.session import get_db_session
from agent_lab.schemas.user_admin import (
    UserAdminCreateRequest,
    UserAdminErrorResponse,
    UserAdminPasswordRequest,
    UserAdminResponse,
    UserAdminUpdateRequest,
    UserSessionRevocationResponse,
)
from agent_lab.services.user_admin_service import (
    UserAdminDomainError,
    UserAdminService,
)


router = APIRouter(prefix="/admin/users", tags=["user-admin"])


def get_user_admin_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserAdminService:
    """用当前请求数据库 Session 构造账号管理 Service。"""

    return UserAdminService(session)


@router.get(
    "",
    response_model=list[UserAdminResponse],
    responses={503: {"model": UserAdminErrorResponse}},
    summary="列出内部账号",
)
async def list_users(
    service: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> list[UserAdminResponse] | JSONResponse:
    """返回不含密码和 Token 的账号列表。"""

    try:
        users = await service.list_users()
    except SQLAlchemyError:
        return _database_error()
    return [UserAdminResponse.model_validate(user) for user in users]


@router.post(
    "",
    response_model=UserAdminResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": UserAdminErrorResponse},
        422: {"model": UserAdminErrorResponse},
        503: {"model": UserAdminErrorResponse},
    },
    summary="创建内部账号",
)
async def create_user(
    body: UserAdminCreateRequest,
    service: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> UserAdminResponse | JSONResponse:
    """创建已确认账号；公开调用方无法访问本路由。"""

    try:
        user = await service.create_user(body)
    except UserAdminDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError:
        return _database_error()
    return UserAdminResponse.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=UserAdminResponse,
    responses={
        404: {"model": UserAdminErrorResponse},
        409: {"model": UserAdminErrorResponse},
        503: {"model": UserAdminErrorResponse},
    },
    summary="修改账号权限",
)
async def update_user(
    user_id: UUID,
    body: UserAdminUpdateRequest,
    service: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> UserAdminResponse | JSONResponse:
    """修改启用/超级用户状态，受环境管理员和最后超级用户保护。"""

    try:
        user = await service.update_user(user_id, body)
    except UserAdminDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError:
        return _database_error()
    return UserAdminResponse.model_validate(user)


@router.post(
    "/{user_id}/password",
    response_model=UserAdminResponse,
    responses={
        404: {"model": UserAdminErrorResponse},
        409: {"model": UserAdminErrorResponse},
        422: {"model": UserAdminErrorResponse},
        503: {"model": UserAdminErrorResponse},
    },
    summary="重置账号密码",
)
async def reset_user_password(
    user_id: UUID,
    body: UserAdminPasswordRequest,
    service: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> UserAdminResponse | JSONResponse:
    """重置非环境账号密码，并同时撤销其全部会话。"""

    try:
        user = await service.reset_password(user_id, body)
    except UserAdminDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError:
        return _database_error()
    return UserAdminResponse.model_validate(user)


@router.delete(
    "/{user_id}/sessions",
    response_model=UserSessionRevocationResponse,
    responses={
        404: {"model": UserAdminErrorResponse},
        503: {"model": UserAdminErrorResponse},
    },
    summary="撤销账号全部会话",
)
async def revoke_user_sessions(
    user_id: UUID,
    service: Annotated[UserAdminService, Depends(get_user_admin_service)],
) -> UserSessionRevocationResponse | JSONResponse:
    """删除目标账号的全部数据库登录 Token。"""

    try:
        count = await service.revoke_sessions(user_id)
    except UserAdminDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError:
        return _database_error()
    return UserSessionRevocationResponse(revoked_sessions=count)


def _domain_error(error: UserAdminDomainError) -> JSONResponse:
    status_code = {
        "user_not_found": status.HTTP_404_NOT_FOUND,
        "invalid_password": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "user_already_exists": status.HTTP_409_CONFLICT,
        "environment_admin_protected": status.HTTP_409_CONFLICT,
        "last_superuser_protected": status.HTTP_409_CONFLICT,
    }.get(error.code, status.HTTP_409_CONFLICT)
    return JSONResponse(
        status_code=status_code,
        content={"code": error.code, "detail": error.detail, "retryable": False},
    )


def _database_error() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "code": "user_admin_database_unavailable",
            "detail": "Account management is temporarily unavailable.",
            "retryable": True,
        },
    )
