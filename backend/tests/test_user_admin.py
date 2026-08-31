"""超级用户账号管理 HTTP 契约与关键权限保护的完全离线测试。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from agent_lab.api.user_admin import get_user_admin_service
from agent_lab.auth.dependencies import current_superuser
from agent_lab.models.user import UserRecord
from agent_lab.schemas.user_admin import (
    UserAdminCreateRequest,
    UserAdminPasswordRequest,
    UserAdminUpdateRequest,
)
from agent_lab.services.user_admin_service import (
    INVALID_PASSWORD_DETAIL,
    UserAdminDomainError,
    UserAdminService,
)
from tests.app_helpers import create_offline_app
from tests.auth_helpers import authenticated_superuser


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的账号管理测试协程。"""

    return asyncio.run(coroutine)


class FakeRuntime:
    """只满足应用 lifespan，不创建真实搜索客户端。"""

    service = object()

    async def close(self) -> None:
        """不执行外部 I/O。"""


class FakeAdminService:
    """记录 HTTP 层传入的账号管理命令并返回安全测试视图。"""

    def __init__(self) -> None:
        now = datetime(2026, 8, 18, tzinfo=UTC)
        self.user_id = uuid4()
        self.user = SimpleNamespace(
            id=self.user_id,
            email="managed@example.com",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            is_environment_admin=False,
            created_at=now,
            updated_at=now,
        )
        self.calls: list[tuple[str, object]] = []
        self.error: UserAdminDomainError | None = None

    async def list_users(self) -> list[object]:
        """返回固定账号列表。"""

        self.calls.append(("list", ""))
        return [self.user]

    async def create_user(self, request: UserAdminCreateRequest) -> object:
        """记录创建请求，不保存密码。"""

        self.calls.append(
            (
                "create",
                (str(request.email), request.is_superuser, len(request.password)),
            )
        )
        return self.user

    async def update_user(
        self,
        user_id: UUID,
        request: UserAdminUpdateRequest,
    ) -> object:
        """记录状态更新，或抛出预置领域错误。"""

        if self.error is not None:
            raise self.error
        self.calls.append(
            ("update", (user_id, request.is_active, request.is_superuser))
        )
        return self.user

    async def reset_password(
        self,
        user_id: UUID,
        request: UserAdminPasswordRequest,
    ) -> object:
        """只记录密码长度，避免测试记录保留明文。"""

        self.calls.append(("password", (user_id, len(request.password))))
        return self.user

    async def revoke_sessions(self, user_id: UUID) -> int:
        """记录会话撤销并返回固定删除数。"""

        self.calls.append(("sessions", user_id))
        return 2


def build_app(service: FakeAdminService, *, authenticated: bool) -> Any:
    """创建使用 fake Runtime 和 fake 管理 Service 的测试应用。"""

    app = create_offline_app(runtime_factory=FakeRuntime)
    app.dependency_overrides[get_user_admin_service] = lambda: service
    if authenticated:
        app.dependency_overrides[current_superuser] = authenticated_superuser
    return app


async def request(app: Any, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """在完整 lifespan 中向 ASGI 应用发送一次请求。"""

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


def test_user_admin_requires_superuser_before_service_call() -> None:
    """匿名访问返回 401，且账号管理 Service 不执行。"""

    service = FakeAdminService()
    response = run(request(build_app(service, authenticated=False), "GET", "/admin/users"))

    assert response.status_code == 401
    assert service.calls == []


def test_user_admin_http_commands_use_typed_bodies_and_safe_responses() -> None:
    """列表、创建、更新、改密和撤销会话保持稳定请求/响应契约。"""

    service = FakeAdminService()
    app = build_app(service, authenticated=True)

    async def verify() -> None:
        listed = await request(app, "GET", "/admin/users")
        created = await request(
            app,
            "POST",
            "/admin/users",
            json={
                "email": "new@example.com",
                "password": "never-echo-this-password",
                "is_superuser": True,
            },
        )
        updated = await request(
            app,
            "PATCH",
            f"/admin/users/{service.user_id}",
            json={"is_active": False},
        )
        password = await request(
            app,
            "POST",
            f"/admin/users/{service.user_id}/password",
            json={"password": "another-private-password"},
        )
        revoked = await request(
            app,
            "DELETE",
            f"/admin/users/{service.user_id}/sessions",
        )

        assert listed.status_code == 200
        assert listed.json()[0]["email"] == "managed@example.com"
        assert created.status_code == 201
        assert updated.status_code == 200
        assert password.status_code == 200
        assert revoked.json() == {"revoked_sessions": 2}
        combined = "".join(
            [listed.text, created.text, updated.text, password.text, revoked.text]
        )
        assert "never-echo-this-password" not in combined
        assert "another-private-password" not in combined

    run(verify())
    assert service.calls == [
        ("list", ""),
        ("create", ("new@example.com", True, 24)),
        ("update", (service.user_id, False, None)),
        ("password", (service.user_id, 24)),
        ("sessions", service.user_id),
    ]


def test_environment_admin_domain_error_has_stable_conflict_response() -> None:
    """环境托管账号不能从 HTTP 管理入口降级，响应不泄露内部状态。"""

    service = FakeAdminService()
    service.error = UserAdminDomainError(
        "environment_admin_protected",
        "环境托管的管理员账号必须通过服务端密钥修改。",
    )
    response = run(
        request(
            build_app(service, authenticated=True),
            "PATCH",
            f"/admin/users/{service.user_id}",
            json={"is_superuser": False},
        )
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "environment_admin_protected",
        "detail": (
            "环境托管的管理员账号必须通过服务端密钥修改。"
        ),
        "retryable": False,
    }


def test_user_admin_validation_error_is_stable_and_does_not_echo_password() -> None:
    """管理请求的 422 使用统一脱敏结构，不返回 Pydantic 原始输入。"""

    private_password = "short"
    response = run(
        request(
            build_app(FakeAdminService(), authenticated=True),
            "POST",
            "/admin/users",
            json={
                "email": "reader@example.com",
                "password": private_password,
                "is_superuser": False,
            },
        )
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "detail": "请求参数无效。",
        "retryable": False,
    }
    assert private_password not in response.text


class FinalSuperuserSession:
    """模拟只剩一个启用超级用户的请求级数据库 Session。"""

    def __init__(self, user: UserRecord) -> None:
        self.user = user
        self.rollback_count = 0
        self.commit_count = 0

    async def scalar(self, _statement: object) -> UserRecord:
        """返回待更新用户。"""

        return self.user

    async def scalars(self, _statement: object) -> list[UUID]:
        """返回唯一启用超级用户的主键。"""

        return [self.user.id]

    async def rollback(self) -> None:
        """记录领域保护触发的回滚。"""

        self.rollback_count += 1

    async def commit(self) -> None:
        """记录不应发生的提交。"""

        self.commit_count += 1


def test_service_protects_last_active_superuser() -> None:
    """最后一个启用超级用户不能被禁用或降级。"""

    current = UserRecord(
        id=uuid4(),
        email="last-admin@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_environment_admin=False,
    )
    session = FinalSuperuserSession(current)
    service = UserAdminService(session)  # type: ignore[arg-type]

    with pytest.raises(UserAdminDomainError) as error:
        run(service.update_user(current.id, UserAdminUpdateRequest(is_active=False)))

    assert error.value.code == "last_superuser_protected"
    assert session.rollback_count == 1
    assert session.commit_count == 0


def test_service_rolls_back_before_protecting_environment_admin() -> None:
    """环境管理员保护分支显式结束行锁事务。"""

    current = UserRecord(
        id=uuid4(),
        email="env-admin@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_environment_admin=True,
    )
    session = FinalSuperuserSession(current)
    service = UserAdminService(session)  # type: ignore[arg-type]

    with pytest.raises(UserAdminDomainError) as error:
        run(service.update_user(current.id, UserAdminUpdateRequest(is_superuser=False)))

    assert error.value.code == "environment_admin_protected"
    assert session.rollback_count == 1
    assert session.commit_count == 0


def test_password_policy_detail_is_local_and_never_upstream_text() -> None:
    """密码策略拒绝时，detail 必须来自本地常量，不能转发 fastapi-users 的 reason。

    reason 由上游库定义，文本不受本项目控制，转发它等于把上游文本送进响应体。

    这里只用「密码等于邮箱」触发：长度违例在 Service 层不可达，
    `UserAdminCreateRequest.password` 已声明 min_length=12/max_length=128，
    过短请求在 Pydantic 阶段就是 422，到不了 validate_password_strength。
    长度分支的真实入口是 `auth/bootstrap.py`，那里密码来自 .env 且没有 schema 约束。
    """

    same_as_email = "reader@example.com"
    service = UserAdminService(object())  # type: ignore[arg-type]

    with pytest.raises(UserAdminDomainError) as error:
        run(
            service.create_user(
                UserAdminCreateRequest(
                    email=same_as_email, password=same_as_email, is_superuser=False
                )
            )
        )

    assert error.value.code == "invalid_password"
    assert error.value.detail == INVALID_PASSWORD_DETAIL
    assert same_as_email not in error.value.detail
