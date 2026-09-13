"""公共执行与策略管理的公开 HTTP 流程、去重回执和权限边界。"""

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.app_helpers import FakeSearchRuntime, create_offline_app
from tests.auth_helpers import allow_superuser
from tests.task_helpers import echo_spec, task_system


@asynccontextmanager
async def task_client(*specs):
    async with task_system(*specs) as system:
        app = allow_superuser(create_offline_app(runtime_factory=FakeSearchRuntime,
            task_service_factory=lambda: system.service))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            yield system, client


@pytest.mark.parametrize("path", ["/task-runs", f"/task-runs/{uuid4()}", "/task-policy", "/task-policy/changes"])
@pytest.mark.parametrize("logged_in, expected", [(False, 401), (True, 403)])
def test_read_routes_require_superuser(path, logged_in, expected):
    from agent_lab.auth.dependencies import cookie_transport
    from tests.test_auth import auth_app, user
    async def scenario():
        app, strategy = auth_app(user(superuser=False))
        headers = {"cookie": f"{cookie_transport.cookie_name}={strategy.token}"} if logged_in else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(path, headers=headers)
            assert response.status_code == expected
    asyncio.run(scenario())


def test_public_submit_list_cancel_and_expired_receipt():
    async def scenario():
        async with task_client() as (system, client):
            headers, body = {"Idempotency-Key": "same"}, {"task_type": "test_echo", "params": {"value": 6}}
            response = await client.post("/task-runs", json=body, headers=headers)
            assert response.status_code == 202 and response.json()["status"] == "queued"
            identity = response.json()["run_id"]
            assert (await client.post("/task-runs", json=body, headers=headers)).json()["run_id"] == identity
            conflict = await client.post("/task-runs", json={**body, "params": {}}, headers=headers)
            assert conflict.status_code == 409 and conflict.json()["run_id"] == identity
            listed = await client.get("/task-runs?status=queued&task_type=test_echo")
            assert [row["id"] for row in listed.json()] == [identity]
            detail = await client.get(f"/task-runs/{identity}")
            assert detail.json()["can_cancel"] and not detail.json()["can_retry"]
            cancelled = await client.post(f"/task-runs/{identity}/cancel")
            assert cancelled.json()["status"] == "cancelled" and not cancelled.json()["can_cancel"]
            await system.worker.execute(UUID(identity), 1)
            system.clock.advance(31 * 86400)
            await system.store.prune_history()
            assert (await client.get(f"/task-runs/{identity}")).status_code == 410
            replay = await client.post("/task-runs", json=body, headers=headers)
            assert replay.status_code == 202 and replay.json()["details_expired"]
            assert replay.json()["run_id"] == identity
    asyncio.run(scenario())


def test_retry_uses_failed_parameters_and_policy_changes_are_auditable():
    async def scenario():
        async with task_client(echo_spec(execute=AsyncMock(side_effect=RuntimeError("private")))) as (system, client):
            original = await client.post("/task-runs", json={"task_type": "test_echo", "params": {"value": 9}},
                headers={"Idempotency-Key": "failed"})
            identity = original.json()["run_id"]
            await system.worker.execute(UUID(identity), 1)
            changed = await client.put("/task-policy", json={"max_retries": 2, "retry_delay_seconds": 20, "history_retention_days": 45})
            assert changed.status_code == 200
            history = (await client.get("/task-policy/changes")).json()
            assert len(history) == 1 and history[0]["previous"]["max_retries"] == 3
            retry = await client.post(f"/task-runs/{identity}/retry", headers={"Idempotency-Key": "retry"})
            assert retry.status_code == 202 and retry.json()["run_id"] != identity
            detail = (await client.get(f"/task-runs/{retry.json()['run_id']}")).json()
            assert detail["retry_of"] == identity and detail["config_snapshot"]["params"] == {"value": 9}
            assert detail["policy_snapshot"]["max_retries"] == 2
            assert "private" not in str(history) + str(detail)
    asyncio.run(scenario())
