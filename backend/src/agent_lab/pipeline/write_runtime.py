"""按需组装一次同步、索引或清理所需的写入 Runtime（工具箱）。

本模块位于“装配根”和“应用 Service”之间：它把 NewsPipelineExecutionService（编排
同步/索引批次）、SourceImportService（导入）和 DocumentIndexingRuntime（写 Qdrant）
三个已有组件打包成一个可调用的写工具箱。

它不暴露 Vector Search（搜索由独立的只读 VectorSearchRuntime 负责）。
也不实现后台任务、队列或调度，构造时保存工厂，不做外部 I/O。
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from qdrant_client import AsyncQdrantClient

from agent_lab.config.freshrss import FreshRSSSettings
from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.qdrant.runtime import DocumentIndexingRuntime
from agent_lab.qdrant.lifecycle import build_qdrant_client
from agent_lab.qdrant.store import QdrantDeletionStore
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository
from agent_lab.services.document_retention_service import DocumentRetentionService
from agent_lab.services.write_coordination import WriteCoordinator
from agent_lab.knowledge.importing import SourceImportService
from agent_lab.knowledge.adapters.documents import postgres_indexing_work
from agent_lab.services.news_pipeline_execution_service import (
    NewsPipelineExecutionService,
    NewsSyncExecutionResult,
    PendingIndexExecutionResult,
)


type AsyncSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True, slots=True)
class PipelineRunOnceExecutionResult:
    """保存一次手动流水线的同步和索引 Service 结果。

    对象只在当前同步 HTTP/CLI 调用中存在，不持久化运行历史。两个子结果只包含计数、
    UUID 和异常类型，不含新闻正文、Vector、凭据、连接地址或第三方响应。
    """

    sync: NewsSyncExecutionResult
    index: PendingIndexExecutionResult


@dataclass(slots=True)
class PipelineWriteRuntime:
    """按需持有一次 HTTP、CLI 或定时任务执行所需的写入组件。

    生命周期 = 一次工作单元，用完即整体关闭：
    - ``build/lazy`` 只保存所需工厂（不连外部服务）；
    - ``run_once`` 依次做 FreshRSS/PostgreSQL 同步 → 准备 Qdrant Alias → 索引；
    - ``close`` 释放写入 client。

    它没有搜索 Service，因此不会污染只读 VectorSearchRuntime 的权限边界。
    """

    executor: NewsPipelineExecutionService
    import_service: SourceImportService | None = None
    indexing_runtime: DocumentIndexingRuntime | None = None
    import_factory: Callable | None = None
    indexing_factory: Callable | None = None
    retention_settings_factory: Callable | None = None
    session_factory: AsyncSessionFactory | None = None
    retention_client: AsyncQdrantClient | None = None

    @classmethod
    def lazy(cls, *, session_factory, freshrss_factory, indexing_factory, qdrant_settings_factory):
        """保存工厂，执行所需步骤时才创建客户端；构造本身不读取上游配置。"""
        return cls(
            executor=NewsPipelineExecutionService(partial(postgres_indexing_work, session_factory), coordinator=WriteCoordinator(session_factory)),
            import_factory=freshrss_factory, indexing_factory=indexing_factory,
            retention_settings_factory=qdrant_settings_factory, session_factory=session_factory,
        )

    @classmethod
    def build(
        cls,
        *,
        session_factory: AsyncSessionFactory,
        freshrss_settings: FreshRSSSettings,
        qdrant_settings: QdrantSettings,
        ollama_settings: OllamaEmbeddingSettings,
    ) -> "PipelineWriteRuntime":
        """从已校验配置组装手动写入 Runtime，不执行外部 I/O。

        Args:
            session_factory: 为同步和每篇索引创建独立异步 Session 的工厂。
            freshrss_settings: FreshRSS 地址、凭据、超时和分类白名单。
            qdrant_settings: current Alias、Collection 规格和 Qdrant 连接配置。
            ollama_settings: 文档 Embedding 模型、维度、批量和连接配置。

        Returns:
            尚未访问 FreshRSS、PostgreSQL、Ollama 或 Qdrant 的写入 Runtime。

        Raises:
            ValueError: 向量规格配置不一致。
            VectorIndexConfigurationError: 组件无法共享同一向量规格。
        """

        from agent_lab.knowledge.composition import build_source_import_service

        return cls.lazy(
            session_factory=session_factory,
            freshrss_factory=lambda: build_source_import_service(freshrss_settings, session_factory),
            indexing_factory=lambda: DocumentIndexingRuntime.build(qdrant_settings, ollama_settings),
            qdrant_settings_factory=lambda: qdrant_settings,
        )

    async def run_once(
        self,
        *,
        limit_per_source: int,
        batch_size: int,
        stale_after: timedelta,
    ) -> PipelineRunOnceExecutionResult:
        """同步执行一个有界新闻增量同步和待索引批次。

        Args:
            limit_per_source: 每个白名单来源本次最多持久化的新闻数。
            batch_size: 本次最多领取的 ``pending/failed`` 文档数。
            stale_after: ``processing`` 任务可回收前必须超过的正时长。

        Returns:
            同步与索引的安全执行统计；来源级和单篇索引失败不会阻止其他项继续。

        Raises:
            ValueError: 任一边界参数不合法。
            Exception: FreshRSS 订阅列表、PostgreSQL 批次操作、Qdrant lifecycle 等无法
                隔离到单个来源/文档的失败。

        Notes:
            本方法执行 FreshRSS 与 PostgreSQL I/O，然后显式准备 Qdrant current Alias，
            再执行 PostgreSQL/Ollama/Qdrant 索引 I/O。它只运行一轮，不创建 asyncio
            后台 Task，不循环、不自动调度，也不执行 Vector Search。
        """

        # 1、同步：FreshRSS → PostgreSQL（不做向量化，只入库并标 pending）
        sync_result = await self.sync_only(limit_per_source=limit_per_source)
        # 2、索引：准备 Qdrant Alias → 领取待处理文档 → 切分/向量化/写入
        index_result = await self.index_only(
            batch_size=batch_size,
            stale_after=stale_after,
        )
        # 两个子结果拼成一个响应载体；失败了由 API/CLI 层转成统计或错误
        return PipelineRunOnceExecutionResult(sync=sync_result, index=index_result)

    async def sync_only(self, *, limit_per_source: int) -> NewsSyncExecutionResult:
        """只执行「FreshRSS → PostgreSQL」增量同步，不触碰 Qdrant。

        手动流水线（``run_once``）和定时任务 ``freshrss_sync`` 共用这一步，保证两条
        入口的同步行为完全一致。

        Args:
            limit_per_source: 每个白名单来源本次最多持久化的新闻数。

        Returns:
            来源、文档、游标推进与失败类型组成的安全汇总。

        Raises:
            ValueError: ``limit_per_source`` 小于一。
            Exception: FreshRSS 订阅列表或 PostgreSQL 批次操作的批次级失败。

        Notes:
            本方法执行 FreshRSS 只读网络 I/O 和 PostgreSQL 业务写入；不生成 Embedding、
            不访问 Qdrant。
        """

        if self.import_service is None:
            self.import_service = self.import_factory()
        return await self.executor.sync_news(
            self.import_service,
            limit_per_source=limit_per_source,
        )

    async def index_only(
        self,
        *,
        batch_size: int,
        stale_after: timedelta,
    ) -> PendingIndexExecutionResult:
        """只执行「PostgreSQL 待索引文档 → Qdrant」批次，不做 FreshRSS 同步。

        定时任务 ``index_pending`` 与手动流水线的索引步骤共用这里；同步频率和索引频率
        可以各自独立配置，互不拖拽。

        Args:
            batch_size: 本次最多领取的 ``pending/failed`` 文档数。
            stale_after: ``processing`` 任务可回收前必须超过的正时长。

        Returns:
            候选、回收、成功、竞争跳过和安全失败明细组成的批次结果。

        Raises:
            ValueError: 边界参数不合法。
            Exception: Qdrant lifecycle 或索引链路无法隔离到单篇的失败。

        Notes:
            本方法显式准备 Qdrant current Alias，再执行 PostgreSQL/Ollama/Qdrant 索引
            I/O；不访问 FreshRSS，也不执行 Vector Search。
        """

        async with self.executor.writing(("index",)):
            if self.indexing_runtime is None:
                self.indexing_runtime = self.indexing_factory()
            await self.indexing_runtime.ensure_ready()
            return await self.executor.index_pending(
                self.indexing_runtime.service, batch_size=batch_size, stale_after=stale_after,
            )

    async def prune_old_documents(
        self, *, retention_days: int, dry_run: bool, knowledge_base_ids: list[UUID]
    ):
        """只创建数据库会话与 Qdrant 删除客户端，不接触 FreshRSS 或向量生成。"""
        async with self.executor.writing(("sync", "index")):
            settings = self.retention_settings_factory()
            if self.retention_client is None:
                self.retention_client = build_qdrant_client(settings)
            async with self.session_factory() as session:
                service = DocumentRetentionService(
                    DocumentRetentionRepository(session), QdrantDeletionStore(self.retention_client, settings),
                )
                return await service.prune_old_documents(
                    retention_days, dry_run, knowledge_base_ids=tuple(knowledge_base_ids)
                )

    async def close(self) -> None:
        """关闭 Ollama 与 Qdrant 写入 client，不修改任何远程业务数据。

        Raises:
            Exception: 任一底层 client 关闭失败。

        Notes:
            FreshRSS client 在同步 Service 内已经按调用关闭；Session 由各自上下文关闭。
            本方法不执行同步、Embedding、Qdrant lifecycle、Point 写入或搜索。
        """

        error = None
        for resource in (self.indexing_runtime, self.retention_client):
            if resource is not None:
                try:
                    await resource.close()
                except Exception as exc:
                    error = error or exc
        if error is not None:
            raise error


__all__ = ["PipelineRunOnceExecutionResult", "PipelineWriteRuntime"]
