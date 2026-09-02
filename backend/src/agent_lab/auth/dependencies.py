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
    """为一次请求提供用户表适配器，不主动提交认证之外的业务事务。

    用 ``yield`` 而不是 ``return``：FastAPI 对生成器依赖会在响应发完之后才继续执行
    yield 之后的部分，这样 ``get_db_session`` 的 Session 在整个请求期间都活着。改成
    ``return`` 的话依赖链一返回 Session 就可能被回收，认证查询会拿到已关闭的 Session。
    """

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


# 下面这些是进程级的：Cookie 参数在进程启动时定死，不随请求变。所以在模块级读一次配置
# 就够了，不用做成依赖。
#
# 三个 Cookie 参数是安全边界，不是风格选择：
# httponly=True —— JavaScript 读不到这个 Cookie，XSS 偷不走会话。
# secure —— 生产必须开（只走 HTTPS）；本地 http 开发要关，所以做成配置项。
# samesite —— 挡跨站请求自动带上 Cookie，也就是 CSRF。
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
    """创建绑定当前请求数据库 Session 的可撤销 Token 策略。

    「可撤销」是选 DatabaseStrategy 而不是 JWT 的原因：Token 是一串随机值，本体存在
    PostgreSQL 里，删掉那行会话立刻失效。JWT 自带签名和过期时间、服务端不留记录，
    想让它提前失效就得再建一张黑名单表——那还不如一开始就把 Token 存库。
    账号管理里的「撤销全部会话」和降权时踢人下线，靠的都是这个。

    这个必须每请求新建（不像上面的 Cookie 参数）：它绑着本次请求的 Session。
    """

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

# 全项目的两道门，路由用 ``Depends(current_active_user)`` 挂上即生效。
# 没通过的请求在进入 endpoint 之前就被拦掉，所以 endpoint 里拿到的 user 一定是有效的。
#
# 两个都带 active=True：被禁用的账号即使手上还有没过期的 Cookie 也进不来。
# 漏掉 active 的话，「禁用账号」就只能等 Token 自然过期才生效。
current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)

# 第三道门，只给「改自己密码」用：除了账号本身，还要拿到本次请求用的那个数据库 Token，
# 返回的是 ``(user, token)`` 二元组。有它才能做到「踢掉其他设备、保留当前这一个」——
# 要保留哪一个，只有认证层知道，请求体里读不到（读它等于让调用者指定自己是谁）。
#
# 必须在模块级只建一次，不能在路由里现调 ``current_user_token()``：每次调用返回的是一个
# 新的函数对象，而 ``app.dependency_overrides`` 是按对象身份查的，现建的那个谁也覆盖不了，
# 测试里就再也没法注入假身份。
#
# 走 ``.authenticator`` 而不是 ``fastapi_users.current_user_token``：``FastAPIUsers`` 只把
# ``current_user`` 转发到了外层，带 Token 的那个变体没转发，只在 Authenticator 上。
current_active_user_token = fastapi_users.authenticator.current_user_token(active=True)
