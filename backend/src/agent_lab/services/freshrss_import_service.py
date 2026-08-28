"""编排 FreshRSS 增量读取、协议映射和 PostgreSQL 幂等持久化。

本模块位于应用 Service 层。一个重要的设计决策：以「单个 FreshRSS 订阅」为失败和
事务边界——一个来源的新闻 + 它的 checkpoint（同步进度游标）放在同一个数据库事务
里原子提交；某个来源失败只回滚它自己，不影响其他来源已经提交的数据。

它只负责抓取和入库：不构建 LangChain Document/Chunk、不调用 Embedding 或 Qdrant，
也不实现自动调度和后台重试。向量化是索引 Service 的事，本模块不管。
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.config.freshrss import FreshRSSSettings
from agent_lab.domain.source_document import SourceDocument
from agent_lab.ingestion.freshrss_client import (
    FreshRSSClient,
    FreshRSSProtocolError,
)
from agent_lab.ingestion.freshrss_mapper import (
    FreshRSSItemMapper,
    FreshRSSMappingError,
)
from agent_lab.models.document import DocumentRecord
from agent_lab.models.source import SourceRecord
from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.repositories.source_repository import SourceRepository
from agent_lab.schemas.freshrss import (
    FreshRSSItem,
    FreshRSSItemIdPage,
    FreshRSSSubscription,
    freshrss_item_id_key,
)

logger = logging.getLogger(__name__)

type FreshRSSClientFactory = Callable[[FreshRSSSettings], FreshRSSClient]


@dataclass(frozen=True, slots=True)
class SourceSyncFailure:
    """描述一个来源本页同步失败的安全摘要。

    生命周期仅限本次手动执行。``source_external_id`` 用于定位 FreshRSS 订阅，
    ``error_type`` 只保存 Python 异常类型；对象不保存异常文本、响应正文或新闻内容。
    """

    source_external_id: str
    error_type: str


@dataclass(frozen=True, slots=True)
class FreshRSSImportResult:
    """汇总一次来源级增量导入的安全计数和失败类型。

    ``source_count`` 是白名单匹配的订阅数，``synchronized_count`` 包含新增、更新和
    幂等命中的文档。``checkpoint_advanced_count`` 只统计本次事务实际把游标推进到
    新值的来源；并发执行已由其他事务推进时不会重复计数。
    """

    source_count: int
    synchronized_count: int
    checkpoint_advanced_count: int
    failures: tuple[SourceSyncFailure, ...]

    @property
    def failed_source_count(self) -> int:
        """返回本次隔离失败的来源数量。"""

        return len(self.failures)

    @property
    def successful_source_count(self) -> int:
        """返回完成本页读取和事务处理的来源数量。"""

        return self.source_count - self.failed_source_count


class FreshRSSImportService:
    """以「来源」为单位编排 FreshRSS API、Mapper 与 Repository。

    实例持有进程配置和无状态 Mapper，不持有数据库 Session 或网络连接（无状态，
    可复用）。增量方法每次调用时创建短生命周期的 FreshRSS client，复用调用方给的
    Session；每个来源成功后单独 commit、失败后单独 rollback。

    增量同步的思路（理解本类最核心的一点）：
    - 首次同步：先取「执行开始时最新文章」的位置作为基线，保存最近一页，并承诺
      「从这条基线往后不漏新闻」；
    - 之后每次：用已存的 checkpoint 作为起点，从旧到新「追赶」上次之后新到的文章，
      而不是只翻最近 N 篇——这样两次手动运行之间到达的新闻不会被静默跳过；
    - ``limit_per_source`` 始终只是「单次、每个来源」的安全上限。
    """

    def __init__(
        self,
        settings: FreshRSSSettings,
        *,
        client_factory: FreshRSSClientFactory = FreshRSSClient,
    ) -> None:
        """保存已校验配置并绑定可替换的 FreshRSS client 工厂。

        Args:
            settings: 当前 FreshRSS 实例配置及同步分类白名单。
            client_factory: 根据配置创建异步上下文客户端的工厂；生产使用真实客户端，
                离线测试可注入协议一致的 fake，不会在构造阶段执行网络 I/O。
        """

        self._settings = settings
        self._mapper = FreshRSSItemMapper()
        self._client_factory = client_factory

    def _filter_allowed_subscriptions(
        self,
        subscriptions: list[FreshRSSSubscription],
    ) -> list[FreshRSSSubscription]:
        """筛选至少属于一个同步白名单分类的 FreshRSS 订阅。

        Args:
            subscriptions: FreshRSS 返回的完整订阅列表。

        Returns:
            至少属于一个 ``sync_categories`` 分类的订阅，保持原列表顺序。
        """

        allowed_categories = set(self._settings.sync_categories)
        return [
            subscription
            for subscription in subscriptions
            if any(
                category.label.strip() in allowed_categories
                for category in subscription.categories
            )
        ]

    async def save_many(
        self,
        session: AsyncSession,
        documents: Sequence[SourceDocument],
    ) -> list[DocumentRecord]:
        """在一个事务中幂等保存一批来源和文档。

        Args:
            session: 当前批次独占的 SQLAlchemy 异步 Session。
            documents: 已完成校验和清洗的统一文档；空序列不执行写 I/O。

        Returns:
            与输入顺序一致的 ORM 文档对象。

        Raises:
            Exception: 任意来源或文档保存失败时，整个批次回滚。

        Notes:
            此兼容方法不处理 checkpoint；增量同步通过 ``_save_source_page`` 把文档与
            游标放入同一事务。同步阶段从不把文档标记为 ``indexed``。
        """

        if not documents:
            return []
        try:
            source_repository = SourceRepository(session)
            document_repository = DocumentRepository(session)
            source_records: dict[tuple[str, str], SourceRecord] = {}
            document_records: list[DocumentRecord] = []
            for document in documents:
                source_key = (
                    document.source.provider,
                    document.source.external_id,
                )
                source_record = source_records.get(source_key)
                if source_record is None:
                    source_record = await source_repository.upsert(document.source)
                    source_records[source_key] = source_record
                document_records.append(
                    await document_repository.upsert(
                        document,
                        source_id=source_record.id,
                    )
                )
            await session.commit()
            return document_records
        except Exception:
            await session.rollback()
            raise

    async def import_recent_per_source(
        self,
        session: AsyncSession,
        *,
        limit_per_source: int = 2,
    ) -> FreshRSSImportResult:
        """可靠地同步每个白名单来源的一页新闻并推进 checkpoint。

        Args:
            session: 本次同步独占的 SQLAlchemy 异步 Session。方法按来源提交或回滚，
                不会跨并发 Task 共享它。
            limit_per_source: 每个允许来源本次最多处理的文章数，必须大于零。

        Returns:
            来源、文档、游标推进和来源级失败组成的安全汇总。

        Raises:
            ValueError: ``limit_per_source`` 小于一。
            FreshRSSError: 订阅列表这一批次级请求失败，因无法确定来源边界而终止。
            Exception: Session 无法在来源失败后 rollback 等批次级数据库故障。

        Notes:
            本方法执行 FreshRSS 只读网络 I/O 和 PostgreSQL 写入。首次同步以执行开始时
            最新 continuation 建立基线；已有 checkpoint 时使用 ``r=o&c=...`` 从旧到
            新追赶。请求、映射或持久化失败不会推进该来源 checkpoint；其他来源继续。
            新建或变化的文档仍由 Repository 保持 ``pending``，必须再经索引 Service。
        """

        if limit_per_source < 1:
            raise ValueError("limit_per_source 必须大于零")

        failures: list[SourceSyncFailure] = []
        synchronized_count = 0
        checkpoint_advanced_count = 0
        async with self._client_factory(self._settings) as client:
            # 拿到freshRSS所有开启的订阅列表
            subscriptions = await client.fetch_subscriptions()
            # 拿到我们配置白名单的freshRSS并且开启的订阅列表
            allowed_subscriptions = self._filter_allowed_subscriptions(subscriptions)
            for subscription in allowed_subscriptions:
                try:
                    records, checkpoint_advanced = await self._import_source_page(
                        client,
                        session,
                        subscription,
                        limit_per_source=limit_per_source,
                    )
                except Exception as exc:
                    # 不读取异常文本，避免第三方响应或正文进入日志；rollback 保证该
                    # 来源的文档和 checkpoint 都不可见，然后继续下一个来源。
                    await session.rollback()
                    error_type = type(exc).__name__
                    failures.append(
                        SourceSyncFailure(
                            source_external_id=subscription.id,
                            error_type=error_type,
                        )
                    )
                    logger.error(
                        "FreshRSS 来源同步失败 source=%s error_type=%s",
                        subscription.id,
                        error_type,
                    )
                    continue
                synchronized_count += len(records)
                checkpoint_advanced_count += int(checkpoint_advanced)

        return FreshRSSImportResult(
            source_count=len(allowed_subscriptions),
            synchronized_count=synchronized_count,
            checkpoint_advanced_count=checkpoint_advanced_count,
            failures=tuple(failures),
        )

    async def _import_source_page(
        self,
        client: FreshRSSClient,
        session: AsyncSession,
        subscription: FreshRSSSubscription,
        *,
        limit_per_source: int,
    ) -> tuple[list[DocumentRecord], bool]:
        """读取并原子保存一个来源的一页增量新闻。

        一个很关键的执行顺序（理解增量同步为何正确）：
        1. 先查数据库里该来源已提交的 checkpoint（旧游标），然后立刻 rollback 结束
           只读事务——不能在等 FreshRSS 网络响应时一直占着数据库事务；
        2. 先取「最新 marker」：以 n=1&r=n 读一页，拿到当前最新文章的游标位置；
        3. 再取「数据页」：从旧 checkpoint 之后开始读（有 checkpoint 时按旧到新
           排序追赶，首次则按最新排序建基线）；
        4. 把文档和「选出的新 checkpoint」放进同一个事务提交。

        为什么先 marker 后数据页：防止请求期间又有新文章到达，导致 checkpoint 越过
        还没来得及处理的新文章（先量好终点，再从头跑到终点）。

        Args:
            client: 当前调用复用且已配置的 FreshRSS 客户端。
            session: 当前同步独占的数据库 Session。
            subscription: 正在处理的白名单订阅。
            limit_per_source: 本页最大新闻数。

        Returns:
            已持久化记录列表，以及 checkpoint 是否实际推进。

        Raises:
            FreshRSSProtocolError: marker、分页正文或来源关联不满足可靠同步约束。
            FreshRSSMappingError: 任一文章无法映射或来源不一致。
            Exception: FreshRSS 或 PostgreSQL I/O 失败。

        Notes:
            先读取最新 marker，再读取数据页，防止请求期间新到文章被 checkpoint 越过。
            PostgreSQL 只读事务在网络 I/O 前 rollback 结束，避免等待 FreshRSS 时占用
            长事务；真正的文档与游标写入随后在单一事务内完成。
        """

        source_repository = SourceRepository(session)
        # 1. 先查该来源已提交的 checkpoint（旧游标），然后立刻结束只读事务——
        #    不能在等 FreshRSS 网络响应时一直占着数据库事务连接
        existing_source = await source_repository.get_by_business_key(
            provider=self._settings.provider_key,
            external_id=subscription.id,
        )
        expected_checkpoint = (
            existing_source.sync_checkpoint if existing_source is not None else None
        )
        existing_source_id = existing_source.id if existing_source is not None else None
        await session.rollback()

        # 2. 取「最新 marker」：n=1&r=n 读一页，得到当前最新文章的游标位置
        marker_page = await client.fetch_subscription_item_id_page(
            subscription_id=subscription.id,
            limit=1,
            order="newest",
        )
        latest_marker = self._require_marker(marker_page)
        # 3. 取「数据页」：有 checkpoint 时从旧到新追赶；首次按最新排序建基线
        data_page = await client.fetch_subscription_item_id_page(
            subscription_id=subscription.id,
            limit=limit_per_source,
            continuation=expected_checkpoint,
            order="oldest" if expected_checkpoint is not None else "newest",
        )
        # 4. 拉正文（严格一一对应）+ 映射成领域文档
        items = await self._fetch_complete_page(client, data_page)
        documents = self._map_page(items, subscription)
        # 5. 决定成功后 checkpoint 推进到哪（推错：推太远漏文章、推太近重复处理）
        new_checkpoint = self._select_new_checkpoint(
            expected_checkpoint=expected_checkpoint,
            latest_marker=latest_marker,
            data_page=data_page,
            limit_per_source=limit_per_source,
        )
        # 6. 文档 + 新 checkpoint 放进同一个事务原子提交
        return await self._save_source_page(
            session,
            documents=documents,
            existing_source_id=existing_source_id,
            expected_checkpoint=expected_checkpoint,
            new_checkpoint=new_checkpoint,
        )

    @staticmethod
    def _require_marker(page: FreshRSSItemIdPage) -> str | None:
        """取得执行边界 marker，并拒绝有文章却没有 continuation 的响应。

        Args:
            page: 以 ``n=1&r=n`` 读取的最新文章页。

        Returns:
            FreshRSS continuation；空来源返回 ``None``。

        Raises:
            FreshRSSProtocolError: 响应有文章但没有可持久化 marker。
        """

        if page.item_ids and page.continuation is None:
            raise FreshRSSProtocolError("FreshRSS 的最新条目页面未提供续读标记。")
        return page.continuation

    async def _fetch_complete_page(
        self,
        client: FreshRSSClient,
        page: FreshRSSItemIdPage,
    ) -> list[FreshRSSItem]:
        """读取 ID 页的全部正文，并严格恢复 ID 页顺序。

        Args:
            client: 当前 FreshRSS 客户端。
            page: 已校验的文章 ID 页。

        Returns:
            与 ``page.item_ids`` 一一对应且顺序一致的文章对象。

        Raises:
            FreshRSSProtocolError: contents 缺少、重复或额外返回文章。

        Notes:
            任一正文缺失都让整页失败，避免 checkpoint 越过无法映射的新闻。
        """

        # 获取rss数据
        items = await client.fetch_items(page.item_ids)
        
        requested_keys = [freshrss_item_id_key(item_id) for item_id in page.item_ids]
        if len(set(requested_keys)) != len(requested_keys):
            raise FreshRSSProtocolError(
                "FreshRSS 条目 ID 页面包含等价的重复文章 ID。"
            )

        items_by_key: dict[tuple[str, int | str], FreshRSSItem] = {}
        for item in items:
            item_key = freshrss_item_id_key(item.id)
            if item_key in items_by_key:
                raise FreshRSSProtocolError(
                    "FreshRSS 条目内容中出现了重复的文章 ID。"
                )
            items_by_key[item_key] = item
        if set(items_by_key) != set(requested_keys):
            raise FreshRSSProtocolError(
                "FreshRSS 条目内容与请求的 ID 页面不完全一致。"
            )
        return [items_by_key[item_key] for item_key in requested_keys]

    def _map_page(
        self,
        items: Sequence[FreshRSSItem],
        subscription: FreshRSSSubscription,
    ) -> list[SourceDocument]:
        """把一页协议文章映射为同一来源下的领域文档。

        Args:
            items: 已与 ID 页一一对应的 FreshRSS 文章。
            subscription: 当前请求的白名单订阅。

        Returns:
            保持页面顺序的规范化文档。

        Raises:
            FreshRSSMappingError: origin 不一致或任一文章质量不满足导入条件。
        """

        documents: list[SourceDocument] = []
        for item in items:
            if item.origin.stream_id != subscription.id:
                raise FreshRSSMappingError(
                    "FreshRSS 条目来源与请求的订阅不一致。"
                )
            documents.append(
                self._mapper.map(
                    item,
                    subscription,
                    provider=self._settings.provider_key,
                )
            )
        return documents

    @staticmethod
    def _select_new_checkpoint(
        *,
        expected_checkpoint: str | None,
        latest_marker: str | None,
        data_page: FreshRSSItemIdPage,
        limit_per_source: int,
    ) -> str | None:
        """决定「这一页处理成功后，checkpoint 该推进到哪个位置」。

        三种情况：
        - 首次同步（没有旧 checkpoint）：直接把预先取得的 latest_marker 存下来当
          基线——这页保存成功后，下次从这条基线往后追；
        - 追赶时数据页为空：说明已经追到执行边界，checkpoint 维持不变（除非 marker
          表明中间有新文章但页是空的——那是协议异常，拒绝推进）；
        - 追赶时数据页有内容：满页（= 页大小）说明可能还有下一页，停在页面返回的
          continuation 上；不满一页说明追到头了，可直接推进到 latest_marker。

        为什么这么精细：checkpoint 是「下次从哪开始」的唯一依据，推错位置要么
        漏文章（推太远）、要么反复处理（推太近）。

        Args:
            expected_checkpoint: 数据页请求前已提交的旧游标。
            latest_marker: 数据页之前取得的执行开始边界。
            data_page: 本次实际处理的 ID 页。
            limit_per_source: 调用方限定的页大小。

        Returns:
            成功持久化后可推进到的 continuation；空来源可能为 ``None``。

        Raises:
            FreshRSSProtocolError: 有文章却没有任何可靠 continuation，或 marker 表明
                有新增文章但追赶页为空。

        Notes:
            首次只承诺从本次基线向后不漏新闻，因此成功保存最近一页后直接记录预先
            取得的 marker。已有 checkpoint 时，满页停在页 continuation；不足一页
            表示已经追到执行边界，可推进到预先取得的 marker。
        """

        if expected_checkpoint is None:
            if data_page.item_ids and latest_marker is None:
                raise FreshRSSProtocolError(
                    "FreshRSS 初始页面包含条目，但没有可靠的检查点。"
                )
            return latest_marker

        if not data_page.item_ids:
            if latest_marker not in {None, expected_checkpoint}:
                raise FreshRSSProtocolError("FreshRSS 标记了高级内容，但增量页面为空。")
            return expected_checkpoint

        if len(data_page.item_ids) >= limit_per_source:
            checkpoint = data_page.continuation or latest_marker
        else:
            checkpoint = latest_marker
        if checkpoint is None:
            raise FreshRSSProtocolError("FreshRSS 的增量页面没有可靠的续载机制。")
        return checkpoint

    async def _save_source_page(
        self,
        session: AsyncSession,
        *,
        documents: Sequence[SourceDocument],
        existing_source_id: UUID | None,
        expected_checkpoint: str | None,
        new_checkpoint: str | None,
    ) -> tuple[list[DocumentRecord], bool]:
        """把一个来源的「文档 + checkpoint」放进同一个数据库事务提交。

        为什么必须同一个事务：文档保存成功但 checkpoint 没保存，下次会重复处理
        这批文章；checkpoint 保存了但文档没保存，就会漏掉文章。只有原子提交才能
        保证「要么都成、要么都不成」。

        checkpoint 更新用「条件 UPDATE」（WHERE 里带 expected_checkpoint）：只有
        数据库里的游标仍等于本次读到的旧值才覆盖，否则放弃推进。

        这一段不是冗余防御，删掉会真的丢数据或重复处理，原因是竞态窗口客观存在：
        ``_import_source_page`` 读完 expected_checkpoint 后立刻 rollback 结束只读
        事务（为了不在等 FreshRSS 时占着连接），随后要发三次网络请求（marker 页、
        数据页、正文）。这段时间 sources 行上没有任何锁，谁都可以推进游标。

        谁可能在这个窗口里推进游标：本服务没有内置调度器，``sync-news`` /
        ``run-once`` 由外部 cron、systemd timer 或运维手动触发，FreshRSS 慢时上一
        次执行还没结束下一次就已启动；多实例部署共用同一个 PostgreSQL 时同样如此。

        破坏后的后果是「重复处理」而不是「漏文章」：无条件覆盖会把先提交者的较新
        游标退回本次读到的旧值，下次执行从已经处理过的位置重新追赶，重复 upsert
        并再次把文档标成 pending，触发多余的 revision 与 Embedding 重算。注意
        ``update_sync_checkpoint`` 的「游标不能回退」校验挡不住这种情况——它比较的
        是本次读到的旧值，看不到并发者已提交的新值，只有这里的条件 UPDATE 能拦住。

        条件不满足时不算错误：文档 upsert 仍在同一事务里幂等提交，只是本次不报告
        游标推进，让先提交的那次执行结果获胜。

        Args:
            session: 当前来源事务独占的异步 Session。
            documents: 已完整映射的当前页文档。
            existing_source_id: 网络请求前查到的来源主键；首次来源为 ``None``。
            expected_checkpoint: 网络请求前查到的旧游标。
            new_checkpoint: 当前页成功后可提交的新游标；空来源可为 ``None``。

        Returns:
            已保存记录列表，以及 checkpoint 是否从旧值推进到新值。

        Raises:
            FreshRSSProtocolError: 首次来源没有文档却出现不可关联的 checkpoint。
            Exception: 任一 PostgreSQL 写入或 commit 失败；方法先 rollback 再传播。

        Notes:
            本方法执行 PostgreSQL 写入并自行 commit / rollback。同步不会修改既有
            processing/revision 并发规则。
        """

        # 没有文档也没推进需求：无事可做，直接返回
        if not documents and new_checkpoint == expected_checkpoint:
            return [], False
        try:
            source_repository = SourceRepository(session)
            document_repository = DocumentRepository(session)
            # 1. 确定来源主键：有文档就 upsert 来源；没有文档但有旧来源就用旧主键
            if documents:
                source_record = await source_repository.upsert(documents[0].source)
                source_id = source_record.id
            elif existing_source_id is not None:
                source_id = existing_source_id
            else:
                raise FreshRSSProtocolError(
                    "FreshRSS 为没有任何文档的来源提供了检查点。"
                )

            # 2. 幂等保存每篇文档（同 external_id 存在则更新、不存在则插入）
            records = [
                await document_repository.upsert(document, source_id=source_id)
                for document in documents
            ]
            # 3. 条件推进 checkpoint（WHERE 带旧值，并发已改则不覆盖）
            checkpoint_advanced = False
            if new_checkpoint is not None and new_checkpoint != expected_checkpoint:
                checkpoint_advanced = await source_repository.update_sync_checkpoint(
                    source_id=source_id,
                    expected_checkpoint=expected_checkpoint,
                    new_checkpoint=new_checkpoint,
                )
            # 4. 一次性提交：文档和 checkpoint 要么都成、要么都不成
            await session.commit()
            return records, checkpoint_advanced
        except Exception:
            await session.rollback()
            raise


__all__ = [
    "FreshRSSImportResult",
    "FreshRSSImportService",
    "SourceSyncFailure",
]
