"""本地账号密码 Cookie 认证、权限边界和公开契约的完全离线测试。"""

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi_users import exceptions
from pydantic import SecretStr, ValidationError

from news_vector_service.auth.dependencies import (
    get_database_strategy,
    get_user_manager,
)
from news_vector_service.auth.manager import UserManager
from news_vector_service.config.auth import AuthSettings
from news_vector_service.main import create_app
from news_vector_service.models.user import UserRecord
from news_vector_service.schemas.auth import AuthUserCreate
from tests.auth_helpers import skip_environment_admin_sync


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的认证测试协程。"""

    return asyncio.run(coroutine)


def user(*, superuser: bool = False) -> UserRecord:
    """构造不持久化的已启用内部账号。"""

    return UserRecord(
        id=uuid4(),
        email="operator@example.com" if superuser else "reader@example.com",
        hashed_password="not-used-in-fake-authentication",
        is_active=True,
        is_superuser=superuser,
        is_verified=True,
        is_environment_admin=False,
    )


class FakeRuntime:
    """只满足应用 lifespan，不创建真实搜索客户端。"""

    def __init__(self) -> None:
        self.service = object()

    async def close(self) -> None:
        """不执行外部 I/O。"""


class FakeUserManager:
    """只实现登录 Router 本次测试需要的管理器行为。"""

    def __init__(self, authenticated_user: UserRecord | None) -> None:
        self.authenticated_user = authenticated_user

    async def authenticate(self, _credentials: Any) -> UserRecord | None:
        """返回预置用户，避免执行密码 Hash 或数据库查询。"""

        return self.authenticated_user

    async def on_after_login(self, *_args: Any) -> None:
        """登录后不执行附加 I/O。"""


class FakeDatabaseStrategy:
    """记录 Cookie Token 的写入、读取与退出撤销。"""

    def __init__(self, authenticated_user: UserRecord) -> None:
        self.authenticated_user = authenticated_user
        self.token = "test-cookie-token"
        self.destroyed = False

    async def read_token(self, token: str | None, _manager: Any) -> UserRecord | None:
        """只接受尚未撤销的预置 Token。"""

        if token == self.token and not self.destroyed:
            return self.authenticated_user
        return None

    async def write_token(self, _user: UserRecord) -> str:
        """返回固定测试 Token。"""

        self.destroyed = False
        return self.token

    async def destroy_token(self, token: str, _user: UserRecord) -> None:
        """记录退出已撤销匹配 Token。"""

        if token == self.token:
            self.destroyed = True


def auth_app(authenticated_user: UserRecord) -> tuple[Any, FakeDatabaseStrategy]:
    """创建覆盖数据库依赖但保留真实 FastAPI Users Router 的测试应用。"""

    strategy = FakeDatabaseStrategy(authenticated_user)
    manager = FakeUserManager(authenticated_user)
    app = create_app(  # type: ignore[arg-type]
        runtime_factory=FakeRuntime,
        environment_admin_sync=skip_environment_admin_sync,
    )
    app.dependency_overrides[get_database_strategy] = lambda: strategy
    app.dependency_overrides[get_user_manager] = lambda: manager
    return app, strategy


def test_cookie_login_me_logout_and_revocation_flow() -> None:
    """登录设置 HttpOnly Cookie，退出撤销 Token 并使后续身份读取返回 401。"""

    app, strategy = auth_app(user())

    async def verify() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://testserver",
            ) as client:
                login = await client.post(
                    "/auth/login",
                    data={"username": "reader@example.com", "password": "valid-password"},
                )
                assert login.status_code == 204
                set_cookie = login.headers["set-cookie"]
                assert "HttpOnly" in set_cookie
                assert "Secure" in set_cookie
                assert "SameSite=strict" in set_cookie

                me = await client.get("/auth/me")
                assert me.status_code == 200
                assert me.json() == {
                    "id": str(strategy.authenticated_user.id),
                    "email": "reader@example.com",
                    "is_active": True,
                    "is_superuser": False,
                    "is_verified": True,
                    "is_environment_admin": False,
                }

                pipeline = await client.post("/pipeline/run-once", json={})
                assert pipeline.status_code == 403

                logout = await client.post("/auth/logout")
                assert logout.status_code == 204
                assert strategy.destroyed is True
                assert (await client.get("/auth/me")).status_code == 401

    run(verify())


def test_anonymous_search_is_rejected_before_runtime_service_call() -> None:
    """没有 Cookie 时受保护搜索返回 401，而不是执行搜索。"""

    app, _strategy = auth_app(user())

    async def verify() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://testserver",
            ) as client:
                response = await client.post(
                    "/vector-search",
                    json={"query": "不应执行的匿名查询"},
                )
                assert response.status_code == 401

    run(verify())


def test_openapi_exposes_cookie_security_without_public_registration() -> None:
    """契约声明 Cookie 认证且只包含登录、退出和当前用户接口。"""

    app, _strategy = auth_app(user())
    schema = app.openapi()

    assert schema["components"]["securitySchemes"] == {
        "APIKeyCookie": {"type": "apiKey", "in": "cookie", "name": "news_auth"}
    }
    assert schema["paths"]["/vector-search"]["post"]["security"] == [
        {"APIKeyCookie": []}
    ]
    assert schema["paths"]["/pipeline/run-once"]["post"]["security"] == [
        {"APIKeyCookie": []}
    ]
    assert "/auth/login" in schema["paths"]
    assert "/auth/logout" in schema["paths"]
    assert "/auth/me" in schema["paths"]
    assert "/auth/register" not in schema["paths"]
    assert "/auth/forgot-password" not in schema["paths"]
    assert schema["paths"]["/admin/users"]["get"]["security"] == [
        {"APIKeyCookie": []}
    ]
    assert schema["paths"]["/admin/users"]["post"]["security"] == [
        {"APIKeyCookie": []}
    ]


def test_user_manager_enforces_minimum_password_rules() -> None:
    """CLI 建号不能绕过 12 字符和邮箱同值规则。"""

    manager = UserManager(object())  # type: ignore[arg-type]
    create = AuthUserCreate(email="reader@example.com", password="long-enough-password")

    run(manager.validate_password("long-enough-password", create))
    with pytest.raises(exceptions.InvalidPasswordException):
        run(manager.validate_password("too-short", create))
    with pytest.raises(exceptions.InvalidPasswordException):
        run(manager.validate_password("reader@example.com", create))


def test_auth_tables_define_required_comments_and_indexes() -> None:
    """认证表保持仓库要求的列注释、主外键和授权查询索引。"""

    from news_vector_service.models.user import AccessTokenRecord

    assert all(column.comment for column in UserRecord.__table__.columns)
    assert all(column.comment for column in AccessTokenRecord.__table__.columns)
    assert {index.name for index in UserRecord.__table__.indexes} == {
        "uq_users_email_lower",
        "uq_users_single_environment_admin",
    }
    assert {index.name for index in AccessTokenRecord.__table__.indexes} == {
        "ix_access_tokens_created_at",
        "ix_access_tokens_user_id",
    }


def test_auth_settings_allow_environment_admin_to_be_omitted() -> None:
    """本地未配置保底管理员时保持向后兼容，不生成任何默认凭据。"""

    settings = AuthSettings(_env_file=None)  # type: ignore[call-arg]

    assert settings.environment_admin_configured is False
    assert settings.admin_email is None
    assert settings.admin_password is None


def test_auth_settings_parse_dotenv_admin_as_secret(tmp_path: Path) -> None:
    """本地 .env 可直接配置完整管理员，且 repr/model dump 不暴露密码。"""

    password = "strong-environment-password"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AUTH_ADMIN_EMAIL=admin@example.com\n"
        f"AUTH_ADMIN_PASSWORD={password}\n",
        encoding="utf-8",
    )
    settings = AuthSettings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.environment_admin_configured is True
    assert str(settings.admin_email) == "admin@example.com"
    assert settings.admin_password is not None
    assert settings.admin_password.get_secret_value() == password
    assert password not in repr(settings)
    assert password not in repr(settings.model_dump())


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("admin@example.com", None),
        (None, "strong-environment-password"),
        ("admin@example.com", "too-short"),
        ("admin@example.com", "admin@example.com"),
    ],
)
def test_auth_settings_reject_invalid_environment_admin_pairs(
    email: str | None,
    password: str | None,
) -> None:
    """邮箱、密码必须成对存在，且密码继续遵守共享强度下限。"""

    values: dict[str, object] = {"_env_file": None}
    if email is not None:
        values["admin_email"] = email
    if password is not None:
        values["admin_password"] = SecretStr(password)

    with pytest.raises(ValidationError):
        AuthSettings(**values)  # type: ignore[arg-type]


def test_lifespan_synchronizes_environment_admin_before_search_runtime() -> None:
    """应用必须先完成保底管理员数据库同步，再接受搜索 Runtime 构造。"""

    events: list[str] = []

    class OrderedRuntime(FakeRuntime):
        async def close(self) -> None:
            events.append("close")

    async def synchronize() -> Any:
        events.append("environment_admin")
        return None

    def build_runtime() -> OrderedRuntime:
        assert events == ["environment_admin"]
        events.append("runtime")
        return OrderedRuntime()

    app = create_app(  # type: ignore[arg-type]
        runtime_factory=build_runtime,
        environment_admin_sync=synchronize,
    )

    async def verify() -> None:
        async with app.router.lifespan_context(app):
            assert events == ["environment_admin", "runtime"]

    run(verify())
    assert events == ["environment_admin", "runtime", "close"]
