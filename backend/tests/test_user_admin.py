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
from tests.auth_helpers import SUPERUSER_ID, authenticated_superuser


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
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        self.calls: list[tuple[str, object]] = []
        self.error: UserAdminDomainError | None = None

    async def list_users(self, include_deleted: bool = False) -> list[object]:
        """返回固定账号列表，并记录路由有没有把「带上已注销」传下来。"""

        self.calls.append(("list", include_deleted))
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
        actor_id: UUID,
    ) -> object:
        """记录停用/启用命令，含路由传下来的调用者身份。"""

        if self.error is not None:
            raise self.error
        self.calls.append(
            ("update", (user_id, request.is_active, actor_id))
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

    async def delete_user(self, user_id: UUID, actor_id: UUID) -> None:
        """记录注销命令，含路由传下来的调用者身份。"""

        if self.error is not None:
            raise self.error
        self.calls.append(("delete", (user_id, actor_id)))

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
        # 带上参数的一次单独取：只验 Service 参数的话，「接口上没暴露这个参数」会从缝里滑过去。
        listed_with_deleted = await request(app, "GET", "/admin/users?include_deleted=true")
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
        assert listed_with_deleted.status_code == 200
        assert listed_with_deleted.json()[0]["email"] == "managed@example.com"
        assert created.status_code == 201
        assert updated.status_code == 200
        assert password.status_code == 200
        assert revoked.json() == {"revoked_sessions": 2}
        combined = "".join(
            [listed.text, listed_with_deleted.text, created.text, updated.text, password.text, revoked.text]
        )
        assert "never-echo-this-password" not in combined
        assert "another-private-password" not in combined

    run(verify())
    assert service.calls == [
        ("list", False),
        ("list", True),
        ("create", ("new@example.com", True, 24)),
        # 第三个元素是调用者 id：路由把当前登录账号交给了 Service。漏传的话
        # 「不能停用或注销自己」就静默失效，而整套离线用例照样全绿。
        ("update", (service.user_id, False, SUPERUSER_ID)),
        ("password", (service.user_id, 24)),
        ("sessions", service.user_id),
    ]


def test_delete_account_is_superuser_only_and_returns_no_body() -> None:
    """注销账号挂在鉴权之后，成功时是 204 空响应。

    两件只有 HTTP 层才成立的事：未登录时 Service 根本不被调用；成功时没有任何响应体
    （前端按 204 读空体，多一个 body 反而要额外处理）。
    """

    anonymous = FakeAdminService()
    anonymous_response = run(
        request(
            build_app(anonymous, authenticated=False),
            "DELETE",
            f"/admin/users/{anonymous.user_id}",
        )
    )
    assert anonymous_response.status_code == 401
    assert anonymous.calls == []

    service = FakeAdminService()
    response = run(
        request(build_app(service, authenticated=True), "DELETE", f"/admin/users/{service.user_id}")
    )
    assert response.status_code == 204
    assert response.content == b""
    assert service.calls == [("delete", (service.user_id, SUPERUSER_ID))]


def test_delete_account_maps_domain_error_to_stable_response() -> None:
    """注销账号的领域错误按稳定 code 返回，不泄露内部状态。"""

    service = FakeAdminService()
    service.error = UserAdminDomainError(
        "last_superuser_protected",
        "最后一个活跃超级管理员不能被注销。",
    )

    response = run(
        request(build_app(service, authenticated=True), "DELETE", f"/admin/users/{service.user_id}")
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "last_superuser_protected",
        "detail": "最后一个活跃超级管理员不能被注销。",
        "retryable": False,
    }


def test_environment_admin_domain_error_has_stable_conflict_response() -> None:
    """环境托管账号不能从 HTTP 管理入口停用，响应不泄露内部状态。"""

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
            json={"is_active": False},
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


# 直接打 Service 的那些用例里，调用者必须与目标账号**不同**：写成同一个 id 会先命中
# 「不能停用或注销自己」，那几条用例就不再验原来那件事了（保护码会从环境托管/最后超管
# 变成 account_self_protected，而它们本来的意图正是要验那两个）。
OPERATOR_ID = UUID("00000000-0000-4000-8000-0000000000ff")


class SingleAccountSession:
    """模拟只返回一个预置账号的请求级数据库 Session。

    ``scalar`` 回放那个账号、``scalars`` 回放它的主键：后者让「还剩几个活跃超管」的
    查询看到只有一行，保护分支因此必然触发。不需要数据库，也不需要真 Session 的其余能力。
    """

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
        """记录提交次数。"""

        self.commit_count += 1

    async def refresh(self, _instance: object) -> None:
        """什么都不做：刷新只为取回数据库生成的列，这里没有数据库。"""


def test_service_protects_last_active_superuser() -> None:
    """最后一个活跃超管不能被停用。

    这条守的需求与改动前一样（不能把管理入口锁死），只是触发它的动作变了：降权这个动作
    已经取消（见 ADR 0038），能让人退出活跃超管的只剩停用与注销。
    """

    current = UserRecord(
        id=uuid4(),
        email="last-admin@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_environment_admin=False,
    )
    session = SingleAccountSession(current)
    service = UserAdminService(session)  # type: ignore[arg-type]

    with pytest.raises(UserAdminDomainError) as error:
        run(service.update_user(current.id, UserAdminUpdateRequest(is_active=False), OPERATOR_ID))

    assert error.value.code == "last_superuser_protected"
    assert session.rollback_count == 1
    assert session.commit_count == 0


def test_service_rolls_back_before_protecting_environment_admin() -> None:
    """环境托管保护分支显式结束行锁事务。"""

    current = UserRecord(
        id=uuid4(),
        email="env-admin@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_environment_admin=True,
    )
    session = SingleAccountSession(current)
    service = UserAdminService(session)  # type: ignore[arg-type]

    with pytest.raises(UserAdminDomainError) as error:
        run(service.update_user(current.id, UserAdminUpdateRequest(is_active=False), OPERATOR_ID))

    assert error.value.code == "environment_admin_protected"
    assert session.rollback_count == 1
    assert session.commit_count == 0


def test_service_protects_last_active_superuser_from_deletion() -> None:
    """最后一个活跃超管连注销都不行，且失败时不留半截事务。

    与 ``update_user`` 那条同源但更硬：停用至少还有别的超管能把它启用回来，注销连入口都没了。
    """

    current = UserRecord(
        id=uuid4(),
        email="only-admin@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_environment_admin=False,
    )
    session = SingleAccountSession(current)
    service = UserAdminService(session)  # type: ignore[arg-type]

    with pytest.raises(UserAdminDomainError) as error:
        run(service.delete_user(current.id, OPERATOR_ID))

    assert error.value.code == "last_superuser_protected"
    assert session.rollback_count == 1
    assert session.commit_count == 0


def test_service_refuses_to_delete_environment_managed_admin() -> None:
    """环境托管账号注销不得：注销了下次启动还会被配置拉回来。

    这个保护与降权无关，所以降权动作取消之后它仍在——只是触发它的动作从「取消超管」换成了
    「停用」与「注销」。
    """

    current = UserRecord(
        id=uuid4(),
        email="env-admin@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_environment_admin=True,
    )
    session = SingleAccountSession(current)
    service = UserAdminService(session)  # type: ignore[arg-type]

    with pytest.raises(UserAdminDomainError) as error:
        run(service.delete_user(current.id, OPERATOR_ID))

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


def _deregistered_account() -> UserRecord:
    """一个已注销的账号：不可用 + 有注销时间，和库里那条配对约束的要求一致。"""

    return UserRecord(
        id=uuid4(),
        email="deregistered@example.com",
        hashed_password="not-used",
        is_active=False,
        is_superuser=False,
        is_verified=True,
        is_environment_admin=False,
        deleted_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_service_refuses_status_change_and_password_reset_on_deregistered_account() -> None:
    """已注销账号上的停用/启用与改密都被拒，且报的是「已注销」而不是「不存在」。

    两个码分得清是给调用方用的：前端据此显示「该账号已经注销」，而不是「该账号已不存在，
    请刷新列表」——后者会把人引去刷新一个本来就正确的列表。
    """

    account = _deregistered_account()

    for action, request in (
        ("update", UserAdminUpdateRequest(is_active=True)),
        ("reset", UserAdminPasswordRequest(password="a" * 12)),
    ):
        session = SingleAccountSession(account)
        service = UserAdminService(session)  # type: ignore[arg-type]

        with pytest.raises(UserAdminDomainError) as error:
            if action == "update":
                run(service.update_user(account.id, request, OPERATOR_ID))
            else:
                run(service.reset_password(account.id, request))

        assert error.value.code == "account_already_deleted"
        assert "注销" in error.value.detail
        # 拒绝要连行锁一起放开，不能把锁留到请求结束。
        assert session.rollback_count == 1
        assert session.commit_count == 0


def test_service_deregistering_twice_succeeds_and_leaves_the_first_timestamp() -> None:
    """对已注销账号再注销一次：成功，不报错、不改时间、不重复清 Token。

    注销是「同一个目标状态，重复到达算成功」，与停用/改密那两类动作刻意不同。
    ``SingleAccountSession`` 没有 ``execute``：实现若去删 Token，这里会直接报
    ``AttributeError``，所以「不重复清凭据」也被这条用例盯着。
    """

    account = _deregistered_account()
    original_timestamp = account.deleted_at
    session = SingleAccountSession(account)
    service = UserAdminService(session)  # type: ignore[arg-type]

    run(service.delete_user(account.id, OPERATOR_ID))

    assert account.deleted_at == original_timestamp
    assert account.is_active is False
    # 没有写入可提交，事务只用来放开行锁。
    assert session.commit_count == 0
    assert session.rollback_count == 1


@pytest.mark.parametrize("code", ["account_already_deleted", "account_self_protected"])
def test_new_account_domain_errors_map_to_conflict(code: str) -> None:
    """两个新错误码都映射成 409，与现有那两个保护类码同形。

    只测到 HTTP 层：Service 内部抛不抛由各自的用例盯着，这里管的是「同一个领域错误
    在响应里长什么样」——前端按 code 查文案表，状态码错了它就走不到那张表。
    """

    service = FakeAdminService()
    service.error = UserAdminDomainError(code, "该操作不被允许。")

    response = run(
        request(build_app(service, authenticated=True), "PATCH", f"/admin/users/{service.user_id}", json={"is_active": False})
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": code,
        "detail": "该操作不被允许。",
        "retryable": False,
    }


def test_service_refuses_to_stop_or_deregister_the_caller_itself() -> None:
    """停用自己与注销自己都被拒，报的是 `account_self_protected`。

    这是**后端规则**，不靠界面隐藏：客户端不可信，直接打接口也必须被拒。写日志、
    脚本、别的前端都可能绕过那个「自己那一行不显示开关」的界面约定。
    """

    current = UserRecord(
        id=uuid4(),
        email="operator@example.com",
        hashed_password="not-used",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_environment_admin=False,
    )

    for action in ("stop", "deregister"):
        session = SingleAccountSession(current)
        service = UserAdminService(session)  # type: ignore[arg-type]

        with pytest.raises(UserAdminDomainError) as error:
            if action == "stop":
                run(
                    service.update_user(
                        current.id,
                        UserAdminUpdateRequest(is_active=False),
                        current.id,
                    )
                )
            else:
                run(service.delete_user(current.id, current.id))

        assert error.value.code == "account_self_protected"
        assert "换一个账号" in error.value.detail
        assert session.rollback_count == 1
        assert session.commit_count == 0
    # 失败之后账号状态一个字节都没动。
    assert current.is_active is True
    assert current.deleted_at is None


def test_service_allows_enabling_yourself_again() -> None:
    """「启用自己」不在保护范围内：它不会让人失去权限，也不会绕过任何边界。

    只有停用与注销需要调用者身份；把启用也挡掉会让「最后一步只能由别人来做」这种
    无关的情况多出一条规则。
    """

    current = UserRecord(
        id=uuid4(),
        email="operator@example.com",
        hashed_password="not-used",
        is_active=False,
        is_superuser=False,
        is_verified=True,
        is_environment_admin=False,
    )
    session = SingleAccountSession(current)
    service = UserAdminService(session)  # type: ignore[arg-type]

    updated = run(
        service.update_user(current.id, UserAdminUpdateRequest(is_active=True), current.id)
    )

    assert updated.is_active is True
    assert session.commit_count == 1
