"""组装 FastAPI Users 的 SQLAlchemy 适配器、Cookie Transport 与权限依赖。

本模块是认证子系统的装配入口：每个请求复用应用 SQLAlchemy Engine，但获得独立
AsyncSession；浏览器只持有 HttpOnly Cookie，随机 Token 保存在 PostgreSQL 并可在
退出时删除。它不挂载注册、重置密码或邮箱验证路由。
"""

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
)
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import (
    SQLAlchemyAccessTokenDatabase,
)
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.auth.manager import UserManager
from agent_lab.config.auth import get_auth_settings
from agent_lab.db.session import get_db_session
from agent_lab.models.user import AccessTokenRecord, UserRecord


async def get_user_database(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncIterator[SQLAlchemyUserDatabase[UserRecord, UUID]]:
    """为一次请求提供用户表适配器，不主动提交认证之外的业务事务。"""

    yield SQLAlchemyUserDatabase(session, UserRecord)


async def get_access_token_database(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncIterator[SQLAlchemyAccessTokenDatabase[AccessTokenRecord]]:
    """为一次请求提供数据库登录 Token 适配器。"""

    yield SQLAlchemyAccessTokenDatabase(session, AccessTokenRecord)


async def get_user_manager(
    user_database: SQLAlchemyUserDatabase[UserRecord, UUID] = Depends(
        get_user_database
    ),
) -> AsyncIterator[UserManager]:
    """为登录或用户解析请求提供内部账号管理器。"""

    yield UserManager(user_database)


_auth_settings = get_auth_settings()

cookie_transport = CookieTransport(
    cookie_name=_auth_settings.cookie_name,
    cookie_max_age=_auth_settings.session_lifetime_seconds,
    cookie_path="/",
    cookie_domain=None,
    cookie_secure=_auth_settings.cookie_secure,
    cookie_httponly=True,
    cookie_samesite=_auth_settings.cookie_samesite,
)


def get_database_strategy(
    token_database: SQLAlchemyAccessTokenDatabase[AccessTokenRecord] = Depends(
        get_access_token_database
    ),
) -> DatabaseStrategy[UserRecord, UUID, AccessTokenRecord]:
    """创建绑定当前请求数据库 Session 的可撤销 Token 策略。"""

    return DatabaseStrategy(
        token_database,
        lifetime_seconds=_auth_settings.session_lifetime_seconds,
    )


cookie_auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_database_strategy,
)

fastapi_users = FastAPIUsers[UserRecord, UUID](
    get_user_manager,
    [cookie_auth_backend],
)

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
