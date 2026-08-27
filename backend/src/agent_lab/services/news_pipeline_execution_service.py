"""执行一次「有界」的 FreshRSS 同步，或一批待处理新闻的向量索引。

将数据从FreshRSS拉取到pg数据库

本模块是 CLI 命令（sync-news / index-pending / run-once）背后的执行器：负责为每个
任务开短生命周期的数据库 Session、读取索引候选、回收超时任务、逐篇调用
DocumentIndexingService。它不创建 Qdrant Collection/Alias、不实现 HTTP/定时调度/
常驻 Worker/WebSocket，也不把 Chunk 或 Embedding 写进 PostgreSQL；外部客户端
（Ollama/Qdrant/FreshRSS）的生命周期由装配根统一管理。
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.services.document_indexing_service import (
    DocumentIndexingService,
)
from agent_lab.services.freshrss_import_service import FreshRSSImportService
from agent_lab.services.freshrss_import_service import SourceSyncFailure

logger = logging.getLogger(__name__)

type AsyncSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
type UtcClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class NewsSyncExecutionResult:
    """报告一次 FreshRSS 增量同步的来源、文档、游标和失败统计。

    结果只存在于命令进程内。``synchronized_count`` 包含新增、实际更新和幂等命中的
    文档，因为 Repository 不伪造无法可靠区分的“新增数量”。失败只保留来源外部 ID
    和异常类型，不包含标题、正文、异常文本、FreshRSS 凭据或数据库连接信息。
    """

    synchronized_count: int
    source_count: int = 0
    successful_source_count: int = 0
    checkpoint_advanced_count: int = 0
    failures: tuple[SourceSyncFailure, ...] = ()

    @property
    def failed_source_count(self) -> int:
        """返回本次隔离失败、checkpoint 未推进的来源数量。"""

        return len(self.failures)


@dataclass(frozen=True, slots=True)
class IndexExecutionFailure:
    """记录单篇索引失败的安全身份和异常类型，不保存异常文本。"""

    document_id: UUID
    error_type: str


@dataclass(frozen=True, slots=True)
class PendingIndexExecutionResult:
    """报告一个有界索引批次的候选、成功、跳过和失败数量。

    ``candidate_count`` 是本批读取到的上限内候选数；多个进程竞争时，候选可能在真正
    claim 前被其他进程领取，因此 ``skipped_count`` 是正常并发结果。失败详情只保留
    document UUID 和异常类型，完整异常、正文及 Vector 不进入命令输出。
    """

    candidate_count: int
    requeued_stale_count: int
    indexed_count: int
    skipped_count: int
    failures: tuple[IndexExecutionFailure, ...]

    @property
    def failed_count(self) -> int:
        """返回本批捕获并安全报告的单篇失败数量。"""

        return len(self.failures)


class NewsPipelineExecutionService:
    """把「同步」和「索引」组织成一次性、有界、可重复执行的批次。

    实例只存在于单个 CLI 进程里，持有进程级 Session factory 和可注入的时钟，但
    不持有数据库 Session、任何外部 client 或任务状态。

    为什么第一版「顺序」处理候选而不是并发：SQLAlchemy 的 AsyncSession 不能跨并发
    Task 共享，而项目的真实并发量和容量还没测量过。先保证正确再优化性能——未来
    若要加并发，每篇仍必须用独立 Session，并继续靠数据库的原子 claim 解决多
    Worker 竞争（这是已经打好的并发地基）。
    """

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        clock: UtcClock | None = None,
    ) -> None:
        """绑定 Session factory 和可测试时钟，不执行外部 I/O。

        Args:
            session_factory: 每次调用返回独立异步 Session 上下文的工厂。
            clock: 返回带时区当前时间的函数；生产默认使用 UTC，测试可注入固定值。

        Notes:
            构造过程不执行 PostgreSQL、FreshRSS、Embedding 或 Qdrant I/O，也不写数据。
        """

        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sync_news(
        self,
        import_service: FreshRSSImportService,
        *,
        limit_per_source: int,
    ) -> NewsSyncExecutionResult:
        """同步每个白名单来源的最近新闻，幂等保存到 PostgreSQL。

        这一步只管「抓取 + 入库」，不做向量化：新入库的文档会被标成 pending 状态，
        等下一步 index_pending 把它们变成向量写进 Qdrant。把两步分开的好处——
        同步失败不影响已经索引好的数据，索引出问题也不会阻塞抓新文章。

        Args:
            import_service: 已绑定 FreshRSS 配置的导入 Service。
            limit_per_source: 每个允许来源最多读取的新闻数，必须大于零。

        Returns:
            来源、文档、游标推进与失败类型组成的安全汇总。

        Raises:
            ValueError: ``limit_per_source`` 小于一。
            Exception: 订阅列表、配置或无法归属到单个来源的批次级操作失败。来源内
                请求、映射和 PostgreSQL 失败由 Import Service 隔离并写入结果。

        Notes:
            本方法执行 FreshRSS 只读网络 I/O 和 PostgreSQL 业务写入；不生成 Embedding、
            不访问 Qdrant，也不直接执行向量索引。新建或变更文档由 Repository 标为
            ``pending``，随后可由 ``index_pending`` 领取。
        """

        if limit_per_source < 1:
            raise ValueError("limit_per_source 必须大于零.")
        async with self._session_factory() as session:
            import_result = await import_service.import_recent_per_source(
                session,
                limit_per_source=limit_per_source,
            )
        logger.info(
            "FreshRSS 同步完成 sources=%d documents=%d failed_sources=%d",
            import_result.source_count,
            import_result.synchronized_count,
            import_result.failed_source_count,
        )
        return NewsSyncExecutionResult(
            synchronized_count=import_result.synchronized_count,
            source_count=import_result.source_count,
            successful_source_count=import_result.successful_source_count,
            checkpoint_advanced_count=import_result.checkpoint_advanced_count,
            failures=import_result.failures,
        )

    async def index_pending(
        self,
        indexing_service: DocumentIndexingService,
        *,
        batch_size: int,
        stale_after: timedelta,
    ) -> PendingIndexExecutionResult:
        """回收「卡死」的任务，然后顺序处理一批 pending/failed 新闻。

        处理前先做一件事：把 processing 状态超过 stale_after 的文档重新放回 pending。
        为什么需要：如果某个 Worker 处理到一半崩溃了，文档会永远卡在 processing
        状态——没有这一步，它就再也不会被索引了（stale 回收就是给这种「僵尸任务」
        收尸）。

        逐篇处理时，每篇用独立 Session 调 index_document；单篇失败只记录
        document_id + 异常类型，然后继续下一篇，绝不让一篇坏文章拖死整批。

        Args:
            indexing_service: 已绑定 Chunk、Ollama 和 current Alias Point Store 的服务。
            batch_size: 本次最多读取的候选文档数量，必须大于零。
            stale_after: ``processing`` 状态超过该正时长后可重新排队。

        Returns:
            候选、回收、成功、竞争跳过和安全失败明细组成的批次结果。

        Raises:
            ValueError: 批量大小或 stale lease 不为正，或注入时钟没有时区。
            Exception: 候选读取或 stale requeue 的 PostgreSQL I/O 失败。单篇索引失败会
                进入 ``failures`` 并继续后续候选，不会被静默转换为成功。

        Notes:
            本方法执行 PostgreSQL 读写、Ollama Embedding 和 Qdrant current Alias Point
            写入；不执行 Vector Search、不创建 Collection/Alias，也不循环读取第二批。
            每篇候选使用独立 Session，Service 内部在网络 I/O 前后提交短状态事务。
        """

        if batch_size < 1:
            raise ValueError("batch_size 必须大于零")
        if stale_after <= timedelta(0):
            raise ValueError("stale_after 必须大于零")
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("执行时钟必须返回一个包含时区信息的 datetime 对象。")

        async with self._session_factory() as session:
            repository = DocumentRepository(session)
            # 1. 回收「僵尸任务」：把 processing 超过阈值的文档重新放回 pending
            requeued_count = await repository.requeue_stale_processing(
                started_before=now - stale_after,
            )
            # 2. 领取本批候选（只是只读 list；真正的原子领取发生在 index_document 里）
            candidate_ids = await repository.list_index_candidate_ids(
                limit=batch_size,
            )

        # 3. 逐篇索引：每篇用独立 Session；单篇失败只记录，继续下一篇
        indexed_count = 0
        skipped_count = 0
        failures: list[IndexExecutionFailure] = []
        for document_id in candidate_ids:
            try:
                async with self._session_factory() as session:
                    result = await indexing_service.index_document(session, document_id)
                if result.indexed:
                    indexed_count += 1
                    logger.info("已索引文档 id=%s", document_id)
                elif result.skipped:
                    skipped_count += 1
                    logger.info(
                        "认领冲突后跳过文档 id=%s", document_id
                    )
                else:
                    raise RuntimeError(
                        "DocumentIndexingService 既没有返回 indexed 也没有返回 skipped"
                    )
            except Exception as exc:
                # Service 已尽力把当前 revision 标成 failed；批次只记录类型和安全 UUID，
                # 不调用 str(exc)，避免第三方响应、正文或凭据进入调度日志。
                error_type = type(exc).__name__
                failures.append(
                    IndexExecutionFailure(
                        document_id=document_id,
                        error_type=error_type,
                    )
                )
                logger.error(
                    "文档索引写入失败 id=%s error_type=%s",
                    document_id,
                    error_type,
                )

        return PendingIndexExecutionResult(
            candidate_count=len(candidate_ids),
            requeued_stale_count=requeued_count,
            indexed_count=indexed_count,
            skipped_count=skipped_count,
            failures=tuple(failures),
        )


__all__ = [
    "IndexExecutionFailure",
    "NewsPipelineExecutionService",
    "NewsSyncExecutionResult",
    "PendingIndexExecutionResult",
]
