"""FreshRSS 订阅与完整增量页适配器；不访问数据库、不决定 KnowledgeBase 归属。"""

import asyncio

from agent_lab.config.freshrss import FreshRSSSettings
from agent_lab.domain.source_document import SourceInfo
from agent_lab.ingestion.freshrss_client import FreshRSSClient, FreshRSSProtocolError
from agent_lab.ingestion.freshrss_mapper import FreshRSSItemMapper, FreshRSSMappingError
from agent_lab.knowledge.document_contracts import SourceImportPage
from agent_lab.schemas.freshrss import FreshRSSItemIdPage, freshrss_item_id_key


class FreshRSSSourceAdapter:
    """一个同步调用复用一个客户端和订阅快照，协议细节不进入应用用例。"""

    def __init__(self, settings: FreshRSSSettings, client: FreshRSSClient):
        self._settings = settings
        self._client = client
        self._mapper = FreshRSSItemMapper()
        self._subscriptions = {}

    async def list_sources(self) -> list[SourceInfo]:
        subscriptions = await self._client.fetch_subscriptions()
        allowed = set(self._settings.sync_categories)
        self._subscriptions = {
            item.id: item for item in subscriptions
            if any(category.label.strip() in allowed for category in item.categories)
        }
        return [SourceInfo(provider=self._settings.provider_key, external_id=item.id,
                           name=item.title, feed_url=item.url, home_url=item.html_url)
                for item in self._subscriptions.values()]

    async def read_page(self, source: SourceInfo, *, expected_checkpoint: str | None, limit: int) -> SourceImportPage:
        subscription = self._subscriptions[source.external_id]
        marker_page = await self._client.fetch_subscription_item_id_page(subscription_id=subscription.id, limit=1, order="newest")
        if marker_page.item_ids and marker_page.continuation is None:
            raise FreshRSSProtocolError("FreshRSS 的最新条目页面未提供续读标记。")
        data_page = await self._client.fetch_subscription_item_id_page(
            subscription_id=subscription.id, limit=limit, continuation=expected_checkpoint,
            order="oldest" if expected_checkpoint is not None else "newest",
        )
        items = await self._client.fetch_items(data_page.item_ids)
        requested = [freshrss_item_id_key(item_id) for item_id in data_page.item_ids]
        if len(set(requested)) != len(requested):
            raise FreshRSSProtocolError("FreshRSS 条目 ID 页面包含等价的重复文章 ID。")
        by_key = {}
        for item in items:
            key = freshrss_item_id_key(item.id)
            if key in by_key:
                raise FreshRSSProtocolError("FreshRSS 条目内容中出现了重复的文章 ID。")
            by_key[key] = item
        if set(by_key) != set(requested):
            raise FreshRSSProtocolError("FreshRSS 条目内容与请求的 ID 页面不完全一致。")
        documents = []
        for key in requested:
            item = by_key[key]
            if item.origin.stream_id != subscription.id:
                raise FreshRSSMappingError("FreshRSS 条目来源与请求的订阅不一致。")
            documents.append(await asyncio.to_thread(self._mapper.map, item, subscription, provider=source.provider))
        checkpoint = self._select_checkpoint(expected_checkpoint, marker_page.continuation, data_page, limit)
        return SourceImportPage(tuple(documents), checkpoint)

    @staticmethod
    def _select_checkpoint(expected: str | None, marker: str | None, page: FreshRSSItemIdPage, limit: int) -> str | None:
        """首次保存执行前的 marker；增量满页停在页游标，未满页追到执行边界。"""
        if expected is None:
            if page.item_ids and marker is None:
                raise FreshRSSProtocolError("FreshRSS 初始页面包含条目，但没有可靠的检查点。")
            return marker
        if not page.item_ids:
            if marker not in {None, expected}:
                raise FreshRSSProtocolError("FreshRSS 标记了高级内容，但增量页面为空。")
            return expected
        checkpoint = (page.continuation or marker) if len(page.item_ids) >= limit else marker
        if checkpoint is None:
            raise FreshRSSProtocolError("FreshRSS 的增量页面没有可靠的续载机制。")
        return checkpoint
