"""组装一次「手动新闻同步 + 向量索引」所需的写入 Runtime（工具箱）。

本模块位于“装配根”和“应用 Service”之间：它把 NewsPipelineExecutionService（编排
同步/索引批次）、FreshRSSImportService（抓取）和 DocumentIndexingRuntime（写 Qdrant）
三个已有组件打包成一个可调用的写工具箱。

它不暴露 Vector Search（搜索由独立的只读 VectorSearchRuntime 负责），保证写入口
不会获得读权限。也不实现后台任务/队列/调度/WebSocket/LLM/RAG，构造时不做任何外部 I/O。
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.config.freshrss import FreshRSSSettings
from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.qdrant.runtime import DocumentIndexingRuntime
from agent_lab.services.freshrss_import_service import FreshRSSImportService
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


@dataclass(frozen=True, slots=True)
class PipelineWriteRuntime:
    """持有一次手动执行所需的「导入、执行、索引写入」三组写入组件。

    生命周期 = 一次 HTTP 请求 或 一次显式 CLI 工作单元，用完即整体关闭：
    - ``build`` 只创建本地 client 和组件（不连外部服务）；
    - ``run_once`` 依次做 FreshRSS/PostgreSQL 同步 → 准备 Qdrant Alias → 索引；
    - ``close`` 释放写入 client。

    它没有搜索 Service，因此不会污染只读 VectorSearchRuntime 的权限边界。
    """

    executor: NewsPipelineExecutionService
    import_service: FreshRSSImportService
    indexing_runtime: DocumentIndexingRuntime

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

        return cls(
            executor=NewsPipelineExecutionService(session_factory),
            import_service=FreshRSSImportService(freshrss_settings),
            indexing_runtime=DocumentIndexingRuntime.build(
                qdrant_settings,
                ollama_settings,
            ),
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
        sync_result = await self.executor.sync_news(
            self.import_service,
            limit_per_source=limit_per_source,
        )
        # 2、显式准备 Qdrant：创建/校验物理 Collection 与 current Alias
        await self.indexing_runtime.ensure_ready()
        # 3、索引：领取待处理文档，切分 → 向量化 → 写入 Qdrant
        index_result = await self.executor.index_pending(
            self.indexing_runtime.service,
            batch_size=batch_size,
            stale_after=stale_after,
        )
        # 两个子结果拼成一个响应载体；失败了由 API/CLI 层转成统计或错误
        return PipelineRunOnceExecutionResult(sync=sync_result, index=index_result)

    async def close(self) -> None:
        """关闭 Ollama 与 Qdrant 写入 client，不修改任何远程业务数据。

        Raises:
            Exception: 任一底层 client 关闭失败。

        Notes:
            FreshRSS client 在同步 Service 内已经按调用关闭；Session 由各自上下文关闭。
            本方法不执行同步、Embedding、Qdrant lifecycle、Point 写入或搜索。
        """

        await self.indexing_runtime.close()


__all__ = ["PipelineRunOnceExecutionResult", "PipelineWriteRuntime"]
