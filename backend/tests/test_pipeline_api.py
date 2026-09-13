"""手动 Pipeline 持久受理后由 Worker 执行，HTTP 按编号查询脱敏统计。"""

import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.config.freshrss import FreshRSSSettings
from agent_lab.domain.write_scope import write_scope
from agent_lab.ingestion.freshrss_client import FreshRSSAuthenticationError, FreshRSSConnectionError, FreshRSSTimeoutError
from agent_lab.pipeline.ollama_embedding_provider import OllamaTimeoutError
from agent_lab.pipeline.write_runtime import PipelineRunOnceExecutionResult, PipelineWriteRuntime
from agent_lab.qdrant.lifecycle import QdrantLifecycleError
from agent_lab.knowledge.document_contracts import SourceSyncFailure
from agent_lab.services.news_pipeline_execution_service import IndexExecutionFailure, NewsSyncExecutionResult, PendingIndexExecutionResult
from agent_lab.services.scheduled_task_registry import TASK_TYPE_SPECS
from agent_lab.services.scheduled_tasks import classify_error
from tests.app_helpers import FakeSearchRuntime, create_offline_app
from tests.auth_helpers import allow_superuser
from tests.task_helpers import task_system

run = asyncio.run


def execution_result(*, sync_failures=(), index_failures=()):
    return PipelineRunOnceExecutionResult(
        sync=NewsSyncExecutionResult(synchronized_count=3, source_count=2,
            successful_source_count=2 - len(sync_failures), checkpoint_advanced_count=1, failures=sync_failures),
        index=PendingIndexExecutionResult(candidate_count=3, requeued_stale_count=1,
            indexed_count=2, skipped_count=0, failures=index_failures),
    )


class FakeWriteRuntime:
    def __init__(self, *, result=None, error=None):
        self.result, self.error = result or execution_result(), error
        self.calls, self.closed, self.session_factory = [], False, None

    async def sync_only(self, **kwargs):
        # 同步阶段不占 index；后续处理阶段已经释放 sync。
        assert write_scope.get().resources == ("sync",)
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result.sync

    async def index_only(self, **kwargs):
        assert write_scope.get() is None
        self.calls.append(kwargs)
        return self.result.index

    async def close(self):
        self.closed = True


@asynccontextmanager
async def pipeline_system(runtime):
    spec = replace(TASK_TYPE_SPECS["pipeline_run_once"], runtime_factory=lambda: runtime)
    async with task_system(spec) as system:
        runtime.session_factory = system.sessions
        app = allow_superuser(create_offline_app(runtime_factory=FakeSearchRuntime,
            task_service_factory=lambda: system.service))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                yield system, client, app


def test_intake_returns_202_without_business_then_worker_publishes_result():
    async def verify():
        runtime = FakeWriteRuntime()
        async with pipeline_system(runtime) as (system, client, _app):
            assert runtime.calls == []
            response = await client.post("/pipeline/run-once", json={}, headers={"Idempotency-Key": "a"})
            assert response.status_code == 202
            accepted = response.json()
            assert accepted["status"] == "queued" and runtime.calls == []
            identity = UUID(accepted["run_id"])
            assert (await client.get(f"/task-runs/{identity}")).json()["status"] == "queued"
            await system.worker.execute(identity, 1)
            detail = await client.get(f"/task-runs/{identity}")
            assert detail.status_code == 200 and detail.json()["status"] == "succeeded"
            stats = detail.json()["stats"]
            assert stats["ok"] is True
            assert stats["sync"]["synchronized_document_count"] == 3
            assert stats["index"]["indexed_document_count"] == 2
            assert runtime.calls == [{"limit_per_source": 2}, {"batch_size": 20, "stale_after": timedelta(minutes=60)}]
            assert runtime.closed
            repeated = await client.post("/pipeline/run-once", json={}, headers={"Idempotency-Key": "a"})
            assert repeated.json()["run_id"] == str(identity)
    run(verify())


@pytest.mark.parametrize("body", [
    {"limit_per_source": 0}, {"limit_per_source": 101}, {"batch_size": 0}, {"batch_size": 1001},
    {"stale_after_minutes": 0}, {"stale_after_minutes": 10081}, {"background": True},
])
def test_manual_endpoint_rejects_invalid_or_unknown_parameters(body):
    async def verify():
        runtime = FakeWriteRuntime()
        async with pipeline_system(runtime) as (system, client, _app):
            response = await client.post("/pipeline/run-once", json=body, headers={"Idempotency-Key": "a"})
            assert response.status_code == 422 and not runtime.calls
            assert not await system.service.list_runs()
    run(verify())


def test_partial_failures_are_preserved_in_execution_detail():
    async def verify():
        runtime = FakeWriteRuntime(result=execution_result(
            sync_failures=(SourceSyncFailure("feed/1", "FreshRSSConnectionError"), SourceSyncFailure("feed/2", "FreshRSSConnectionError")),
            index_failures=(IndexExecutionFailure(uuid4(), "OllamaTimeoutError"),),
        ))
        async with pipeline_system(runtime) as (system, client, _app):
            response = await client.post("/pipeline/run-once", json={}, headers={"Idempotency-Key": "a"})
            identity = UUID(response.json()["run_id"])
            await system.worker.execute(identity, 1)
            detail = await client.get(f"/task-runs/{identity}")
            stats = detail.json()["stats"]
            assert stats["ok"] is False
            assert stats["sync"]["failed_source_count"] == 2
            assert stats["sync"]["failures"] == [{"error_type": "FreshRSSConnectionError", "count": 2}]
            assert stats["index"]["failed_document_count"] == 1
            assert stats["index"]["failures"] == [{"error_type": "OllamaTimeoutError", "count": 1}]
            assert "feed/1" not in detail.text and str(runtime.result.index.failures[0].document_id) not in detail.text
    run(verify())


def test_safe_timeout_waits_for_retry_without_echoing_exception():
    async def verify():
        runtime = FakeWriteRuntime(error=FreshRSSTimeoutError("secret token and full response body"))
        async with pipeline_system(runtime) as (system, client, _app):
            accepted = await client.post("/pipeline/run-once", json={}, headers={"Idempotency-Key": "a"})
            assert accepted.status_code == 202
            identity = UUID(accepted.json()["run_id"])
            await system.worker.execute(identity, 1)
            response = await client.get(f"/task-runs/{identity}")
            assert response.json()["status"] == "retry_wait"
            assert response.json()["stats"]["error_code"] == "freshrss_timeout"
            assert response.json()["error_type"] == "FreshRSSTimeoutError"
            assert "secret token" not in response.text and runtime.closed
    run(verify())


def configuration_error():
    with pytest.raises(ValidationError) as error:
        FreshRSSSettings(provider_key="freshrss_test", api_base_url="https://example.com/api/",
            username="user", api_password=SecretStr("secret"), sync_categories=())
    return error.value


@pytest.mark.parametrize("error, code, safe_retry", [
    (FreshRSSAuthenticationError("private"), "freshrss_authentication_failed", False),
    (FreshRSSConnectionError("private"), "freshrss_unavailable", True),
    (SQLAlchemyError("postgresql://secret"), "postgresql_unavailable", False),
    (OllamaTimeoutError("full vector"), "embedding_timeout", False),
    (QdrantLifecycleError("api-key"), "qdrant_unavailable", False),
    (configuration_error(), "pipeline_configuration_invalid", False),
    (TimeoutError("private"), "pipeline_timeout", False),
])
def test_business_error_classification_preserves_safe_codes(error, code, safe_retry):
    decision = classify_error(error)
    assert decision.stats["error_code"] == code and decision.retryable is safe_retry
    for sensitive in ("private", "postgresql://secret", "full vector", "api-key"):
        assert sensitive not in str(decision)


def test_pipeline_write_runtime_syncs_then_uses_shared_processing_batch() -> None:
    events: list[str] = []
    import_service = object()
    expected = execution_result()

    class FakeExecutor:
        def writing(self, _resources):
            return nullcontext()

        async def sync_news(self, service: Any, **kwargs: Any) -> NewsSyncExecutionResult:
            assert service is import_service
            assert kwargs == {"limit_per_source": 4}
            events.append("sync")
            return expected.sync

    class FakeBatch:
        async def run(self, **kwargs: Any) -> PendingIndexExecutionResult:
            assert kwargs == {
                "batch_size": 7,
                "stale_after": timedelta(minutes=30),
            }
            events.append("index")
            return expected.index

    runtime = PipelineWriteRuntime(
        executor=FakeExecutor(),  # type: ignore[arg-type]
        import_service=import_service,  # type: ignore[arg-type]
        processing_batch=FakeBatch(),  # type: ignore[arg-type]
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
    assert events == ["sync", "index"]
    assert result.index is expected.index



def test_openapi_exposes_async_receipt_and_independent_query():
    app = create_offline_app(runtime_factory=FakeSearchRuntime)
    schema = app.openapi()
    operation = schema["paths"]["/pipeline/run-once"]["post"]
    assert "202" in operation["responses"] and "200" not in operation["responses"]
    assert any(item["name"] == "Idempotency-Key" and item["required"] for item in operation["parameters"])
    assert "/task-runs/{run_id}" in schema["paths"]
