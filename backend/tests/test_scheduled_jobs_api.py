"""定时任务管理 HTTP API 的契约与权限测试（完全离线）。

Service 整体替换成内存替身（照 test_user_admin 的模式），只验证 HTTP 层自己的职责：
路由形状、状态码、领域错误的 code→status 映射、权限门和响应脱敏。Service 的业务
行为（校验顺序、调度器同步）由 test_scheduler_runner 与服务级测试覆盖。
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from agent_lab.api.scheduled_jobs import get_scheduled_job_service
from agent_lab.auth.dependencies import current_superuser
from agent_lab.services.scheduled_job_service import ScheduledJobService, ScheduledJobView
from agent_lab.services.scheduled_task_errors import (
    ScheduledJobAlreadyRunningError,
    ScheduledJobInvalidCronError,
    ScheduledJobInvalidParamsError,
    ScheduledJobKeyConflictError,
    ScheduledJobNotFoundError,
    ScheduledJobUnknownTypeError,
)
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_reader, allow_superuser


def run(coroutine: Any) -> Any:
    """执行不依赖 pytest asyncio 插件的测试协程。"""

    return asyncio.run(coroutine)


def make_view(*, key: str = "freshrss-sync", enabled: bool = True) -> ScheduledJobView:
    """构造一条内存视图，形状与真实 Service 返回一致。"""

    now = datetime(2026, 9, 2, 4, 0, tzinfo=UTC)
    job_id = uuid4()
    record = SimpleNamespace(
        id=job_id,
        key=key,
        task_type="freshrss_sync",
        cron_expr="*/10 * * * *",
        params={"limit_per_source": 2},
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )
    last_run = SimpleNamespace(
        id=uuid4(),
        job_id=job_id,
        trigger_type="scheduled",
        status="succeeded",
        started_at=now,
        finished_at=now,
        stats={"synchronized_document_count": 1, "failures": {}},
        error_type=None,
    )
    return ScheduledJobView(
        record=record,
        next_run_at=now,
        last_run=last_run,
    )


class FakeScheduledJobService:
    """记录命令、返回 canned 视图或抛指定领域异常的 Service 替身。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.created: list[dict] = []
        self.updated: list[tuple] = []
        self.deleted: list = []
        self.triggered: list = []
        self.runs_queried: list = []
        self.cron_validated: list = []
        self.error = error
        self.view = make_view()
        # 固定的触发回执 id，断言 run_id 时才有可比对的值。
        self.next_run_id = uuid4()

    async def list_jobs(self) -> list[ScheduledJobView]:
        return [self.view]

    async def get_job(self, job_id) -> ScheduledJobView:
        if self.error is not None:
            raise self.error
        return self.view

    async def create_job(self, **kwargs: Any) -> ScheduledJobView:
        if self.error is not None:
            raise self.error
        self.created.append(kwargs)
        return self.view

    async def update_job(self, job_id, **kwargs: Any) -> ScheduledJobView:
        if self.error is not None:
            raise self.error
        self.updated.append((job_id, kwargs))
        return self.view

    async def delete_job(self, job_id) -> None:
        if self.error is not None:
            raise self.error
        self.deleted.append(job_id)

    async def trigger(self, job_id) -> Any:
        if self.error is not None:
            raise self.error
        self.triggered.append(job_id)
        return self.next_run_id

    async def list_runs(self, job_id, *, limit: int) -> list[Any]:
        if self.error is not None:
            raise self.error
        self.runs_queried.append((job_id, limit))
        return [self.view.last_run]

    def validate_cron(self, cron_expr: str) -> tuple[list, list]:
        if self.error is not None:
            raise self.error
        self.cron_validated.append(cron_expr)
        now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
        return [now], ["2026-09-03T09:00:00+08:00"]


def make_app(
    service: FakeScheduledJobService,
    *,
    superuser: bool | None = True,
) -> FastAPI:
    """创建离线应用并整体替换定时任务 Service，按需挂角色。

    Args:
        service: 替身 Service。
        superuser: ``True`` 覆盖为超级用户；``False`` 覆盖为普通用户（保留真实超管
            检查，用来测 403）；``None`` 不覆盖任何鉴权依赖（用来测未登录 401）。
    """

    app = create_offline_app()
    app.dependency_overrides[get_scheduled_job_service] = lambda: service
    if superuser is True:
        allow_superuser(app)
    elif superuser is False:
        allow_reader(app)
    return app


def send(app: FastAPI, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """在显式 lifespan 内发送非流式请求。"""

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
        app = make_app(FakeScheduledJobService(), superuser=None)
        response = send(app, "GET", "/scheduled-jobs")
        assert response.status_code == 401

    def test_all_routes_are_superuser_gated(self) -> None:
        # 与 pipeline / user-admin 同一道门（Depends(current_superuser)）。普通用户被拒
        # 的完整 403 行为由 test_auth.py 的真实登录流程覆盖（pipeline 已验），这里锁定
        # 的是我们自己的装配：/scheduled-jobs 的 include 必须挂着超管依赖。
        # FastAPI 0.141 起 include_router 在 app.routes 上留的是懒加载包装
        # （_IncludedRouter），依赖在 include_context 里；老版本是拍平的 APIRoute，
        # 依赖在 route.dependencies。两种形态都认，升级 FastAPI 时这里不用改。
        app = make_app(FakeScheduledJobService(), superuser=None)
        gated_dependencies: list = []
        for route in app.routes:
            if type(route).__name__ == "_IncludedRouter":
                if route.original_router.prefix != "/scheduled-jobs":
                    continue
                gated_dependencies = list(route.include_context.dependencies)
                break
            if getattr(route, "path", "").startswith("/scheduled-jobs"):
                gated_dependencies = list(getattr(route, "dependencies", []))
                break
        # include 侧存的是 Depends 包装对象， unwrap 后必须正是 auth 里那个超管门。
        assert len(gated_dependencies) == 1
        assert gated_dependencies[0].dependency is current_superuser


class TestCrudContract:
    def test_list_returns_job_with_next_run_and_last_run(self) -> None:
        app = make_app(FakeScheduledJobService())
        response = send(app, "GET", "/scheduled-jobs")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        job = body[0]
        assert job["key"] == "freshrss-sync"
        assert job["task_type"] == "freshrss_sync"
        assert job["cron_expr"] == "*/10 * * * *"
        assert job["params"] == {"limit_per_source": 2}
        assert job["enabled"] is True
        assert job["next_run_at"] is not None
        assert job["last_run"]["status"] == "succeeded"
        assert job["last_run"]["stats"] == {
            "synchronized_document_count": 1,
            "failures": {},
        }
        # 响应里只该有契约字段：没有 ORM 内部属性顺带漏出去。
        assert set(job) == {
            "id",
            "key",
            "task_type",
            "cron_expr",
            "params",
            "enabled",
            "next_run_at",
            "last_run",
            "created_at",
            "updated_at",
        }

    def test_create_returns_201_and_passes_body_to_service(self) -> None:
        service = FakeScheduledJobService()
        app = make_app(service)
        payload = {
            "key": "my-sync",
            "task_type": "freshrss_sync",
            "cron_expr": "*/5 * * * *",
            "params": {"limit_per_source": 3},
            "enabled": False,
        }
        response = send(app, "POST", "/scheduled-jobs", json=payload)
        assert response.status_code == 201
        assert service.created == [payload]
        assert response.json()["key"] == "freshrss-sync"

    def test_create_key_pattern_rejected_with_sanitized_422(self) -> None:
        app = make_app(FakeScheduledJobService())
        response = send(
            app,
            "POST",
            "/scheduled-jobs",
            json={"key": "大写不合法", "task_type": "freshrss_sync", "cron_expr": "* * * * *"},
        )
        # 走 SanitizedValidationRoute 的固定 invalid_request，不回显原始输入。
        assert response.status_code == 422
        assert response.json() == {
            "code": "invalid_request",
            "detail": "请求参数无效。",
            "retryable": False,
        }

    def test_patch_only_forwards_provided_fields(self) -> None:
        service = FakeScheduledJobService()
        app = make_app(service)
        job_id = service.view.record.id
        response = send(
            app,
            "PATCH",
            f"/scheduled-jobs/{job_id}",
            json={"cron_expr": "0 9 * * *", "enabled": False},
        )
        assert response.status_code == 200
        # 路由把三个可选字段都转发给 Service；params=None 由 Service 解释为「不修改」。
        assert service.updated == [
            (
                job_id,
                {"cron_expr": "0 9 * * *", "params": None, "enabled": False},
            )
        ]

    def test_delete_returns_204(self) -> None:
        service = FakeScheduledJobService()
        app = make_app(service)
        job_id = service.view.record.id
        response = send(app, "DELETE", f"/scheduled-jobs/{job_id}")
        assert response.status_code == 204
        assert service.deleted == [job_id]


class TestDomainErrorMapping:
    @pytest.mark.parametrize(
        ("error", "expected_status"),
        [
            (ScheduledJobNotFoundError(), 404),
            (ScheduledJobKeyConflictError(), 409),
            (ScheduledJobAlreadyRunningError(uuid4()), 409),
            (ScheduledJobInvalidCronError(), 422),
            (ScheduledJobInvalidParamsError(), 422),
            (ScheduledJobUnknownTypeError(), 422),
        ],
    )
    def test_domain_errors_map_to_stable_statuses(self, error, expected_status) -> None:
        app = make_app(FakeScheduledJobService(error=error))
        response = send(app, "GET", f"/scheduled-jobs/{uuid4()}")
        assert response.status_code == expected_status
        body = response.json()
        assert body["code"] == error.code
        assert body["detail"] == error.detail
        assert body["retryable"] is False


class TestTriggerAndRuns:
    def test_trigger_returns_202_with_run_receipt(self) -> None:
        service = FakeScheduledJobService()
        app = make_app(service)
        job_id = service.view.record.id
        response = send(app, "POST", f"/scheduled-jobs/{job_id}/trigger")
        assert response.status_code == 202
        body = response.json()
        assert body["job_id"] == str(job_id)
        assert body["status"] == "running"
        assert body["run_id"] == str(service.next_run_id)

    def test_trigger_conflict_maps_to_409(self) -> None:
        app = make_app(
            FakeScheduledJobService(error=ScheduledJobAlreadyRunningError(uuid4()))
        )
        response = send(app, "POST", f"/scheduled-jobs/{uuid4()}/trigger")
        assert response.status_code == 409
        assert response.json()["code"] == "scheduled_job_already_running"

    def test_list_runs_forwards_limit_and_maps_records(self) -> None:
        service = FakeScheduledJobService()
        app = make_app(service)
        job_id = service.view.record.id
        response = send(app, "GET", f"/scheduled-jobs/{job_id}/runs?limit=5")
        assert response.status_code == 200
        assert service.runs_queried == [(job_id, 5)]
        runs = response.json()
        assert runs[0]["trigger_type"] == "scheduled"

    def test_list_runs_rejects_out_of_range_limit(self) -> None:
        app = make_app(FakeScheduledJobService())
        response = send(app, "GET", f"/scheduled-jobs/{uuid4()}/runs?limit=0")
        assert response.status_code == 422


class TestCronPreview:
    def test_validate_cron_returns_utc_and_local(self) -> None:
        service = FakeScheduledJobService()
        app = make_app(service)
        response = send(
            app, "POST", "/scheduled-jobs/validate-cron", json={"cron_expr": "0 9 * * *"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["next_run_times"] == ["2026-09-03T01:00:00Z"]
        assert body["next_run_times_local"] == ["2026-09-03T09:00:00+08:00"]

    def test_validate_cron_invalid_maps_to_422(self) -> None:
        app = make_app(FakeScheduledJobService(error=ScheduledJobInvalidCronError()))
        response = send(
            app, "POST", "/scheduled-jobs/validate-cron", json={"cron_expr": "bad"}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "scheduled_job_invalid_cron"
