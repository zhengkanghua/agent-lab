"""阶段 6 FreshRSS continuation 与来源级事务隔离的完全离线测试。

测试用内存 FreshRSS 页和事务暂存区模拟真实 ``r=n/r=o/c`` 契约；Repository fake 只在
commit 时发布文档与 checkpoint，rollback 会清空暂存。测试不访问 FreshRSS、PostgreSQL、
Ollama 或 Qdrant，也不打印文章正文。
"""

import asyncio
from dataclasses import dataclass, field
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

import pytest
import httpx
from pydantic import SecretStr

import agent_lab.services.freshrss_import_service as import_module
from agent_lab.config.freshrss import FreshRSSSettings
from agent_lab.domain.source_document import SourceDocument, SourceInfo
from agent_lab.ingestion.freshrss_client import FreshRSSConnectionError
from agent_lab.ingestion.freshrss_client import FreshRSSClient
from agent_lab.schemas.freshrss import (
    FreshRSSItem,
    FreshRSSItemIdPage,
    FreshRSSSubscription,
    freshrss_item_id_key,
)
from agent_lab.repositories.source_repository import SourceRepository
from agent_lab.services.freshrss_import_service import FreshRSSImportService


def run(coroutine: Any) -> Any:
    """执行测试协程，不依赖真实异步插件或事件循环。"""

    return asyncio.run(coroutine)


def test_freshrss_client_module_import_has_no_cross_layer_cycle() -> None:
    """直接导入客户端不能依赖 API 写 Runtime 或绕回导入 Service。"""

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from agent_lab.ingestion.freshrss_client "
                "import FreshRSSClient; print(FreshRSSClient.__name__)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "FreshRSSClient"


def test_decimal_id_page_matches_equivalent_google_reader_hex_tag() -> None:
    """真实 FreshRSS 的 IDs/contents 两种表示必须严格映射为同一文章。"""

    decimal_id = "1786671916262585"
    tag_id = "tag:google.com,2005:reader/item/000658f7f8e624b9"

    assert freshrss_item_id_key(decimal_id) == freshrss_item_id_key(tag_id)


def test_client_sends_real_continuation_parameters_and_validates_page() -> None:
    """离线核对 FreshRSS ``n/r/c`` 请求参数，不依赖当前实例。"""

    client = FreshRSSClient(settings())
    calls: list[dict[str, Any]] = []

    async def fake_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
        calls.append({"method": method, "path": path, **kwargs})
        return httpx.Response(
            200,
            json={
                "itemRefs": [{"id": "100"}],
                "continuation": "101",
            },
            request=httpx.Request(method, "https://example.com"),
        )

    client._request = fake_request  # type: ignore[method-assign]  # noqa: SLF001
    try:
        page = run(
            client.fetch_subscription_item_id_page(
                subscription_id="feed/1",
                limit=1,
                continuation="099",
                order="oldest",
            )
        )
    finally:
        run(client.close())

    assert page.item_ids == ("100",)
    assert page.continuation == "101"
    assert calls[0]["params"] == {
        "s": "feed/1",
        "n": "1",
        "r": "o",
        "output": "json",
        "c": "99",
    }


@dataclass(slots=True)
class MemoryStore:
    """保存仅在 fake commit 后可见的来源、游标、文档和 revision。"""

    source_ids: dict[str, UUID] = field(default_factory=dict)
    checkpoints: dict[str, str | None] = field(default_factory=dict)
    documents: dict[tuple[str, str], SourceDocument] = field(default_factory=dict)
    revisions: dict[tuple[str, str], int] = field(default_factory=dict)
    fail_commit_once: set[str] = field(default_factory=set)
    fail_document_upsert: set[str] = field(default_factory=set)

    def source_id(self, external_id: str) -> UUID:
        """为来源生成与执行次数无关的稳定测试 UUID。"""

        return uuid5(UUID(int=0), external_id)

    def install_checkpoint(self, external_id: str, checkpoint: str) -> None:
        """预置一个已经成功提交的来源游标。"""

        self.source_ids[external_id] = self.source_id(external_id)
        self.checkpoints[external_id] = checkpoint


class FakeSession:
    """把来源页写入暂存到 commit，并在 rollback 时完整丢弃。"""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.pending_source: tuple[str, UUID] | None = None
        self.pending_documents: dict[tuple[str, str], SourceDocument] = {}
        self.pending_checkpoint: tuple[str, str] | None = None
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        """原子发布当前来源页，或注入一次 PostgreSQL commit 失败。"""

        external_id = self._pending_external_id()
        if external_id in self.store.fail_commit_once:
            self.store.fail_commit_once.remove(external_id)
            raise RuntimeError("数据库响应体必须保持私密")

        if self.pending_source is not None:
            source_external_id, source_id = self.pending_source
            self.store.source_ids[source_external_id] = source_id
            self.store.checkpoints.setdefault(source_external_id, None)
        for key, document in self.pending_documents.items():
            existing = self.store.documents.get(key)
            if existing is None:
                self.store.revisions[key] = 1
            elif existing.content_text != document.content_text:
                self.store.revisions[key] += 1
            self.store.documents[key] = document
        if self.pending_checkpoint is not None:
            source_external_id, checkpoint = self.pending_checkpoint
            self.store.checkpoints[source_external_id] = checkpoint
        self.commit_count += 1
        self._clear()

    async def rollback(self) -> None:
        """丢弃未提交来源页，模拟 PostgreSQL 事务回滚。"""

        self.rollback_count += 1
        self._clear()

    def _pending_external_id(self) -> str:
        """取得当前事务关联来源，供失败注入使用。"""

        if self.pending_source is not None:
            return self.pending_source[0]
        if self.pending_checkpoint is not None:
            return self.pending_checkpoint[0]
        if self.pending_documents:
            return next(iter(self.pending_documents))[0]
        return ""

    def _clear(self) -> None:
        """清空当前事务暂存，不修改已提交状态。"""

        self.pending_source = None
        self.pending_documents.clear()
        self.pending_checkpoint = None


class FakeSourceRepository:
    """在 ``MemoryStore`` 中实现来源业务键和条件 checkpoint 更新。"""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def get_by_business_key(
        self,
        *,
        provider: str,
        external_id: str,
    ) -> Any:
        """返回已提交来源；provider 仅用于验证生产调用已传入。"""

        assert provider == "freshrss_test"
        source_id = self.session.store.source_ids.get(external_id)
        if source_id is None:
            return None
        return SimpleNamespace(
            id=source_id,
            sync_checkpoint=self.session.store.checkpoints.get(external_id),
        )

    async def upsert(self, source: SourceInfo) -> Any:
        """暂存来源，返回稳定主键但不立即发布。"""

        source_id = self.session.store.source_ids.get(
            source.external_id,
            self.session.store.source_id(source.external_id),
        )
        self.session.pending_source = (source.external_id, source_id)
        return SimpleNamespace(id=source_id)

    async def update_sync_checkpoint(
        self,
        *,
        source_id: UUID,
        expected_checkpoint: str | None,
        new_checkpoint: str,
    ) -> bool:
        """仅在已提交旧值匹配时暂存新 checkpoint。"""

        external_id = self._external_id_for(source_id)
        current = self.session.store.checkpoints.get(external_id)
        if current != expected_checkpoint:
            return False
        self.session.pending_checkpoint = (external_id, new_checkpoint)
        return True

    def _external_id_for(self, source_id: UUID) -> str:
        """从当前暂存或已提交来源反查测试外部 ID。"""

        if self.session.pending_source is not None:
            external_id, pending_id = self.session.pending_source
            if pending_id == source_id:
                return external_id
        return next(
            external_id
            for external_id, stored_id in self.session.store.source_ids.items()
            if stored_id == source_id
        )


class FakeDocumentRepository:
    """暂存文档 upsert，并可按来源注入 PostgreSQL 失败。"""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def upsert(self, document: SourceDocument, *, source_id: UUID) -> Any:
        """按来源与文章 ID 暂存文档，不修改 processing/revision 真实实现。"""

        external_id = document.source.external_id
        assert source_id == self.session.store.source_id(external_id)
        if external_id in self.session.store.fail_document_upsert:
            raise RuntimeError("数据库 URL 和语句必须保持私密")
        key = (external_id, document.external_id)
        self.session.pending_documents[key] = document
        return SimpleNamespace(id=uuid5(source_id, document.external_id))


class FakeFreshRSSClient:
    """按数字文章序号实现 FreshRSS newest/oldest continuation 行为。"""

    def __init__(
        self,
        articles: dict[str, list[int]],
        *,
        fail_sources: set[str] | None = None,
        item_titles: dict[str, str] | None = None,
    ) -> None:
        self.articles = articles
        self.fail_sources = fail_sources or set()
        self.item_titles = item_titles or {}
        self.calls: list[tuple[str, int, str | None, str]] = []

    async def __aenter__(self) -> "FakeFreshRSSClient":
        """返回当前内存客户端。"""

        return self

    async def __aexit__(self, *_args: Any) -> None:
        """内存客户端没有连接池需要关闭。"""

        return None

    async def fetch_subscriptions(self) -> list[FreshRSSSubscription]:
        """返回全部属于测试白名单分类的订阅。"""

        return [
            FreshRSSSubscription.model_validate(
                {
                    "id": source_id,
                    "title": f"Source {source_id}",
                    "url": f"https://example.com/{source_id}.xml",
                    "htmlUrl": f"https://example.com/{source_id}",
                    "categories": [{"id": "label/test", "label": "测试"}],
                }
            )
            for source_id in self.articles
        ]

    async def fetch_subscription_item_id_page(
        self,
        *,
        subscription_id: str,
        limit: int,
        continuation: str | None = None,
        order: str = "newest",
    ) -> FreshRSSItemIdPage:
        """返回有界 ID 页；oldest 只返回 continuation 之后的文章。"""

        self.calls.append((subscription_id, limit, continuation, order))
        if subscription_id in self.fail_sources:
            raise FreshRSSConnectionError("私有主机地址不得进入结果集")
        numbers = sorted(self.articles[subscription_id])
        if order == "oldest":
            assert continuation is not None
            selected = [number for number in numbers if number > int(continuation)][:limit]
        else:
            selected = list(reversed(numbers))[:limit]
        item_ids = tuple(f"{subscription_id}/item/{number}" for number in selected)
        page_continuation = str(selected[-1]) if selected else continuation
        return FreshRSSItemIdPage(
            item_ids=item_ids,
            continuation=page_continuation,
        )

    async def fetch_items(self, item_ids: tuple[str, ...]) -> list[FreshRSSItem]:
        """为请求 ID 构造最小有效 FreshRSS 正文对象。"""

        return [self._item(item_id) for item_id in item_ids]

    def _item(self, item_id: str) -> FreshRSSItem:
        """构造一篇不含外部 I/O 的协议文章。"""

        source_id, _, number = item_id.partition("/item/")
        title = self.item_titles.get(item_id, f"Title {number}")
        return FreshRSSItem.model_validate(
            {
                "id": item_id,
                "title": title,
                "published": 1_700_000_000 + int(number),
                "timestampUsec": str((1_700_000_000 + int(number)) * 1_000_000),
                "alternate": [
                    {"href": f"https://example.com/{source_id}/{number}"}
                ],
                "content": {"content": f"<p>Article body {number}.</p>"},
                "origin": {
                    "streamId": source_id,
                    "title": f"Source {source_id}",
                    "htmlUrl": f"https://example.com/{source_id}",
                },
            }
        )


def settings() -> FreshRSSSettings:
    """构造不读取本地 .env 的 FreshRSS 测试配置。"""

    return FreshRSSSettings(
        provider_key="freshrss_test",
        api_base_url="https://freshrss.example/api/",
        username="test-user",
        api_password=SecretStr("test-password"),
        sync_categories=("测试",),
    )


def service_for(client: FakeFreshRSSClient) -> FreshRSSImportService:
    """创建注入内存客户端的增量导入 Service。"""

    return FreshRSSImportService(
        settings(),
        client_factory=lambda _settings: client,  # type: ignore[arg-type,return-value]
    )


@pytest.fixture(autouse=True)
def fake_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    """让本文件所有导入测试使用 commit-aware 内存 Repository。"""

    monkeypatch.setattr(import_module, "SourceRepository", FakeSourceRepository)
    monkeypatch.setattr(import_module, "DocumentRepository", FakeDocumentRepository)


def test_checkpoint_pages_do_not_lose_news_between_bounded_manual_runs() -> None:
    store = MemoryStore()
    session = FakeSession(store)
    client = FakeFreshRSSClient({"feed/1": [1, 2, 3]})
    service = service_for(client)

    first = run(service.import_recent_per_source(session, limit_per_source=2))
    assert first.synchronized_count == 2
    assert store.checkpoints["feed/1"] == "3"
    assert set(store.documents) == {
        ("feed/1", "feed/1/item/2"),
        ("feed/1", "feed/1/item/3"),
    }

    client.articles["feed/1"].extend([4, 5, 6, 7])
    second = run(service.import_recent_per_source(session, limit_per_source=2))
    assert second.synchronized_count == 2
    assert store.checkpoints["feed/1"] == "5"
    third = run(service.import_recent_per_source(session, limit_per_source=2))
    assert third.synchronized_count == 2
    assert store.checkpoints["feed/1"] == "7"
    assert {
        external_id for source_id, external_id in store.documents if source_id == "feed/1"
    } >= {f"feed/1/item/{number}" for number in range(2, 8)}

    repeated = run(service.import_recent_per_source(session, limit_per_source=2))
    assert repeated.synchronized_count == 0
    assert repeated.checkpoint_advanced_count == 0
    assert store.checkpoints["feed/1"] == "7"
    assert any(call == ("feed/1", 2, "3", "oldest") for call in client.calls)
    assert any(call == ("feed/1", 2, "5", "oldest") for call in client.calls)
    assert all(limit <= 2 for _source, limit, _continuation, _order in client.calls)


def test_checkpoint_is_not_published_when_page_commit_fails() -> None:
    store = MemoryStore(fail_commit_once={"feed/1"})
    session = FakeSession(store)
    client = FakeFreshRSSClient({"feed/1": [1]})
    service = service_for(client)

    failed = run(service.import_recent_per_source(session, limit_per_source=2))
    assert failed.failed_source_count == 1
    assert failed.failures[0].error_type == "RuntimeError"
    assert store.checkpoints.get("feed/1") is None
    assert store.documents == {}

    succeeded = run(service.import_recent_per_source(session, limit_per_source=2))
    assert succeeded.failed_source_count == 0
    assert store.checkpoints["feed/1"] == "1"
    assert ("feed/1", "feed/1/item/1") in store.documents


@pytest.mark.parametrize("failure_mode", ["request", "mapping", "postgresql"])
def test_request_mapping_or_postgresql_failure_never_advances_checkpoint(
    failure_mode: str,
) -> None:
    store = MemoryStore()
    store.install_checkpoint("feed/1", "1")
    session = FakeSession(store)
    fail_sources = {"feed/1"} if failure_mode == "request" else set()
    item_titles = {"feed/1/item/2": ""} if failure_mode == "mapping" else {}
    if failure_mode == "postgresql":
        store.fail_document_upsert.add("feed/1")
    client = FakeFreshRSSClient(
        {"feed/1": [1, 2]},
        fail_sources=fail_sources,
        item_titles=item_titles,
    )

    result = run(
        service_for(client).import_recent_per_source(session, limit_per_source=2)
    )

    assert result.failed_source_count == 1
    assert store.checkpoints["feed/1"] == "1"
    assert ("feed/1", "feed/1/item/2") not in store.documents


def test_one_source_failure_is_isolated_and_other_source_commits() -> None:
    store = MemoryStore()
    session = FakeSession(store)
    client = FakeFreshRSSClient(
        {"feed/failed": [1], "feed/healthy": [1]},
        fail_sources={"feed/failed"},
    )

    result = run(
        service_for(client).import_recent_per_source(session, limit_per_source=2)
    )

    assert result.source_count == 2
    assert result.successful_source_count == 1
    assert result.failed_source_count == 1
    assert result.failures[0].source_external_id == "feed/failed"
    assert result.failures[0].error_type == "FreshRSSConnectionError"
    assert "feed/failed" not in store.checkpoints
    assert store.checkpoints["feed/healthy"] == "1"
    assert ("feed/healthy", "feed/healthy/item/1") in store.documents


def test_repository_rejects_numeric_checkpoint_rewind_before_database_io() -> None:
    session = SimpleNamespace()

    with pytest.raises(ValueError, match="不能回退"):
        run(
            SourceRepository(session).update_sync_checkpoint(  # type: ignore[arg-type]
                source_id=uuid5(UUID(int=0), "feed/1"),
                expected_checkpoint="10",
                new_checkpoint="9",
            )
        )
