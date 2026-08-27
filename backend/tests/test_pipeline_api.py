"""阶段 6 手动流水线 HTTP API 与独立写入 Runtime 的完全离线测试。

测试分别注入只读 Search Runtime 和按请求 Write Runtime，不连接 FreshRSS、PostgreSQL、
Ollama 或 Qdrant。重点验证参数边界、startup 零写入、Service 复用、部分失败统计、
错误分类与异常文本脱敏。
"""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from news_vector_service.api.pipeline import build_pipeline_error_response
from news_vector_service.config.freshrss import FreshRSSSettings
from news_vector_service.ingestion.freshrss_client import (
    FreshRSSAuthenticationError,
    FreshRSSConnectionError,
    FreshRSSTimeoutError,
)
from news_vector_service.main import create_app
from news_vector_service.pipeline.ollama_embedding_provider import OllamaTimeoutError
from news_vector_service.pipeline.write_runtime import (
    PipelineRunOnceExecutionResult,
    PipelineWriteRuntime,
)
from news_vector_service.qdrant.lifecycle import QdrantLifecycleError
from news_vector_service.services.freshrss_import_service import SourceSyncFailure
from news_vector_service.services.news_pipeline_execution_service import (
    IndexExecutionFailure,
    NewsSyncExecutionResult,
    PendingIndexExecutionResult,
)
from tests.auth_helpers import allow_superuser, skip_environment_admin_sync


def run(coroutine: Any) -> Any:
    """执行异步 API 测试，不访问外部事件循环。"""

    return asyncio.run(coroutine)


def execution_result(
    *,
    sync_failures: tuple[SourceSyncFailure, ...] = (),
    index_failures: tuple[IndexExecutionFailure, ...] = (),
) -> PipelineRunOnceExecutionResult:
    """构造不含正文或 Vector 的写 Runtime 结果。"""

    return PipelineRunOnceExecutionResult(
        sync=NewsSyncExecutionResult(
            synchronized_count=3,
            source_count=2,
            successful_source_count=2 - len(sync_failures),
            checkpoint_advanced_count=1,
            failures=sync_failures,
        ),
        index=PendingIndexExecutionResult(
            candidate_count=3,
            requeued_stale_count=1,
            indexed_count=2,
            skipped_count=0,
            failures=index_failures,
        ),
    )


class FakeSearchRuntime:
    """只支持 lifespan close；任何写入属性访问都会暴露边界错误。"""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        """记录只读 Runtime 已由 lifespan 关闭。"""

        self.closed = True


class FakeWriteRuntime:
    """记录一次同步调用参数，并返回或抛出预设行为。"""

    def __init__(
        self,
        *,
        result: PipelineRunOnceExecutionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or execution_result()
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def run_once(self, **kwargs: Any) -> PipelineRunOnceExecutionResult:
        """记录参数并执行预设结果，不创建后台 Task。"""

        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result

    async def close(self) -> None:
        """记录按请求 Runtime 已关闭。"""

        self.closed = True


def app_for(
    runtime_factory: Any,
) -> tuple[FastAPI, FakeSearchRuntime]:
    """创建同时注入只读和写入 fake 的 FastAPI 应用。"""

    search_runtime = FakeSearchRuntime()
    app = allow_superuser(
        create_app(
            runtime_factory=lambda: search_runtime,  # type: ignore[arg-type]
            pipeline_runtime_factory=runtime_factory,
            environment_admin_sync=skip_environment_admin_sync,
        )
    )
    return app, search_runtime


async def request(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    """在显式 lifespan 内发送内存 ASGI 请求。"""

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


def test_startup_never_constructs_or_executes_pipeline_write_runtime() -> None:
    factory_calls = 0

    def factory() -> FakeWriteRuntime:
        nonlocal factory_calls
        factory_calls += 1
        return FakeWriteRuntime()

    app, search_runtime = app_for(factory)

    async def verify() -> None:
        async with app.router.lifespan_context(app):
            assert factory_calls == 0

    run(verify())
    assert factory_calls == 0
    assert search_runtime.closed is True


def test_manual_endpoint_uses_defaults_waits_for_result_and_closes_runtime() -> None:
    write_runtime = FakeWriteRuntime()
    factory_calls = 0

    def factory() -> FakeWriteRuntime:
        nonlocal factory_calls
        factory_calls += 1
        return write_runtime

    app, _search_runtime = app_for(factory)
    response = run(request(app, "POST", "/pipeline/run-once", json={}))

    assert response.status_code == 200
    assert factory_calls == 1
    assert write_runtime.calls == [
        {
            "limit_per_source": 2,
            "batch_size": 20,
            "stale_after": timedelta(minutes=60),
        }
    ]
    assert write_runtime.closed is True
    assert response.json() == {
        "ok": True,
        "execution_mode": "manual",
        "sync": {
            "source_count": 2,
            "successful_source_count": 2,
            "failed_source_count": 0,
            "synchronized_document_count": 3,
            "checkpoint_advanced_count": 1,
            "failures": [],
        },
        "index": {
            "requeued_stale_document_count": 1,
            "candidate_document_count": 3,
            "indexed_document_count": 2,
            "skipped_document_count": 0,
            "failed_document_count": 0,
            "failures": [],
        },
    }


@pytest.mark.parametrize(
    "body",
    [
        {"limit_per_source": 0},
        {"limit_per_source": 101},
        {"batch_size": 0},
        {"batch_size": 1001},
        {"stale_after_minutes": 0},
        {"stale_after_minutes": 10081},
        {"background": True},
    ],
)
def test_manual_endpoint_rejects_invalid_or_unknown_parameters(
    body: dict[str, Any],
) -> None:
    factory_calls = 0

    def factory() -> FakeWriteRuntime:
        nonlocal factory_calls
        factory_calls += 1
        return FakeWriteRuntime()

    app, _search_runtime = app_for(factory)
    response = run(request(app, "POST", "/pipeline/run-once", json=body))

    assert response.status_code == 422
    assert factory_calls == 0


def test_partial_source_and_document_failures_are_safe_200_statistics() -> None:
    write_runtime = FakeWriteRuntime(
        result=execution_result(
            sync_failures=(
                SourceSyncFailure("feed/1", "FreshRSSConnectionError"),
                SourceSyncFailure("feed/2", "FreshRSSConnectionError"),
            ),
            index_failures=(
                IndexExecutionFailure(uuid4(), "OllamaTimeoutError"),
            ),
        )
    )
    app, _search_runtime = app_for(lambda: write_runtime)

    response = run(request(app, "POST", "/pipeline/run-once", json={}))

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["sync"]["failed_source_count"] == 2
    assert body["sync"]["failures"] == [
        {"error_type": "FreshRSSConnectionError", "count": 2}
    ]
    assert body["index"]["failed_document_count"] == 1
    assert body["index"]["failures"] == [
        {"error_type": "OllamaTimeoutError", "count": 1}
    ]
    assert "feed/1" not in response.text
    assert str(write_runtime.result.index.failures[0].document_id) not in response.text


def test_batch_error_response_is_classified_and_never_echoes_exception_text() -> None:
    write_runtime = FakeWriteRuntime(
        error=FreshRSSTimeoutError("secret token and full response body")
    )
    app, _search_runtime = app_for(lambda: write_runtime)

    response = run(request(app, "POST", "/pipeline/run-once", json={}))

    assert response.status_code == 504
    assert response.json() == {
        "code": "freshrss_timeout",
        "detail": "FreshRSS request timed out.",
        "error_type": "FreshRSSTimeoutError",
        "retryable": True,
    }
    assert "secret token" not in response.text
    assert "full response body" not in response.text
    assert write_runtime.closed is True


def configuration_error() -> ValidationError:
    """构造真实 Pydantic Settings 校验错误，不读取环境配置。"""

    with pytest.raises(ValidationError) as error:
        FreshRSSSettings(
            provider_key="freshrss_test",
            api_base_url="https://example.com/api/",
            username="user",
            api_password=SecretStr("secret"),
            sync_categories=(),
        )
    return error.value


@pytest.mark.parametrize(
    ("error", "status_code", "code", "retryable"),
    [
        (
            FreshRSSAuthenticationError("private"),
            502,
            "freshrss_authentication_failed",
            False,
        ),
        (FreshRSSConnectionError("private"), 503, "freshrss_unavailable", True),
        (SQLAlchemyError("postgresql://secret"), 503, "postgresql_unavailable", True),
        (OllamaTimeoutError("full vector"), 504, "embedding_timeout", True),
        (QdrantLifecycleError("api-key"), 503, "qdrant_unavailable", True),
        (configuration_error(), 503, "pipeline_configuration_invalid", False),
        (TimeoutError("private"), 504, "pipeline_timeout", True),
    ],
)
def test_known_freshrss_postgresql_ollama_qdrant_config_and_timeout_errors(
    error: Exception,
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    response = build_pipeline_error_response(error)

    assert response.status_code == status_code
    body = bytes(response.body).decode()
    assert code in body
    assert f'"retryable":{str(retryable).lower()}' in body
    for sensitive in ("private", "postgresql://secret", "full vector", "api-key"):
        assert sensitive not in body


def test_pipeline_write_runtime_reuses_execution_service_in_strict_order() -> None:
    events: list[str] = []
    import_service = object()
    index_service = object()
    expected = execution_result()

    class FakeExecutor:
        async def sync_news(self, service: Any, **kwargs: Any) -> NewsSyncExecutionResult:
            assert service is import_service
            assert kwargs == {"limit_per_source": 4}
            events.append("sync")
            return expected.sync

        async def index_pending(
            self,
            service: Any,
            **kwargs: Any,
        ) -> PendingIndexExecutionResult:
            assert service is index_service
            assert kwargs == {
                "batch_size": 7,
                "stale_after": timedelta(minutes=30),
            }
            events.append("index")
            return expected.index

    class FakeIndexingRuntime:
        service = index_service

        async def ensure_ready(self) -> None:
            events.append("ensure_ready")

        async def close(self) -> None:
            events.append("close")

    runtime = PipelineWriteRuntime(
        executor=FakeExecutor(),  # type: ignore[arg-type]
        import_service=import_service,  # type: ignore[arg-type]
        indexing_runtime=FakeIndexingRuntime(),  # type: ignore[arg-type]
    )

    result = run(
        runtime.run_once(
            limit_per_source=4,
            batch_size=7,
            stale_after=timedelta(minutes=30),
        )
    )
    run(runtime.close())

    assert result is not None
    assert events == ["sync", "ensure_ready", "index", "close"]


def test_openapi_exposes_manual_route_without_background_fields() -> None:
    app, _search_runtime = app_for(lambda: FakeWriteRuntime())
    schema = app.openapi()
    operation = schema["paths"]["/pipeline/run-once"]["post"]

    assert operation["requestBody"]["content"]["application/json"]["schema"]
    assert operation["summary"] == "手动执行一次新闻增量同步与向量索引"
    assert "写入副作用" in operation["description"]
    assert operation["responses"]["200"]["description"] == "本轮同步与索引完成后的脱敏统计。"
    assert operation["responses"]["503"]["description"] == "配置、PostgreSQL 或写入上游当前不可用。"
    assert operation["responses"]["200"]
    assert "202" not in operation["responses"]
    tag_descriptions = {item["name"]: item["description"] for item in schema["tags"]}
    assert "手动、有界且同步" in tag_descriptions["pipeline"]
