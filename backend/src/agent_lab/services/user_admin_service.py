"""实现超级用户对内部账号、权限、密码和数据库会话的管理用例。

本 Service 只读写 PostgreSQL users/access_tokens，不负责 HTTP、Cookie 设置或公开注册。
环境管理员不可通过此层降级或改密；任何操作都不能移除最后一个启用的超级用户。
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi_users import exceptions
from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.auth.manager import validate_password_strength
from agent_lab.models.user import AccessTokenRecord, UserRecord
from agent_lab.schemas.user_admin import (
    UserAdminCreateRequest,
    UserAdminPasswordRequest,
    UserAdminUpdateRequest,
)


@dataclass(frozen=True, slots=True)
class UserAdminDomainError(Exception):
    """携带稳定安全代码的账号管理预期失败。"""

    code: str
    detail: str


class UserAdminService:
    """以一个请求级 AsyncSession 管理用户和登录 Token 的事务边界。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._password_helper = PasswordHelper()

    async def list_users(self) -> list[UserRecord]:
        """按环境管理员优先、邮箱升序返回全部内部账号。

        Returns:
            当前 users 表中的 ORM 用户列表，不包含密码 Hash 的额外加载。

        Notes:
            执行一次 PostgreSQL 只读查询，不访问 access_tokens 或外部服务。
        """

        return list(
            await self._session.scalars(
                select(UserRecord).order_by(
                    UserRecord.is_environment_admin.desc(),
                    UserRecord.email.asc(),
                )
            )
        )

    async def create_user(self, request: UserAdminCreateRequest) -> UserRecord:
        """创建一个已确认、默认启用且不受环境托管的内部账号。"""

        try:
            validate_password_strength(request.password, str(request.email))
        except exceptions.InvalidPasswordException as error:
            raise UserAdminDomainError(
                "invalid_password",
                str(error.reason),
            ) from error

        user = UserRecord(
            email=str(request.email),
            hashed_password=self._password_helper.hash(request.password),
            is_active=True,
            is_superuser=request.is_superuser,
            is_verified=True,
            is_environment_admin=False,
        )
        self._session.add(user)
        try:
            await self._session.commit()
        except IntegrityError as error:
            await self._session.rollback()
            raise UserAdminDomainError(
                "user_already_exists",
                "该邮箱的账号已存在。",
            ) from error
        await self._session.refresh(user)
        return user

    async def update_user(
        self,
        user_id: UUID,
        request: UserAdminUpdateRequest,
    ) -> UserRecord:
        """修改普通账号状态，并在降权时撤销全部现有会话。"""

        user = await self._get_user_for_update(user_id)
        await self._ensure_not_environment_managed(user)

        next_active = request.is_active if request.is_active is not None else user.is_active
        next_superuser = (
            request.is_superuser
            if request.is_superuser is not None
            else user.is_superuser
        )
        loses_active_superuser = user.is_active and user.is_superuser and not (
            next_active and next_superuser
        )
        if loses_active_superuser:
            active_superusers = list(
                await self._session.scalars(
                    select(UserRecord.id)
                    .where(
                        UserRecord.is_active.is_(True),
                        UserRecord.is_superuser.is_(True),
                    )
                    .with_for_update()
                )
            )
            if len(active_superusers) <= 1:
                await self._session.rollback()
                raise UserAdminDomainError(
                    "last_superuser_protected",
                    "最后一个活跃超级管理员不能被禁用或降级。",
                )

        user.is_active = next_active
        user.is_superuser = next_superuser
        if loses_active_superuser or not next_active:
            await self._delete_sessions(user.id)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def reset_password(
        self,
        user_id: UUID,
        request: UserAdminPasswordRequest,
    ) -> UserRecord:
        """重置普通账号密码，并撤销该账号全部现有登录 Token。"""

        user = await self._get_user_for_update(user_id)
        await self._ensure_not_environment_managed(user)
        try:
            validate_password_strength(request.password, user.email)
        except exceptions.InvalidPasswordException as error:
            await self._session.rollback()
            raise UserAdminDomainError(
                "invalid_password",
                str(error.reason),
            ) from error

        user.hashed_password = self._password_helper.hash(request.password)
        await self._delete_sessions(user.id)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def revoke_sessions(self, user_id: UUID) -> int:
        """撤销目标账号的全部数据库登录 Token，环境管理员也允许主动撤销。"""

        await self._get_user_for_update(user_id)
        result = await self._session.execute(
            delete(AccessTokenRecord).where(AccessTokenRecord.user_id == user_id)
        )
        await self._session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def _get_user_for_update(self, user_id: UUID) -> UserRecord:
        user = await self._session.scalar(
            select(UserRecord).where(UserRecord.id == user_id).with_for_update()
        )
        if user is None:
            await self._session.rollback()
            raise UserAdminDomainError("user_not_found", "账号不存在。")
        return user

    async def _ensure_not_environment_managed(self, user: UserRecord) -> None:
        if user.is_environment_admin:
            await self._session.rollback()
            raise UserAdminDomainError(
                "environment_admin_protected",
                "环境托管的管理员账号必须通过服务端密钥修改。",
            )

    async def _delete_sessions(self, user_id: UUID) -> None:
        await self._session.execute(
            delete(AccessTokenRecord).where(AccessTokenRecord.user_id == user_id)
        )
