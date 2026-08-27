"""GET /documents/{document_id} 的 PostgreSQL 详情契约测试。

通过 FastAPI dependency override 注入 fake Repository；测试不会连接项目数据库，也不
读取任何环境密钥。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from news_vector_service.api.documents import get_document_repository
from news_vector_service.main import create_app
from tests.auth_helpers import allow_reader, skip_environment_admin_sync


def run(coroutine: Any) -> Any:
    """执行异步测试协程。"""

    return asyncio.run(coroutine)


class FakeRuntime:
    """只提供搜索 Runtime 生命周期接口，避免构造真实上游客户端。"""

    def __init__(self) -> None:
        self.service = SimpleNamespace()

    async def close(self) -> None:
        """不执行外部 I/O。"""


def record(*, source: Any = "available") -> Any:
    """构造包含完整正文和 source relationship 的最小 ORM 形状。"""

    source_value = (
        None
        if source is None
        else SimpleNamespace(name="测试来源")
        if source == "available"
        else source
    )
    return SimpleNamespace(
        id=uuid4(),
        content_hash="a" * 64,
        index_revision=4,
        title="政策利率维持不变",
        url="https://example.com/news",
        source=source_value,
        published_at=datetime(2026, 8, 14, tzinfo=UTC),
        authors=["作者甲"],
        labels=["宏观", "利率"],
        content_text="第一段正文。\n\n第二段正文。",
    )


class FakeRepository:
    """按测试配置返回文档、None 或数据库异常。"""

    def __init__(self, value: Any) -> None:
        self.value = value
        self.calls: list[Any] = []

    async def get_with_source(self, document_id: Any) -> Any:
        """记录 UUID 并返回预置值。"""

        self.calls.append(document_id)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def build_app(repository: FakeRepository) -> FastAPI:
    """创建带 Repository override 的测试应用。"""

    app = allow_reader(
        create_app(  # type: ignore[arg-type]
            runtime_factory=FakeRuntime,
            environment_admin_sync=skip_environment_admin_sync,
        )
    )
    app.dependency_overrides[get_document_repository] = lambda: repository
    return app


async def request(app: FastAPI, path: str) -> httpx.Response:
    """在 lifespan 中请求详情接口。"""

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)


def test_document_detail_returns_full_plain_text_and_metadata() -> None:
    item = record()
    repository = FakeRepository(item)
    response = run(request(build_app(repository), f"/documents/{item.id}"))

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "document_id": str(item.id),
        "content_hash": "a" * 64,
        "revision": 4,
        "title": "政策利率维持不变",
        "url": "https://example.com/news",
        "source_name": "测试来源",
        "published_at": "2026-08-14T00:00:00Z",
        "authors": ["作者甲"],
        "labels": ["宏观", "利率"],
        "content_text": "第一段正文。\n\n第二段正文。",
    }
    assert repository.calls == [item.id]


def test_document_detail_missing_document_and_source_are_stable_404s() -> None:
    missing = FakeRepository(None)
    missing_response = run(request(build_app(missing), f"/documents/{uuid4()}"))
    assert missing_response.status_code == 404
    assert missing_response.json() == {"detail": "文档不存在。"}

    without_source = record(source=None)
    source_response = run(request(build_app(FakeRepository(without_source)), f"/documents/{without_source.id}"))
    assert source_response.status_code == 404
    assert source_response.json() == {"detail": "文档不存在。"}


def test_document_detail_database_failure_is_sanitized() -> None:
    repository = FakeRepository(SQLAlchemyError("postgres://secret"))
    response = run(request(build_app(repository), f"/documents/{uuid4()}"))

    assert response.status_code == 503
    assert response.json() == {
        "detail": "文档服务不可用。",
    }
    assert "postgres" not in response.text


def test_document_detail_invalid_uuid_uses_global_sanitized_422() -> None:
    repository = FakeRepository(None)
    response = run(request(build_app(repository), "/documents/not-a-uuid"))

    assert response.status_code == 422
    assert "not-a-uuid" not in response.text
    assert repository.calls == []
