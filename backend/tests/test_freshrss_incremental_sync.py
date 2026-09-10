"""阶段 6 FreshRSS continuation 与来源级事务隔离的完全离线测试。

测试用内存 FreshRSS 页和事务暂存区模拟真实 ``r=n/r=o/c`` 契约；Repository fake 只在
commit 时发布文档与 checkpoint，rollback 会清空暂存。测试不访问 FreshRSS、PostgreSQL、
Ollama 或 Qdrant，也不打印文章正文。
"""

import asyncio
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

import pytest
import httpx
from pydantic import SecretStr

import agent_lab.knowledge.adapters.importing as import_module
from agent_lab.config.freshrss import FreshRSSSettings
from agent_lab.domain.source_document import SourceDocument, SourceInfo
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.ingestion.freshrss_client import FreshRSSConnectionError
from agent_lab.ingestion.freshrss_client import FreshRSSClient
from agent_lab.schemas.freshrss import (
    FreshRSSItem,
    FreshRSSItemIdPage,
    FreshRSSSubscription,
    freshrss_item_id_key,
)
from agent_lab.repositories.source_repository import SourceRepository
from agent_lab.knowledge.importing import SourceImportService
from agent_lab.knowledge.composition import build_source_import_service
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, SourceIntake, SourceReception


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
    sources: dict[str, SourceInfo] = field(default_factory=dict)
    bindings: dict[str, UUID] = field(default_factory=dict)
    checkpoints: dict[str, str | None] = field(default_factory=dict)
    documents: dict[tuple[str, str], SourceDocument] = field(default_factory=dict)
    intakes: dict[tuple[str, str], SourceIntake] = field(default_factory=dict)
    states: dict[UUID, str] = field(default_factory=dict)
    objects: dict[str, bytes] = field(default_factory=dict)
    fail_commit_once: set[str] = field(default_factory=set)
    fail_checkpoint_once: set[str] = field(default_factory=set)
    fail_storage_once: set[str] = field(default_factory=set)
    fail_document_upsert: set[str] = field(default_factory=set)
    inactive_knowledge_bases: set[UUID] = field(default_factory=set)

    def source_id(self, external_id: str) -> UUID:
        """为来源生成与执行次数无关的稳定测试 UUID。"""

        return uuid5(UUID(int=0), external_id)

    def install_checkpoint(self, external_id: str, checkpoint: str) -> None:
        """预置一个已经成功提交的来源游标。"""

        self.source_ids[external_id] = self.source_id(external_id)
        self.checkpoints[external_id] = checkpoint
        # 已有 checkpoint 的来源必然完成过绑定，默认挂到新闻库模拟存量数据。
        self.bindings.setdefault(external_id, DEFAULT_NEWS_KNOWLEDGE_BASE_ID)

    def register(self, external_id: str) -> None:
        """预置一个已发现并绑定、但还没有同步基线的来源。"""

        self.source_ids[external_id] = self.source_id(external_id)
        self.checkpoints.setdefault(external_id, None)
        self.bindings.setdefault(external_id, DEFAULT_NEWS_KNOWLEDGE_BASE_ID)


class FakeSession:
    """把来源页写入暂存到 commit，并在 rollback 时完整丢弃。"""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.pending_source: tuple[str, UUID] | None = None
        self.pending_source_info: SourceInfo | None = None
        self.pending_documents: dict[tuple[str, str], SourceDocument] = {}
        self.pending_intakes: dict[tuple[str, str], SourceIntake] = {}
        self.pending_confirmations: list[UUID] = []
        self.pending_checkpoint: tuple[str, str] | None = None
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self) -> None:
        """原子发布当前来源页，或注入一次 PostgreSQL commit 失败。"""

        external_id = self._pending_external_id()
        if external_id in self.store.fail_commit_once:
            self.store.fail_commit_once.remove(external_id)
            raise RuntimeError("数据库响应体必须保持私密")
        if self.pending_checkpoint and external_id in self.store.fail_checkpoint_once:
            self.store.fail_checkpoint_once.remove(external_id)
            raise RuntimeError("checkpoint commit failed")

        if self.pending_source is not None:
            source_external_id, source_id = self.pending_source
            self.store.source_ids[source_external_id] = source_id
            self.store.checkpoints.setdefault(source_external_id, None)
            self.store.sources[source_external_id] = self.pending_source_info
        for key, document in self.pending_documents.items():
            self.store.documents[key] = document
        for key, intake in self.pending_intakes.items():
            self.store.intakes[key] = intake
            self.store.states[intake.id] = "received"
        for processing_id in self.pending_confirmations:
            if self.store.states[processing_id] == "stored":
                self.store.states[processing_id] = "pending"
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
        self.pending_source_info = None
        self.pending_documents.clear()
        self.pending_intakes.clear()
        self.pending_confirmations.clear()
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
            knowledge_base_id=self.session.store.bindings.get(external_id),
            sync_checkpoint=self.session.store.checkpoints.get(external_id),
        )

    async def upsert(self, source: SourceInfo) -> Any:
        """暂存来源，返回稳定主键但不立即发布。"""

        source_id = self.session.store.source_ids.get(
            source.external_id,
            self.session.store.source_id(source.external_id),
        )
        self.session.pending_source = (source.external_id, source_id)
        self.session.pending_source_info = source
        return SimpleNamespace(
            id=source_id,
            knowledge_base_id=self.session.store.bindings.get(source.external_id),
            sync_checkpoint=self.session.store.checkpoints.get(source.external_id),
        )

    async def get_for_update(self, source_id: UUID) -> Any:
        external_id = self._external_id_for(source_id)
        return await self.get_by_business_key(provider="freshrss_test", external_id=external_id)

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
    """暂存接收意图与整页确认；对象保存独立发生，不能被数据库回滚。"""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def prepare(
        self,
        document: SourceDocument,
        *,
        source_id: UUID,
        knowledge_base_id: UUID | None,
    ) -> Any:
        """按来源与条目 ID 暂存候选，完全相同请求复用接收意图。"""

        external_id = document.source.external_id
        assert source_id == self.session.store.source_id(external_id)
        # 文档归属必须与来源绑定一致，模拟真实 upsert 的来源分流语义。
        assert knowledge_base_id == self.session.store.bindings.get(external_id)
        if external_id in self.session.store.fail_document_upsert:
            raise RuntimeError("数据库 URL 和语句必须保持私密")
        key = (external_id, document.external_id)
        if self.session.store.documents.get(key) == document:
            intake = self.session.store.intakes[key]
            return SourceReception(intake, self.session.store.states[intake.id] in {"stored", "pending"})
        self.session.pending_documents[key] = document
        intake = SourceIntake.prepare(document_id=uuid5(source_id, document.external_id), source_kind="freshrss",
                                      data=document.raw_bytes, mime_type=document.mime_type, metadata={"title": document.title})
        self.session.pending_intakes[key] = intake
        return SourceReception(intake, False)

    async def confirm_receptions(self, processing_ids, **_scope):
        assert all(self.session.store.states[identity] in {"stored", "pending"} for identity in processing_ids)
        self.session.pending_confirmations.extend(processing_ids)

    async def refresh_source_metadata(self, *_args):
        # 此测试只验证导入编排；元数据候选在真实 PostgreSQL 验证中覆盖。
        pass


class FakeProcessing:
    def __init__(self, store):
        self.store = store

    async def store_source(self, intake, data, *, queue_processing):
        assert not queue_processing
        source = next(key[0] for key, value in self.store.intakes.items() if value.id == intake.id)
        if source in self.store.fail_storage_once:
            self.store.fail_storage_once.remove(source)
            self.store.states[intake.id] = "receiving_failed"
            raise ProcessingApplicationError("document_source_storage_failed")
        self.store.objects[intake.reference.key] = data
        self.store.states[intake.id] = "stored"


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


def service_for(client: FakeFreshRSSClient, session: FakeSession) -> SourceImportService:
    """创建注入内存客户端的增量导入 Service。"""

    @asynccontextmanager
    async def sessions():
        try:
            yield session
        finally:
            await session.rollback()

    return build_source_import_service(
        settings(), sessions,
        client_factory=lambda _settings: client,  # type: ignore[arg-type,return-value]
        processing_factory=lambda: FakeProcessing(session.store),
    )


@pytest.fixture(autouse=True)
def fake_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    """让本文件所有导入测试使用 commit-aware 内存 Repository。"""

    monkeypatch.setattr(import_module, "SourceRepository", FakeSourceRepository)
    monkeypatch.setattr(import_module, "PostgresImportDocumentRepository", FakeDocumentRepository)
    class KnowledgeBaseRepository:
        def __init__(self, session):
            self.store = session.store

        async def get(self, knowledge_base_id):
            return SimpleNamespace(id=knowledge_base_id, is_active=knowledge_base_id not in self.store.inactive_knowledge_bases)

        get_for_update = get

    monkeypatch.setattr(import_module, "PostgresKnowledgeBaseRepository", KnowledgeBaseRepository)


def test_disabled_source_is_skipped_and_reenabled_source_resumes():
    async def verify():
        store = MemoryStore()
        store.install_checkpoint("feed/1", "10")
        store.inactive_knowledge_bases.add(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)
        client = FakeFreshRSSClient({"feed/1": [11]})
        session = FakeSession(store)
        result = await service_for(client, session).import_recent_per_source()
        assert result.synchronized_count == 0
        assert result.failed_source_count == 0
        assert client.calls == []
        assert store.documents == {}
        assert store.checkpoints["feed/1"] == "10"
        store.inactive_knowledge_bases.clear()
        resumed = await service_for(client, session).import_recent_per_source()
        assert resumed.synchronized_count == 1
        assert store.checkpoints["feed/1"] == "11"

    run(verify())


def test_disable_during_network_request_discards_page_but_other_source_continues():
    async def verify():
        store = MemoryStore()
        store.install_checkpoint("feed/1", "10")
        store.install_checkpoint("feed/2", "20")
        target = UUID("10000000-0000-4000-8000-000000000011")
        store.bindings["feed/1"] = target

        class DisableDuringRead(FakeFreshRSSClient):
            async def fetch_items(self, item_ids):
                if item_ids[0].startswith("feed/1/"):
                    store.inactive_knowledge_bases.add(target)
                return await super().fetch_items(item_ids)

        client = DisableDuringRead({"feed/1": [11], "feed/2": [21]})
        result = await service_for(client, FakeSession(store)).import_recent_per_source()
        assert result.synchronized_count == 1
        assert result.checkpoint_advanced_count == 1
        assert result.failed_source_count == 0
        assert store.checkpoints == {"feed/1": "10", "feed/2": "21"}
        assert set(store.documents) == {("feed/2", "feed/2/item/21")}

    run(verify())


def test_disabled_source_cannot_advance_checkpoint_without_documents():
    store = MemoryStore()
    store.install_checkpoint("feed/1", "10")
    store.inactive_knowledge_bases.add(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)
    session = FakeSession(store)
    result = run(service_for(FakeFreshRSSClient({}), session).save_source_page(
        documents=[], existing_source_id=store.source_id("feed/1"),
        expected_checkpoint="10", new_checkpoint="11",
    ))
    assert result == (0, False)
    assert store.checkpoints["feed/1"] == "10"
    assert session.commit_count == 0


def test_checkpoint_pages_do_not_lose_news_between_bounded_manual_runs() -> None:
    store = MemoryStore()
    store.register("feed/1")
    session = FakeSession(store)
    client = FakeFreshRSSClient({"feed/1": [1, 2, 3]})
    service = service_for(client, session)

    first = run(service.import_recent_per_source(limit_per_source=2))
    assert first.synchronized_count == 2
    assert store.checkpoints["feed/1"] == "3"
    assert set(store.documents) == {
        ("feed/1", "feed/1/item/2"),
        ("feed/1", "feed/1/item/3"),
    }

    client.articles["feed/1"].extend([4, 5, 6, 7])
    second = run(service.import_recent_per_source(limit_per_source=2))
    assert second.synchronized_count == 2
    assert store.checkpoints["feed/1"] == "5"
    third = run(service.import_recent_per_source(limit_per_source=2))
    assert third.synchronized_count == 2
    assert store.checkpoints["feed/1"] == "7"
    assert {
        external_id for source_id, external_id in store.documents if source_id == "feed/1"
    } >= {f"feed/1/item/{number}" for number in range(2, 8)}

    repeated = run(service.import_recent_per_source(limit_per_source=2))
    assert repeated.synchronized_count == 0
    assert repeated.checkpoint_advanced_count == 0
    assert store.checkpoints["feed/1"] == "7"
    assert any(call == ("feed/1", 2, "3", "oldest") for call in client.calls)
    assert any(call == ("feed/1", 2, "5", "oldest") for call in client.calls)
    assert all(limit <= 2 for _source, limit, _continuation, _order in client.calls)


def test_checkpoint_is_not_published_when_page_commit_fails() -> None:
    store = MemoryStore(fail_commit_once={"feed/1"})
    store.register("feed/1")
    session = FakeSession(store)
    client = FakeFreshRSSClient({"feed/1": [1]})
    service = service_for(client, session)

    failed = run(service.import_recent_per_source(limit_per_source=2))
    assert failed.failed_source_count == 1
    assert failed.failures[0].error_type == "RuntimeError"
    assert store.checkpoints.get("feed/1") is None
    assert store.documents == {}

    succeeded = run(service.import_recent_per_source(limit_per_source=2))
    assert succeeded.failed_source_count == 0
    assert store.checkpoints["feed/1"] == "1"
    assert ("feed/1", "feed/1/item/1") in store.documents


@pytest.mark.parametrize("failure_mode", ["request", "postgresql"])
def test_request_or_postgresql_failure_never_advances_checkpoint(
    failure_mode: str,
) -> None:
    store = MemoryStore()
    store.install_checkpoint("feed/1", "1")
    session = FakeSession(store)
    fail_sources = {"feed/1"} if failure_mode == "request" else set()
    if failure_mode == "postgresql":
        store.fail_document_upsert.add("feed/1")
    client = FakeFreshRSSClient(
        {"feed/1": [1, 2]},
        fail_sources=fail_sources,
    )

    result = run(
        service_for(client, session).import_recent_per_source(limit_per_source=2)
    )

    assert result.failed_source_count == 1
    assert store.checkpoints["feed/1"] == "1"
    assert ("feed/1", "feed/1/item/2") not in store.documents


def test_empty_title_is_received_with_normal_items_and_checkpoint_advances():
    store = MemoryStore()
    store.register("feed/1")
    client = FakeFreshRSSClient({"feed/1": [1, 2]}, item_titles={"feed/1/item/1": ""})
    result = run(service_for(client, FakeSession(store)).import_recent_per_source())
    assert result.synchronized_count == 2 and result.failed_source_count == 0
    assert store.checkpoints["feed/1"] == "2"
    assert len(store.objects) == 2
    assert set(store.states.values()) == {"pending"}
    assert store.documents["feed/1", "feed/1/item/1"].title == ""


@pytest.mark.parametrize("failure_stage", ["storage", "confirmation"])
def test_failed_page_preserves_intent_and_reuses_original_on_retry(failure_stage):
    store = MemoryStore()
    store.register("feed/1")
    if failure_stage == "storage":
        store.fail_storage_once.add("feed/1")
    else:
        store.fail_checkpoint_once.add("feed/1")
    service = service_for(FakeFreshRSSClient({"feed/1": [1]}), FakeSession(store))
    failed = run(service.import_recent_per_source())
    assert failed.failed_source_count == 1 and store.checkpoints["feed/1"] is None
    assert set(store.states.values()) == {"receiving_failed" if failure_stage == "storage" else "stored"}
    before = next(iter(store.intakes.values()))
    succeeded = run(service.import_recent_per_source())
    assert succeeded.synchronized_count == 1 and succeeded.failed_source_count == 0
    assert store.checkpoints["feed/1"] == "1" and set(store.states.values()) == {"pending"}
    assert next(iter(store.intakes.values())).id == before.id
    assert list(store.objects) == [before.reference.key]


def test_one_source_failure_is_isolated_and_other_source_commits() -> None:
    store = MemoryStore()
    store.register("feed/failed")
    store.register("feed/healthy")
    session = FakeSession(store)
    client = FakeFreshRSSClient(
        {"feed/failed": [1], "feed/healthy": [1]},
        fail_sources={"feed/failed"},
    )

    result = run(
        service_for(client, session).import_recent_per_source(limit_per_source=2)
    )

    assert result.source_count == 2
    assert result.successful_source_count == 1
    assert result.failed_source_count == 1
    assert result.failures[0].source_external_id == "feed/failed"
    assert result.failures[0].error_type == "FreshRSSConnectionError"
    # register 预置的空游标必须原样保留：失败的来源不能有任何已提交 checkpoint。
    assert store.checkpoints.get("feed/failed") is None
    assert store.checkpoints["feed/healthy"] == "1"
    assert ("feed/healthy", "feed/healthy/item/1") in store.documents


def test_pending_document_deletion_rolls_back_source_page_and_checkpoint(monkeypatch):
    from agent_lab.domain.write_scope import DocumentDeletionPendingError
    store = MemoryStore()
    store.install_checkpoint("feed/blocked", "1")
    store.register("feed/healthy")
    session = FakeSession(store)
    client = FakeFreshRSSClient({"feed/blocked": [1, 2], "feed/healthy": [3]})
    original = FakeDocumentRepository.prepare

    async def guarded(repository, document, *, source_id, knowledge_base_id):
        if document.source.external_id == "feed/blocked":
            raise DocumentDeletionPendingError()
        return await original(
            repository,
            document,
            source_id=source_id,
            knowledge_base_id=knowledge_base_id,
        )

    monkeypatch.setattr(FakeDocumentRepository, "prepare", guarded)
    result = run(service_for(client, session).import_recent_per_source(limit_per_source=2))
    assert result.failed_source_count == 1
    assert result.failures[0].error_type == "DocumentDeletionPendingError"
    assert store.checkpoints["feed/blocked"] == "1"
    assert ("feed/blocked", "feed/blocked/item/2") not in store.documents
    assert store.checkpoints["feed/healthy"] == "3"


def test_new_subscription_is_registered_without_fetching_articles() -> None:
    """新发现的订阅只登记来源元数据；绑定前不请求文章、不写文档、不建游标。"""

    store = MemoryStore()
    session = FakeSession(store)
    client = FakeFreshRSSClient({"feed/new": [1, 2]})

    result = run(
        service_for(client, session).import_recent_per_source(limit_per_source=2)
    )

    assert result.source_count == 1
    assert result.failed_source_count == 0
    assert result.synchronized_count == 0
    assert result.checkpoint_advanced_count == 0
    assert "feed/new" in store.source_ids
    assert client.calls == []
    assert not any(
        external_id == "feed/new" for external_id, _item in store.documents
    )
    assert store.checkpoints.get("feed/new") is None


def test_unbound_source_is_skipped_until_binding() -> None:
    """已登记未绑定的来源被跳过：不拉取、不推进游标，绑定后从新基线开始。"""

    store = MemoryStore()
    store.source_ids["feed/unbound"] = store.source_id("feed/unbound")
    store.checkpoints["feed/unbound"] = None
    session = FakeSession(store)
    client = FakeFreshRSSClient({"feed/unbound": [1, 2]})

    result = run(
        service_for(client, session).import_recent_per_source(limit_per_source=2)
    )

    assert result.source_count == 1
    assert result.failed_source_count == 0
    assert result.synchronized_count == 0
    assert result.checkpoint_advanced_count == 0
    assert client.calls == []
    assert store.checkpoints["feed/unbound"] is None
    assert not any(
        external_id == "feed/unbound" for external_id, _item in store.documents
    )


@pytest.mark.parametrize("state", ["unbound", "inactive", "active"])
def test_subscription_metadata_updates_without_new_documents(state) -> None:
    store = MemoryStore()
    store.install_checkpoint("feed/1", "10")
    store.sources["feed/1"] = SourceInfo(provider="freshrss_test", external_id="feed/1", name="Old name")
    if state == "unbound":
        store.bindings.clear()
    elif state == "inactive":
        store.inactive_knowledge_bases.add(DEFAULT_NEWS_KNOWLEDGE_BASE_ID)
    client = FakeFreshRSSClient({"feed/1": []})

    result = run(service_for(client, FakeSession(store)).import_recent_per_source())

    assert result.failed_source_count == result.synchronized_count == result.checkpoint_advanced_count == 0
    assert store.sources["feed/1"].name == "Source feed/1"
    assert str(store.sources["feed/1"].feed_url) == "https://example.com/feed/1.xml"
    assert str(store.sources["feed/1"].home_url) == "https://example.com/feed/1"
    assert store.checkpoints["feed/1"] == "10" and store.documents == {}
    if state != "active":
        assert client.calls == []


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
