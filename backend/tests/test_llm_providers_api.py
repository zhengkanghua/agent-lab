"""上游渠道管理 HTTP API 的契约、权限与脱敏测试（完全离线）。

Service 整体替换成内存替身（照 test_scheduled_jobs_api / test_user_admin 的模式），只验证
HTTP 层自己的职责：路由形状、状态码、领域错误的 code→status 映射、权限门，以及**响应里没有
凭据**。Service 的业务行为（加密落库、凭据留空语义、凭据要求）由 ``test_llm_provider_service``
覆盖，两者不重复。
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.api.llm_providers import get_llm_provider_service, router as llm_providers_router
from agent_lab.auth.dependencies import current_superuser
from agent_lab.schemas.llm_providers import LlmProviderUpdateRequest
from agent_lab.services.llm_credential_cipher import LlmCredentialKeyUnavailableError
from agent_lab.services.llm_model_errors import LlmModelDefaultConflictError
from agent_lab.services.llm_provider_errors import (
    LlmProviderCredentialRequiredError,
    LlmProviderNotFoundError,
)
from tests.app_helpers import FakeSearchRuntime, create_offline_app
from tests.auth_helpers import allow_reader, allow_superuser

PROVIDER_ID = "30000000-0000-4000-8000-000000000001"
PLAINTEXT = "sk-live-plaintext-must-not-leak"
CIPHERTEXT = "gAAAAABciphertext-must-not-leak"


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


def make_record(*, credential_configured: bool = True) -> SimpleNamespace:
    """构造一行形状与 ``LlmProviderRecord`` 一致的渠道。"""

    now = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
    return SimpleNamespace(
        id=PROVIDER_ID,
        name="主中转站",
        provider="openai_compatible",
        base_url="https://api.example.com/v1",
        credential_ciphertext=CIPHERTEXT if credential_configured else None,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


class FakeLlmProviderService:
    """记录命令、返回 canned 记录或抛指定异常的 Service 替身。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.created: list[Any] = []
        self.updated: list[tuple] = []
        self.error = error
        self.record = make_record()

    async def list_providers(self) -> list[Any]:
        if self.error is not None:
            raise self.error
        return [self.record]

    async def get_provider(self, provider_id) -> Any:
        if self.error is not None:
            raise self.error
        if str(provider_id) != self.record.id:
            raise LlmProviderNotFoundError()
        return self.record

    async def create_provider(self, request) -> Any:
        if self.error is not None:
            raise self.error
        self.created.append(request)
        return self.record

    async def update_provider(self, provider_id, request) -> Any:
        if self.error is not None:
            raise self.error
        if str(provider_id) != self.record.id:
            raise LlmProviderNotFoundError()
        self.updated.append((provider_id, request))
        return self.record


def make_app(service: FakeLlmProviderService, *, superuser: bool | None = True) -> FastAPI:
    """创建离线应用并整体替换上游渠道 Service，按需挂角色。

    Args:
        service: 替身 Service。
        superuser: ``True`` 覆盖为超级用户；``False`` 只覆盖普通账号（真实超管检查仍在，
            没有 Cookie 时表现为 401）；``None`` 不覆盖任何鉴权依赖。
    """

    app = create_offline_app(runtime_factory=FakeSearchRuntime)
    app.dependency_overrides[get_llm_provider_service] = lambda: service
    if superuser is True:
        allow_superuser(app)
    elif superuser is False:
        allow_reader(app)
    return app


def send(app: FastAPI, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """在显式 lifespan 内发送一个非流式请求。"""

    async def request() -> httpx.Response:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.request(method, path, **kwargs)

    return run(request())


class TestAuthGate:
    def test_requires_login(self) -> None:
        # 不覆盖任何鉴权依赖：真实 Cookie 认证在无 Cookie 时必须给 401。
        app = make_app(FakeLlmProviderService(), superuser=None)
        assert send(app, "GET", "/llm-providers").status_code == 401

    def test_all_routes_are_superuser_gated(self) -> None:
        # 与 pipeline / user-admin / scheduled-jobs 同一道门：普通账号拿不到，装配侧必须挂着
        # 那把超管门的依赖（FastAPI 0.141 起 include_router 在 app.routes 上留的是懒加载包装，
        # 老版本是拍平的 APIRoute，两种形态都认）。
        app = make_app(FakeLlmProviderService(), superuser=None)
        gated_dependencies: list = []
        for route in app.routes:
            if type(route).__name__ == "_IncludedRouter":
                if route.original_router.prefix != "/llm-providers":
                    continue
                gated_dependencies = list(route.include_context.dependencies)
                break
            if getattr(route, "path", "").startswith("/llm-providers"):
                gated_dependencies = list(getattr(route, "dependencies", []))
                break
        assert len(gated_dependencies) == 1
        assert gated_dependencies[0].dependency is current_superuser


class TestRouteShape:
    def test_only_list_read_create_and_update_no_delete(self) -> None:
        """本表只提供停用，不提供删除：四条路由之外不该有第五条。"""

        routes = {
            (route.path, tuple(sorted(route.methods))) for route in llm_providers_router.routes
        }
        assert routes == {
            ("/llm-providers", ("GET",)),
            ("/llm-providers", ("POST",)),
            ("/llm-providers/{provider_id}", ("GET",)),
            ("/llm-providers/{provider_id}", ("PATCH",)),
        }


class TestReadContract:
    def test_list_reports_whether_credential_is_configured(self) -> None:
        app = make_app(FakeLlmProviderService())
        response = send(app, "GET", "/llm-providers")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        provider = body[0]
        assert provider["id"] == PROVIDER_ID
        assert provider["name"] == "主中转站"
        assert provider["provider"] == "openai_compatible"
        assert provider["base_url"] == "https://api.example.com/v1"
        assert provider["enabled"] is True
        assert provider["credential_configured"] is True
        # 响应里只该有契约字段：没有 ORM 属性、更没有凭据。
        assert set(provider) == {
            "id",
            "name",
            "provider",
            "base_url",
            "enabled",
            "credential_configured",
            "created_at",
            "updated_at",
        }
        assert PLAINTEXT not in response.text
        assert CIPHERTEXT not in response.text

    def test_without_credential_the_flag_is_false(self) -> None:
        service = FakeLlmProviderService()
        service.record = make_record(credential_configured=False)
        response = send(make_app(service), "GET", "/llm-providers")

        assert response.json()[0]["credential_configured"] is False

    def test_get_unknown_id_maps_to_404(self) -> None:
        response = send(make_app(FakeLlmProviderService()), "GET", f"/llm-providers/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["code"] == "llm_provider_not_found"


class TestWriteContract:
    def test_create_passes_the_plaintext_to_the_service_and_returns_201(self) -> None:
        service = FakeLlmProviderService()
        response = send(
            make_app(service),
            "POST",
            "/llm-providers",
            json={
                "name": "主中转站",
                "provider": "openai_compatible",
                "base_url": "https://api.example.com/v1",
                "credential": PLAINTEXT,
            },
        )

        assert response.status_code == 201
        request = service.created[0]
        assert request.credential.get_secret_value() == PLAINTEXT
        # 明文只在请求方向出现：响应体里既没有明文也没有密文。
        assert PLAINTEXT not in response.text
        assert CIPHERTEXT not in response.text

    def test_blank_credential_on_update_means_unchanged(self) -> None:
        """空字符串与「字段没出现」是同一个意思，两者都不能被理解成清空凭据。"""

        service = FakeLlmProviderService()
        app = make_app(service)

        assert send(app, "PATCH", f"/llm-providers/{PROVIDER_ID}", json={"name": "改个名"}).status_code == 200
        assert send(
            app, "PATCH", f"/llm-providers/{PROVIDER_ID}", json={"credential": ""}
        ).status_code == 200

        assert [request.credential for _id, request in service.updated] == [None, None]

    def test_credential_writes_through_as_given(self) -> None:
        service = FakeLlmProviderService()
        response = send(
            make_app(service),
            "PATCH",
            f"/llm-providers/{PROVIDER_ID}",
            json={"provider": "openai_compatible", "credential": PLAINTEXT},
        )

        assert response.status_code == 200
        assert service.updated[0][1].credential.get_secret_value() == PLAINTEXT
        assert PLAINTEXT not in response.text


class TestFailureContract:
    def test_missing_credential_maps_to_422(self) -> None:
        service = FakeLlmProviderService(error=LlmProviderCredentialRequiredError())
        response = send(
            make_app(service), "PATCH", f"/llm-providers/{PROVIDER_ID}", json={"provider": "openai_compatible"}
        )

        assert response.status_code == 422
        assert response.json() == {
            "code": "llm_provider_credential_required",
            "detail": "该接入类型必须配置凭据，请在本次保存里补上凭据。",
            "retryable": False,
        }

    def test_missing_master_key_maps_to_503(self) -> None:
        """主密钥没配不是启动失败，而是这一次保存的失败。"""

        service = FakeLlmProviderService(error=LlmCredentialKeyUnavailableError())
        response = send(
            make_app(service),
            "POST",
            "/llm-providers",
            json={
                "name": "主中转站",
                "provider": "openai_compatible",
                "base_url": "https://api.example.com/v1",
                "credential": PLAINTEXT,
            },
        )

        assert response.status_code == 503
        assert response.json() == {
            "code": "llm_catalog_unavailable",
            "detail": "渠道凭据的加密密钥未配置，暂时不能保存凭据。",
            "retryable": False,
        }
        assert PLAINTEXT not in response.text

    def test_database_failure_maps_to_its_own_503(self) -> None:
        service = FakeLlmProviderService(error=SQLAlchemyError("private-column-value"))
        response = send(make_app(service), "GET", "/llm-providers")

        assert response.status_code == 503
        assert response.json() == {
            "code": "llm_catalog_database_unavailable",
            "detail": "模型目录存储当前不可用。",
            "retryable": True,
        }
        assert "private-column-value" not in response.text

    def test_a_model_catalog_conflict_from_this_route_maps_to_its_own_409(self) -> None:
        """启停渠道会碰到「目录里恰好有一个默认」那条不变量，它的守卫在可用模型那一侧。

        那个领域错误会从这条路由冒出来，而它不是 ``LlmProviderDomainError``。路由只认提供方那一个
        基类的话，它会变成未分类的 500——管理员看到的是一句没法照做的事，而其实只要先换个默认。
        """

        service = FakeLlmProviderService(error=LlmModelDefaultConflictError())
        response = send(make_app(service), "PATCH", f"/llm-providers/{PROVIDER_ID}", json={"enabled": True})

        assert response.status_code == 409
        assert response.json() == {
            "code": "llm_model_default_conflict",
            "detail": "另一个请求刚刚改过默认模型，请刷新列表后重试。",
            "retryable": False,
        }

    def test_invalid_body_is_sanitized_and_does_not_echo_the_credential(self) -> None:
        """请求校验失败时既不回显原始正文，也不透露是哪一个字段错了（正文里有凭据）。"""

        response = send(
            make_app(FakeLlmProviderService()),
            "POST",
            "/llm-providers",
            json={
                "name": "主中转站",
                "provider": "openai_compatible",
                "base_url": "not-a-url",
                "credential": PLAINTEXT,
            },
        )

        assert response.status_code == 422
        assert response.json() == {
            "code": "invalid_request",
            "detail": "请求参数无效。",
            "retryable": False,
        }
        assert PLAINTEXT not in response.text


def test_update_request_keeps_whitespace_only_credentials_as_absent() -> None:
    """表单里被清空的密码框提交上来是空串；纯空白一律折算成「没给凭据」。"""

    assert LlmProviderUpdateRequest(credential="").credential is None
    assert LlmProviderUpdateRequest(credential="   ").credential is None
    assert (
        LlmProviderUpdateRequest(credential=PLAINTEXT).credential.get_secret_value() == PLAINTEXT
    )
