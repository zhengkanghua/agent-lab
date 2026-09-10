"""统一审核 HTTP 的权限、并发命令和原件下载安全边界。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from agent_lab.api.document_review import get_document_review_application
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, ProcessingReceipt
from tests.app_helpers import create_offline_app
from tests.auth_helpers import SUPERUSER_ID, allow_superuser
from tests.test_auth import auth_app, user


@pytest.mark.parametrize("method,path", [
    ("GET", ""), ("GET", "/{id}"), ("POST", "/{id}/draft"),
    ("GET", "/candidates/{id}/original"), ("POST", "/candidates/{id}/reject"),
    ("GET", "/{id}/versions"), ("GET", "/{id}/reviews"),
])
def test_management_and_originals_require_superuser(method, path):
    async def verify():
        app, _ = auth_app(user())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            response = await client.request(method, "/document-management" + path.format(id=uuid4()))
            assert response.status_code == 401
            await client.post("/auth/login", data={"username": "reader@example.com", "password": "valid-password"})
            response = await client.request(method, "/document-management" + path.format(id=uuid4()))
            assert response.status_code == 403
    asyncio.run(verify())


def test_adoption_requires_matching_preview_and_passes_authenticated_actor():
    async def verify():
        identity, document_id = uuid4(), uuid4()
        service = SimpleNamespace(adopt=AsyncMock(return_value=ProcessingReceipt(identity, document_id, "adopting", "a" * 64)))
        app = allow_superuser(create_offline_app())
        app.dependency_overrides[get_document_review_application] = lambda: service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            path = f"/document-management/candidates/{identity}/adopt"
            command = {"candidate_revision": 2, "management_revision": 4, "fingerprint": "b" * 64}
            response = await client.post(path, json=command)
            assert response.status_code == 202 and response.json()["state"] == "adopting"
            assert service.adopt.await_args.kwargs["actor_id"] == SUPERUSER_ID
            service.adopt.reset_mock()
            response = await client.post(path, json={**command, "candidate_revision": 0, "text": "私密草稿"})
            assert response.status_code == 422 and "私密草稿" not in response.text
            service.adopt.assert_not_awaited()
            service.adopt.side_effect = ProcessingApplicationError("document_preview_stale")
            response = await client.post(path, json=command)
            assert response.status_code == 409 and response.json()["code"] == "document_preview_stale"
    asyncio.run(verify())


def test_original_is_exact_bytes_with_download_headers_not_executable_html():
    async def verify():
        original = b"<script>alert(1)</script>\r\n\x00"
        service = SimpleNamespace(original=AsyncMock(return_value=(original, "原件.html")))
        app = allow_superuser(create_offline_app())
        app.dependency_overrides[get_document_review_application] = lambda: service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            response = await client.get(f"/document-management/candidates/{uuid4()}/original")
            assert response.status_code == 200 and response.content == original
            assert response.headers["content-type"] == "application/octet-stream"
            assert response.headers["content-disposition"].startswith("attachment;")
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["cache-control"] == "no-store"
    asyncio.run(verify())
