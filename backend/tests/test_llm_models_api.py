"""可用模型 HTTP API 的契约、权限与脱敏测试（完全离线）。

Service 整体替换成内存替身（照 ``test_llm_providers_api`` 的模式），只验证 HTTP 层自己的职责：
路由形状、权限门（**管理路由要超级用户，选择列表任何登录账号都能读**）、状态码、领域错误的
code→status 映射，以及两个视图的字段。Service 的业务行为（不变量的七条规则）由
``test_llm_model_service`` 覆盖，两者不重复。

并发那一条在 HTTP 层的形状是「冲突领域错误 → 409，且文案不泄漏内部信息」：真并发要在真库上
才验得出来（那条部分唯一索引是 PG 方言的东西），这里用替身抛那个错误来钉对外契约。
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.api.llm_models import get_llm_model_service, router as llm_models_router
from agent_lab.auth.dependencies import current_active_user, current_superuser
from agent_lab.services.llm_model_errors import (
    LlmModelDefaultCannotBeClearedError,
    LlmModelDefaultCannotBeDisabledError,
    LlmModelDefaultConflictError,
    LlmModelDefaultNotAvailableError,
    LlmModelDomainError,
    LlmModelNameConflictError,
    LlmModelNotFoundError,
    LlmModelProviderDisabledError,
)
from agent_lab.services.llm_model_service import ModelView
from agent_lab.services.llm_provider_errors import LlmProviderNotFoundError
from tests.app_helpers import FakeSearchRuntime, create_offline_app
from tests.auth_helpers import allow_reader, allow_superuser

MODEL_ID = "40000000-0000-4000-8000-000000000001"
PROVIDER_ID = "30000000-0000-4000-8000-000000000001"


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


def make_view(**overrides: Any) -> ModelView:
    """构造一条形状与 ``LlmModelRecord`` 一致的记录（管理视图要读的字段都在）。"""

    now = datetime(2026, 9, 20, 4, 0, tzinfo=UTC)
    fields: dict[str, Any] = {
        "id": MODEL_ID,
        "provider_id": PROVIDER_ID,
        "upstream_model_name": "qwen2.5:7b",
        "display_name": "演示模型",
        "context_window": 32768,
        "is_default": True,
        "enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return ModelView(
        model=SimpleNamespace(**fields), provider_name="本地渠道", provider_enabled=True
    )


class FakeLlmModelService:
    """记录命令、返回 canned 视图或抛指定异常的 Service 替身。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.created: list[Any] = []
        self.updated: list[tuple] = []
        self.error = error
        self.view = make_view()

    async def list_models(self) -> list[ModelView]:
        if self.error is not None:
            raise self.error
        return [self.view]

    async def list_available_models(self) -> list[ModelView]:
        if self.error is not None:
            raise self.error
        return [self.view]

    async def create_model(self, request) -> ModelView:
        if self.error is not None:
            raise self.error
        self.created.append(request)
        return self.view

    async def update_model(self, model_id, request) -> ModelView:
        if self.error is not None:
            raise self.error
        if str(model_id) != self.view.model.id:
            raise LlmModelNotFoundError()
        self.updated.append((model_id, request))
        return self.view


def make_app(service: FakeLlmModelService, *, account: str = "superuser") -> FastAPI:
    """创建离线应用并整体替换可用模型 Service，按需挂角色。

    Args:
        service: 替身 Service。
        account: ``superuser`` 覆盖为超级用户；``reader`` 只覆盖普通账号（真实超管检查仍在）；
            ``anonymous`` 不覆盖任何鉴权依赖。
    """

    app = create_offline_app(runtime_factory=FakeSearchRuntime)
    app.dependency_overrides[get_llm_model_service] = lambda: service
    if account == "superuser":
        allow_superuser(app)
    elif account == "reader":
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
        """两条读路由在无 Cookie 时都必须给 401（真实认证依赖没有被覆盖）。"""

        app = make_app(FakeLlmModelService(), account="anonymous")
        assert send(app, "GET", "/llm-models").status_code == 401
        assert send(app, "GET", "/llm-models/available").status_code == 401

    def test_the_selection_list_is_open_to_any_account_and_management_is_not(self) -> None:
        """选择列表任何已登录账号都读得到；管理路由仍然挡着。

        这条兼作「装配侧没有给整个 router 加 include 级超管门」的证据：加了的话，普通账号读
        ``/llm-models/available`` 会先被那道门挡下来，拿不到 200。管理那几条在这里表现为 401
        而不是 403——测试夹具只覆盖了「已登录」那道依赖，``current_superuser`` 是真实的，它
        自己拿不到 Cookie；「该不该给超级用户」这件事由下面那条结构断言钉住。
        """

        reader = make_app(FakeLlmModelService(), account="reader")
        assert send(reader, "GET", "/llm-models/available").status_code == 200
        assert send(reader, "GET", "/llm-models").status_code == 401
        assert send(reader, "POST", "/llm-models", json={}).status_code == 401
        assert send(
            reader, "PATCH", f"/llm-models/{MODEL_ID}", json={"enabled": False}
        ).status_code == 401

    def test_each_route_carries_its_own_gate(self) -> None:
        """门挂在每条路由自己身上（不是整组）：三条管理路由是超管那道，选择列表是「已登录」那道。"""

        gates = {
            (route.path, tuple(sorted(route.methods))): [
                dependency.dependency for dependency in route.dependencies
            ]
            for route in llm_models_router.routes
        }
        assert gates == {
            ("/llm-models/available", ("GET",)): [current_active_user],
            ("/llm-models", ("GET",)): [current_superuser],
            ("/llm-models", ("POST",)): [current_superuser],
            ("/llm-models/{model_id}", ("PATCH",)): [current_superuser],
        }


class TestRouteShape:
    def test_there_is_no_delete_route(self) -> None:
        """本表只提供停用，不提供删除：四条路由之外不该有第五条。"""

        routes = {
            (route.path, tuple(sorted(route.methods))) for route in llm_models_router.routes
        }
        assert routes == {
            ("/llm-models/available", ("GET",)),
            ("/llm-models", ("GET",)),
            ("/llm-models", ("POST",)),
            ("/llm-models/{model_id}", ("PATCH",)),
        }


class TestReadContract:
    def test_the_management_list_carries_the_provider_and_the_raw_display_name(self) -> None:
        response = send(make_app(FakeLlmModelService()), "GET", "/llm-models")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        model = body[0]
        assert model == {
            "id": MODEL_ID,
            "provider_id": PROVIDER_ID,
            "provider_name": "本地渠道",
            "provider_enabled": True,
            "upstream_model_name": "qwen2.5:7b",
            "display_name": "演示模型",
            "context_window": 32768,
            "is_default": True,
            "enabled": True,
            "created_at": "2026-09-20T04:00:00Z",
            "updated_at": "2026-09-20T04:00:00Z",
        }

    def test_the_management_list_keeps_a_missing_display_name_as_null(self) -> None:
        """管理视图给的是原始字段：没有展示名就是 null，编辑表单据此知道它是一个空输入框。"""

        service = FakeLlmModelService()
        service.view = make_view(display_name=None)
        response = send(make_app(service), "GET", "/llm-models")

        assert response.json()[0]["display_name"] is None

    def test_the_selection_list_falls_back_to_the_upstream_name(self) -> None:
        """条目没填展示名时，选择器拿到的就是上游模型名；选择列表只回选择器要用的四个字段。"""

        service = FakeLlmModelService()
        service.view = make_view(display_name=None)
        response = send(make_app(service), "GET", "/llm-models/available")

        assert response.status_code == 200
        assert response.json() == [
            {
                "id": MODEL_ID,
                "display_name": "qwen2.5:7b",
                "context_window": 32768,
                "provider_name": "本地渠道",
            }
        ]

    def test_a_configured_display_name_shows_up_as_is(self) -> None:
        response = send(make_app(FakeLlmModelService()), "GET", "/llm-models/available")

        assert response.json()[0]["display_name"] == "演示模型"


class TestWriteContract:
    def test_create_passes_the_body_to_the_service_and_returns_201(self) -> None:
        service = FakeLlmModelService()
        response = send(
            make_app(service),
            "POST",
            "/llm-models",
            json={
                "provider_id": PROVIDER_ID,
                "upstream_model_name": "qwen2.5:7b",
                "display_name": "演示模型",
                "context_window": 32768,
            },
        )

        assert response.status_code == 201
        request = service.created[0]
        assert str(request.provider_id) == PROVIDER_ID
        assert request.upstream_model_name == "qwen2.5:7b"
        assert request.context_window == 32768
        # 没给的两个开关走默认值：新建即启用，且不主动打默认标记（目录没有默认时会自动补）
        assert (request.enabled, request.is_default) == (True, False)

    @pytest.mark.parametrize("window", [None, 0, -1])
    def test_a_missing_or_non_positive_window_is_rejected(self, window: float | None) -> None:
        """窗口留空（或不大于零）在 HTTP 层就被拒：它是必填字段，没有默认值。"""

        body: dict[str, Any] = {
            "provider_id": PROVIDER_ID,
            "upstream_model_name": "qwen2.5:7b",
        }
        if window is not None:
            body["context_window"] = window

        response = send(make_app(FakeLlmModelService()), "POST", "/llm-models", json=body)

        assert response.status_code == 422
        # 请求体里没有凭据，仍然沿用同一套脱敏校验契约：模型目录这一族只回一个固定形状。
        assert response.json() == {
            "code": "invalid_request",
            "detail": "请求参数无效。",
            "retryable": False,
        }

    def test_a_blank_display_name_means_no_display_name(self) -> None:
        """空串与纯空白都折算成「没有展示名」，而且请求里确实带着这个字段（不是「不修改」）。"""

        service = FakeLlmModelService()
        app = make_app(service)

        for blank in ("", "   "):
            assert (
                send(
                    app, "PATCH", f"/llm-models/{MODEL_ID}", json={"display_name": blank}
                ).status_code
                == 200
            )

        assert [request.display_name for _id, request in service.updated] == [None, None]
        assert all("display_name" in request.model_fields_set for _id, request in service.updated)

    def test_an_omitted_display_name_means_unchanged(self) -> None:
        service = FakeLlmModelService()
        send(make_app(service), "PATCH", f"/llm-models/{MODEL_ID}", json={"context_window": 65536})

        request = service.updated[0][1]
        assert "display_name" not in request.model_fields_set
        assert request.context_window == 65536

    def test_enable_and_default_flags_are_passed_through(self) -> None:
        service = FakeLlmModelService()
        send(
            make_app(service),
            "PATCH",
            f"/llm-models/{MODEL_ID}",
            json={"enabled": False, "is_default": True, "provider_id": PROVIDER_ID},
        )

        request = service.updated[0][1]
        assert (request.enabled, request.is_default) == (False, True)
        assert str(request.provider_id) == PROVIDER_ID

    def test_updating_an_unknown_model_maps_to_404(self) -> None:
        response = send(
            make_app(FakeLlmModelService()),
            "PATCH",
            f"/llm-models/{uuid4()}",
            json={"enabled": False},
        )

        assert response.status_code == 404
        assert response.json()["code"] == "llm_model_entry_not_found"


class TestFailureContract:
    @pytest.mark.parametrize(
        ("error", "status_code", "code"),
        [
            (LlmModelNotFoundError(), 404, "llm_model_entry_not_found"),
            (LlmModelNameConflictError(), 409, "llm_model_name_conflict"),
            (LlmModelProviderDisabledError(), 409, "llm_model_provider_disabled"),
            (LlmModelDefaultNotAvailableError(), 409, "llm_model_default_not_available"),
            (
                LlmModelDefaultCannotBeClearedError(),
                409,
                "llm_model_default_cannot_be_cleared",
            ),
            (
                LlmModelDefaultCannotBeDisabledError(),
                409,
                "llm_model_default_cannot_be_disabled",
            ),
            (LlmModelDefaultConflictError(), 409, "llm_model_default_conflict"),
            # 所属渠道不存在属于渠道那一族：同一条失败不该有两个 code
            (LlmProviderNotFoundError(), 404, "llm_provider_not_found"),
        ],
    )
    def test_every_domain_error_keeps_its_own_code_and_message(
        self, error: LlmModelDomainError, status_code: int, code: str
    ) -> None:
        """逐条钉住「一个失败一个 code 一句预写文案」：文案来自异常自带的 detail，不是异常文本。"""

        service = FakeLlmModelService(error=error)
        response = send(
            make_app(service), "PATCH", f"/llm-models/{MODEL_ID}", json={"enabled": False}
        )

        assert response.status_code == status_code
        assert response.json() == {
            "code": code,
            "detail": error.detail,
            "retryable": False,
        }

    def test_the_default_conflict_is_a_clear_409_that_leaks_nothing(self) -> None:
        """两个请求同时设默认时，落败的一方拿到明确的 409，且文案里没有库内部的东西。

        这是并发那一半在 HTTP 层的形状：真并发（部分唯一索引真的拦住第二条默认）要在真库上
        才验得出来，这里钉的是落败方看到的对外契约——管理员据此重试，而不用去看数据库报错。
        """

        service = FakeLlmModelService(error=LlmModelDefaultConflictError())
        response = send(
            make_app(service),
            "PATCH",
            f"/llm-models/{MODEL_ID}",
            json={"is_default": True},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "llm_model_default_conflict"
        for leaked in (
            "uq_llm_models_single_default",
            "is_default",
            "IntegrityError",
            "duplicate key",
            "llm_models",
        ):
            assert leaked not in response.text

    def test_a_database_failure_maps_to_the_catalog_503(self) -> None:
        service = FakeLlmModelService(error=SQLAlchemyError("private-column-value"))
        response = send(make_app(service), "GET", "/llm-models")

        assert response.status_code == 503
        assert response.json() == {
            "code": "llm_catalog_database_unavailable",
            "detail": "模型目录存储当前不可用。",
            "retryable": True,
        }
        assert "private-column-value" not in response.text
