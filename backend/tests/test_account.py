"""账号自助改密的 HTTP 契约与 Service 行为的完全离线测试。

分两层，边界与 ``test_user_admin.py`` 一致：
- HTTP 层用 fake Service，只证明「路由把认证层的账号和 Token 交给了 Service」「稳定错误码到
  状态码的映射」「任何响应都不回显明文密码」。
- Service 层用 fake Session，证明「旧密码校验」「行锁与回滚」「只删其他 Token 保留当前那个」。

本文件不连 PostgreSQL、不访问网络。Service 层用**真实**的 Argon2 哈希（``PasswordHelper``
现算），因为本接口的核心行为就是验旧密码，而 ``tests/auth_helpers.py`` 里两个角色的
``hashed_password`` 是一个假串——那两个角色服务于不验密码的既有测试，不在这里改动它们。
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi_users.password import PasswordHelper

from agent_lab.api.account import get_account_service
from agent_lab.auth.dependencies import current_active_user_token
from agent_lab.models.user import UserRecord
from agent_lab.schemas.account import AccountPasswordChangeRequest
from agent_lab.services.account_service import (
    INVALID_CURRENT_PASSWORD_DETAIL,
    AccountDomainError,
    AccountService,
)
from agent_lab.services.user_admin_service import INVALID_PASSWORD_DETAIL
from tests.app_helpers import create_offline_app, send
from tests.auth_helpers import READER_ID, authenticated_reader

# 本次请求 Cookie 里那个 Token。断言「保留当前会话」时要能把它和别的 Token 区分开，
# 所以取一个一眼能认出来的固定值。
CURRENT_TOKEN = "current-session-token"

# 明文密码只存在于测试进程内，且必须能在响应文本里搜到——「不回显」的断言靠的就是搜它。
OLD_PASSWORD = "old-private-passphrase"
NEW_PASSWORD = "new-private-passphrase"


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的账号自助测试协程。"""

    return asyncio.run(coroutine)


class FakeRuntime:
    """只满足应用 lifespan，不创建真实搜索客户端。"""

    service = object()

    async def close(self) -> None:
        """不执行外部 I/O。"""


class FakeAccountService:
    """记录 HTTP 层传入的自助改密命令，只保留密码长度。

    刻意不存明文：测试记录本身也是一份日志，存了它就等于把「密码不该落到别处」这条约束
    在测试里破一次。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, int, int]] = []
        self.error: Exception | None = None

    async def change_own_password(
        self,
        user: UserRecord,
        current_token: str,
        request: AccountPasswordChangeRequest,
    ) -> None:
        """记录调用参数，或抛出预置错误。"""

        if self.error is not None:
            raise self.error
        self.calls.append(
            (
                user.id,
                current_token,
                len(request.current_password),
                len(request.new_password),
            )
        )


async def fake_identity() -> tuple[UserRecord, str]:
    """替代 ``current_active_user_token``，返回 ``(账号, 本次请求 Token)``。"""

    return await authenticated_reader(), CURRENT_TOKEN


def build_app(service: FakeAccountService, *, authenticated: bool) -> Any:
    """创建使用 fake Runtime 和 fake 自助 Service 的测试应用。"""

    app = create_offline_app(runtime_factory=FakeRuntime)
    app.dependency_overrides[get_account_service] = lambda: service
    if authenticated:
        app.dependency_overrides[current_active_user_token] = fake_identity
    return app


def test_self_service_password_requires_authentication_before_service_call() -> None:
    """匿名改密返回 401，且自助 Service 不执行。"""

    service = FakeAccountService()
    response = run(
        send(
            build_app(service, authenticated=False),
            "POST",
            "/auth/me/password",
            json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
        )
    )

    assert response.status_code == 401
    assert service.calls == []


def test_self_service_password_passes_authenticated_identity_and_returns_204() -> None:
    """成功改密返回 204 空响应，账号与当前 Token 都取自认证层而非请求体。"""

    service = FakeAccountService()
    response = run(
        send(
            build_app(service, authenticated=True),
            "POST",
            "/auth/me/password",
            json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
        )
    )

    assert response.status_code == 204
    assert response.text == ""
    assert service.calls == [
        (READER_ID, CURRENT_TOKEN, len(OLD_PASSWORD), len(NEW_PASSWORD))
    ]


def test_wrong_current_password_is_422_not_401_and_never_echoes_input() -> None:
    """旧密码错误必须是 422。

    这条是硬约束不是口味：前端 ``api/client.ts`` 见到 401 会触发全局「登录已失效」把人踢回
    登录页。给 401 的话，输错一次旧密码就会连带丢掉表单里已填好的新密码。
    """

    service = FakeAccountService()
    service.error = AccountDomainError(
        "current_password_invalid",
        INVALID_CURRENT_PASSWORD_DETAIL,
    )
    response = run(
        send(
            build_app(service, authenticated=True),
            "POST",
            "/auth/me/password",
            json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
        )
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "current_password_invalid",
        "detail": INVALID_CURRENT_PASSWORD_DETAIL,
        "retryable": False,
    }
    assert OLD_PASSWORD not in response.text
    assert NEW_PASSWORD not in response.text


def test_environment_admin_self_service_change_is_conflict() -> None:
    """环境托管账号的密码由服务端配置管，自助入口一律 409。"""

    service = FakeAccountService()
    service.error = AccountDomainError(
        "environment_admin_protected",
        "环境托管的管理员账号必须通过服务端密钥修改。",
    )
    response = run(
        send(
            build_app(service, authenticated=True),
            "POST",
            "/auth/me/password",
            json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
        )
    )

    assert response.status_code == 409
    assert response.json()["code"] == "environment_admin_protected"


def test_self_service_validation_error_is_stable_and_does_not_echo_password() -> None:
    """新密码过短在 Pydantic 阶段就被拦，响应是统一脱敏结构而非原始输入。

    这条同时是 ``SanitizedValidationRoute`` 确实挂在 ``/auth/me/password`` 上的证据：
    默认的 ``RequestValidationError`` 处理器会把 ``input``（就是明文密码）放进响应体。
    """

    short_new_password = "too-short"
    response = run(
        send(
            build_app(FakeAccountService(), authenticated=True),
            "POST",
            "/auth/me/password",
            json={
                "current_password": OLD_PASSWORD,
                "new_password": short_new_password,
            },
        )
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "detail": "请求参数无效。",
        "retryable": False,
    }
    assert short_new_password not in response.text
    assert OLD_PASSWORD not in response.text


class RecordingSession:
    """记录语句、提交与回滚的请求级 Session 替身。

    ``scalar`` 恒返回预置的那一行，等价于「行锁拿到了」；传 ``user=None`` 即可构造「认证之后
    账号又被删了」那个窄窗口。``execute`` 只把语句收下来，由用例自己编译它检查 WHERE 条件。
    """

    def __init__(self, user: UserRecord | None) -> None:
        self.user = user
        self.statements: list[Any] = []
        self.commit_count = 0
        self.rollback_count = 0

    async def scalar(self, _statement: object) -> UserRecord | None:
        """返回预置的待改密账号。"""

        return self.user

    async def execute(self, statement: Any) -> None:
        """收下 DELETE 语句，不执行。"""

        self.statements.append(statement)

    async def commit(self) -> None:
        """记录提交。"""

        self.commit_count += 1

    async def rollback(self) -> None:
        """记录回滚。"""

        self.rollback_count += 1


def build_account(*, environment_admin: bool = False) -> UserRecord:
    """构造一个持有 ``OLD_PASSWORD`` 真实哈希的测试账号。

    哈希是现算的，不是写死的常量：写死会把 ``PasswordHelper`` 当前的算法和参数钉进测试，
    以后上游换默认算法时这里会以「旧密码校验坏了」的样子假失败。
    """

    return UserRecord(
        id=uuid4(),
        email="self-service@example.com",
        hashed_password=PasswordHelper().hash(OLD_PASSWORD),
        is_active=True,
        is_superuser=False,
        is_verified=True,
        is_environment_admin=environment_admin,
    )


def change(session: RecordingSession, current: str, new: str) -> None:
    """用给定 Session 跑一次改密，参数是两个明文密码。"""

    service = AccountService(session)  # type: ignore[arg-type]
    run(
        service.change_own_password(
            session.user,  # type: ignore[arg-type]
            CURRENT_TOKEN,
            AccountPasswordChangeRequest(current_password=current, new_password=new),
        )
    )


def test_service_replaces_hash_and_keeps_only_the_current_session() -> None:
    """旧密码正确时换 Hash 并只删其他 Token，新密码随后可用、旧密码失效。"""

    account = build_account()
    original_hash = account.hashed_password
    session = RecordingSession(account)

    change(session, OLD_PASSWORD, NEW_PASSWORD)

    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert account.hashed_password != original_hash

    # 新 Hash 真的对应新密码，旧密码不再可用——只断言「Hash 变了」的话，
    # 写进去一个任意串也能过。
    helper = PasswordHelper()
    assert helper.verify_and_update(NEW_PASSWORD, account.hashed_password)[0]
    assert not helper.verify_and_update(OLD_PASSWORD, account.hashed_password)[0]

    # 明文不该出现在落库的那个值里。
    assert NEW_PASSWORD not in account.hashed_password

    # 删除条件必须同时限定「本人的 Token」和「不是当前这个」。少了后半条就是全踢，
    # 用户改完密码立刻被登出。
    assert len(session.statements) == 1
    compiled = session.statements[0].compile()
    assert "access_tokens.token !=" in str(compiled)
    assert set(compiled.params.values()) == {account.id, CURRENT_TOKEN}


def test_service_rejects_wrong_current_password_without_touching_the_hash() -> None:
    """旧密码错误时不改 Hash、不提交，并显式回滚释放行锁。"""

    account = build_account()
    original_hash = account.hashed_password
    session = RecordingSession(account)

    with pytest.raises(AccountDomainError) as error:
        change(session, "wrong-old-passphrase", NEW_PASSWORD)

    assert error.value.code == "current_password_invalid"
    assert error.value.detail == INVALID_CURRENT_PASSWORD_DETAIL
    assert account.hashed_password == original_hash
    assert session.commit_count == 0
    assert session.rollback_count == 1
    assert session.statements == []


def test_service_rejects_weak_new_password_with_local_detail() -> None:
    """新密码不合强度策略时被拒，detail 用本地常量而非上游 reason 文本。

    这里用「新密码等于登录邮箱」触发：长度违例在 Service 层不可达，
    ``AccountPasswordChangeRequest.new_password`` 已声明 min_length=12，过短的请求在
    Pydantic 阶段就是 422。
    """

    account = build_account()
    original_hash = account.hashed_password
    session = RecordingSession(account)

    with pytest.raises(AccountDomainError) as error:
        change(session, OLD_PASSWORD, account.email)

    assert error.value.code == "invalid_password"
    assert error.value.detail == INVALID_PASSWORD_DETAIL
    assert account.email not in error.value.detail
    assert account.hashed_password == original_hash
    assert session.commit_count == 0
    assert session.rollback_count == 1


def test_service_refuses_environment_admin_before_verifying_password() -> None:
    """环境托管账号在验密码之前就被拒，Hash 不变。"""

    account = build_account(environment_admin=True)
    original_hash = account.hashed_password
    session = RecordingSession(account)

    with pytest.raises(AccountDomainError) as error:
        change(session, OLD_PASSWORD, NEW_PASSWORD)

    assert error.value.code == "environment_admin_protected"
    assert account.hashed_password == original_hash
    assert session.commit_count == 0
    assert session.rollback_count == 1


def test_service_treats_vanished_account_as_wrong_current_password() -> None:
    """认证通过后账号又被删了，按「旧密码不对」处理，不泄露账号已不存在。"""

    account = build_account()
    session = RecordingSession(None)
    service = AccountService(session)  # type: ignore[arg-type]

    with pytest.raises(AccountDomainError) as error:
        run(
            service.change_own_password(
                account,
                CURRENT_TOKEN,
                AccountPasswordChangeRequest(
                    current_password=OLD_PASSWORD,
                    new_password=NEW_PASSWORD,
                ),
            )
        )

    assert error.value.code == "current_password_invalid"
    assert session.commit_count == 0
    assert session.rollback_count == 1
