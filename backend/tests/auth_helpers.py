"""为既有 HTTP 行为测试提供显式、无数据库 I/O 的认证依赖覆盖。"""

from uuid import UUID

from fastapi import FastAPI

from agent_lab.auth.dependencies import current_active_user, current_superuser
from agent_lab.auth.bootstrap import EnvironmentAdminSyncResult
from agent_lab.models.user import UserRecord


async def skip_environment_admin_sync() -> EnvironmentAdminSyncResult:
    """离线 HTTP 测试禁用真实启动同步，避免依赖开发者本地 ``.env``。"""

    return EnvironmentAdminSyncResult(
        configured=False,
        created=False,
        password_changed=False,
        released_previous_managers=0,
        user_id=None,
    )


# 两个角色各固定一个 id。**不能用 uuid4() 每次现生成**：这两个函数是按请求调用的依赖覆盖，
# 现生成意味着同一个测试里前后两个请求会拿到不同账号，于是任何跨请求的归属断言（第一个请求建会话、
# 第二个请求列出它）都会假失败，而且失败原因看起来像「归属校验坏了」。
SUPERUSER_ID = UUID("00000000-0000-4000-8000-000000000001")
READER_ID = UUID("00000000-0000-4000-8000-000000000002")


def _test_user(*, superuser: bool) -> UserRecord:
    """构造不持久化的启用测试用户。"""

    return UserRecord(
        id=SUPERUSER_ID if superuser else READER_ID,
        email="operator@example.com" if superuser else "reader@example.com",
        hashed_password="not-used-by-dependency-overrides",
        is_active=True,
        is_superuser=superuser,
        is_verified=True,
        is_environment_admin=False,
    )


async def authenticated_reader() -> UserRecord:
    """返回普通有效测试用户，不访问数据库。"""

    return _test_user(superuser=False)


async def authenticated_superuser() -> UserRecord:
    """返回有 Pipeline 权限的测试用户，不访问数据库。"""

    return _test_user(superuser=True)


def allow_reader(app: FastAPI) -> FastAPI:
    """只覆盖普通用户依赖，保留 Pipeline 的真实超级用户检查。"""

    app.dependency_overrides[current_active_user] = authenticated_reader
    return app


def allow_superuser(app: FastAPI) -> FastAPI:
    """覆盖普通与超级用户依赖，供既有 Pipeline 行为测试使用。"""

    app.dependency_overrides[current_active_user] = authenticated_superuser
    app.dependency_overrides[current_superuser] = authenticated_superuser
    return app
