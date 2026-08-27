"""在应用启动时把环境 Secret 幂等同步为唯一保底超级管理员。

本模块只访问 PostgreSQL 的 users/access_tokens：不挂载 HTTP 路由，不访问 FreshRSS、
Ollama 或 Qdrant。环境密码只进入 pwdlib 校验/Hash，不写日志、异常或返回对象。
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from news_vector_service.auth.manager import validate_password_strength
from news_vector_service.config.auth import AuthSettings, get_auth_settings
from news_vector_service.db.session import async_session_factory
from news_vector_service.models.user import AccessTokenRecord, UserRecord


@dataclass(frozen=True, slots=True)
class EnvironmentAdminSyncResult:
    """不含邮箱、密码或 Token 的启动同步安全摘要。"""

    configured: bool
    created: bool
    password_changed: bool
    released_previous_managers: int
    user_id: UUID | None


async def sync_configured_environment_admin() -> EnvironmentAdminSyncResult:
    """使用默认配置与 Session factory 同步环境保底管理员。

    Returns:
        只包含布尔状态、释放数量和用户 UUID 的脱敏结果。

    Raises:
        IntegrityError: 两次并发冲突重试后数据库仍无法满足唯一约束。
        SQLAlchemyError: PostgreSQL 不可用或认证表尚未迁移。

    Notes:
        最多执行两次 PostgreSQL 事务；不访问新闻、Embedding、Qdrant 或远程网络。
    """

    return await synchronize_environment_admin(
        get_auth_settings(),
        async_session_factory,
    )


async def synchronize_environment_admin(
    settings: AuthSettings,
    session_factory: async_sessionmaker[AsyncSession],
) -> EnvironmentAdminSyncResult:
    """同步、切换或解除唯一环境管理员标记，并处理并发启动竞争。

    Args:
        settings: 已校验且用 SecretStr 包装密码的认证配置。
        session_factory: 应用进程级异步 Session factory。

    Returns:
        不含环境邮箱、明文密码、Hash 或 Token 的同步结果。

    Raises:
        IntegrityError: 两次尝试都遇到并发唯一约束冲突。
        SQLAlchemyError: 其他 PostgreSQL 查询、写入或提交失败。
        InvalidPasswordException: 调用方绕过 AuthSettings 构造了不合规密码。
    """

    last_integrity_error: IntegrityError | None = None
    for _attempt in range(2):
        async with session_factory() as session:
            try:
                return await _synchronize_once(session, settings)
            except IntegrityError as error:
                last_integrity_error = error
                await session.rollback()

    assert last_integrity_error is not None
    raise last_integrity_error


async def _synchronize_once(
    session: AsyncSession,
    settings: AuthSettings,
) -> EnvironmentAdminSyncResult:
    """在一个事务中完成一次环境管理员同步尝试。"""

    managed_users = list(
        await session.scalars(
            select(UserRecord)
            .where(UserRecord.is_environment_admin.is_(True))
            .with_for_update()
        )
    )

    if not settings.environment_admin_configured:
        for user in managed_users:
            user.is_environment_admin = False
        await session.commit()
        return EnvironmentAdminSyncResult(
            configured=False,
            created=False,
            password_changed=False,
            released_previous_managers=len(managed_users),
            user_id=None,
        )

    assert settings.admin_email is not None
    assert settings.admin_password is not None
    email = str(settings.admin_email)
    password = settings.admin_password.get_secret_value()
    validate_password_strength(password, email)

    target = await session.scalar(
        select(UserRecord)
        .where(func.lower(UserRecord.email) == email.casefold())
        .with_for_update()
    )
    helper = PasswordHelper()
    created = target is None
    password_changed = False

    if target is None:
        target = UserRecord(
            email=email,
            hashed_password=helper.hash(password),
            is_active=True,
            is_superuser=True,
            is_verified=True,
            is_environment_admin=False,
        )
        session.add(target)
        await session.flush()
        password_changed = True
    else:
        password_matches, upgraded_hash = helper.verify_and_update(
            password,
            target.hashed_password,
        )
        if not password_matches:
            target.hashed_password = helper.hash(password)
            password_changed = True
        elif upgraded_hash is not None:
            target.hashed_password = upgraded_hash

    released = 0
    for previous in managed_users:
        if previous.id != target.id:
            previous.is_environment_admin = False
            released += 1
    if released:
        await session.flush()

    target.email = email
    target.is_active = True
    target.is_superuser = True
    target.is_verified = True
    target.is_environment_admin = True
    if password_changed and not created:
        await session.execute(
            delete(AccessTokenRecord).where(AccessTokenRecord.user_id == target.id)
        )

    await session.commit()
    await session.refresh(target)
    return EnvironmentAdminSyncResult(
        configured=True,
        created=created,
        password_changed=password_changed,
        released_previous_managers=released,
        user_id=target.id,
    )
