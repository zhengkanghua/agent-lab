"""编排「一篇 PostgreSQL 新闻 → Chunk → Embedding → 写入 Qdrant」的完整索引过程。

本模块位于应用 Service 层，是唯一同时知道四个环节的组件：
PostgreSQL 的 ``processing_status``（状态机）、Document/Chunk Pipeline（切分）、
Ollama Embedding（向量化）、Qdrant Point Store（写入）。它相当于索引任务的
「总调度员」。

它不执行相似度搜索、不生成 LLM 回答、不创建 PostgreSQL Chunk/Embedding 表；
Qdrant Collection 和 Alias 的生命周期由单独的 lifecycle 组件负责。一个关键顺序
约定：长时间的 Embedding/Qdrant 网络调用发生在「领取任务」的事务提交之后，
避免在等待网络时一直占着数据库事务连接。
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from news_vector_service.models.document import DocumentRecord
from news_vector_service.pipeline.document_chunk_pipeline import DocumentChunkPipeline
from news_vector_service.pipeline.ollama_embedding_provider import (
    OllamaEmbeddingProvider,
)
from news_vector_service.qdrant.index_spec import VectorIndexSpec
from news_vector_service.qdrant.index_spec import VectorIndexConfigurationError
from news_vector_service.qdrant.store import QdrantChunkStore, ReplaceChunksResult
from news_vector_service.repositories.document_repository import DocumentRepository


@dataclass(frozen=True, slots=True)
class DocumentIndexingResult:
    """报告一次索引任务的最终状态，不保存向量本身。

    只存在于当前调用内存中，不是数据库表。三种可能：
    - ``indexed=True``：PostgreSQL 最终状态更新成功（文档已完整写入 Qdrant）；
    - ``skipped=True``：任务没领到，或处理期间文档内容又变了（被更新的版本抢走）；
    - ``qdrant_result``：只记录 Point ID 变化，不含正文或向量。
    """

    document_id: UUID
    index_revision: int
    indexed: bool
    skipped: bool
    qdrant_result: ReplaceChunksResult | None = None


class DocumentIndexingNotFoundError(LookupError):
    """调用方指定的 PostgreSQL 文档不存在。"""


class DocumentIndexingService:
    """索引任务的总调度员：串接数据库状态、Chunk、Embedding 和 Qdrant 写入。

    核心是一个「先占坑、再干活、后确认」的流程，解决并发安全：
    1. 领取（claim）：用条件 UPDATE 把文档从 pending/failed 原子改成 processing，
       多个 Worker 同时抢时只有一个人能成功；
    2. 干活：切 Chunk → 向量化 → 写 Qdrant（这期间不占数据库事务）；
    3. 确认（mark_indexed）：再次用 revision 条件更新，如果处理期间新闻内容变了
       （revision 变了），条件不满足就不会覆盖新版本，旧 Worker 白干但无害。

    实例可以在同一事件循环中复用，但每次调用传入的 ``AsyncSession`` 只能属于当前
    工作单元（不能跨任务共享 Session）。底层组件只做自己的职责，不自行修改
    processing_status。
    """

    def __init__(
        self,
        *,
        chunk_pipeline: DocumentChunkPipeline,
        embedding_provider: OllamaEmbeddingProvider,
        point_store: QdrantChunkStore,
        spec: VectorIndexSpec,
    ) -> None:
        """创建索引编排服务。

        Args:
            chunk_pipeline: ORM 文档到 LangChain Chunk 的内存流水线。
            embedding_provider: 通过 Ollama 生成并校验向量的 Provider。
            point_store: 只使用 current Alias 的 Qdrant Point Store。
            spec: 当前 Collection 的维度、模型和 Schema 规格。

        Raises:
            VectorIndexConfigurationError: Pipeline tokenizer/Chunk 参数或 Ollama 模型
                与 Collection 规格不一致。不同规则的派生数据不能写入同一索引空间。
        """

        actual_pipeline = (
            chunk_pipeline.encoding_name,
            chunk_pipeline.chunk_size,
            chunk_pipeline.chunk_overlap,
        )
        expected_pipeline = (
            spec.tokenizer,
            spec.chunk_size,
            spec.chunk_overlap,
        )
        # 切块参数(tokenizer/chunk_size/overlap) vs spec
        if actual_pipeline != expected_pipeline:
            raise VectorIndexConfigurationError(
                "Document Chunk Pipeline 与 VectorIndexSpec 不一致："
                f"期望 {expected_pipeline!r}，实际 {actual_pipeline!r}。"
            )

        # 模型 vs spec
        if embedding_provider.embedding_model != spec.embedding_model:
            raise VectorIndexConfigurationError(
                "Ollama 嵌入模型与 VectorIndexSpec 不一致："
                f"期望 {spec.embedding_model!r}，实际 "
                f"{embedding_provider.embedding_model!r}。"
            )

        # store 的 spec vs 自己的 spec
        if point_store.index_spec != spec:
            raise VectorIndexConfigurationError(
                "Qdrant Point Store 与 "
                "DocumentIndexingService 使用的 VectorIndexSpec 不一致。"
            )

        self._chunk_pipeline = chunk_pipeline
        self._embedding_provider = embedding_provider
        self._point_store = point_store
        self._spec = spec

    async def index_document(
        self,
        session: AsyncSession,
        document_id: UUID,
    ) -> DocumentIndexingResult:
        """按主键加载并索引一篇文档，无需调用方处理 ORM eager loading。

        Args:
            session: 当前工作单元的 AsyncSession。
            document_id: PostgreSQL ``documents.id`` 主键。

        Returns:
            当前 revision 的索引或跳过结果。

        Raises:
            DocumentIndexingNotFoundError: 数据库不存在指定文档。
            Exception: PostgreSQL、Chunk、Embedding 或 Qdrant 操作失败。

        Notes:
            本方法先进行 PostgreSQL 读取 I/O，再委托 ``index_record`` 执行数据库状态、
            Ollama Embedding 和 Qdrant Alias 写入；不执行向量检索或 LLM I/O。
        """

        record = await DocumentRepository(session).get_with_source(document_id)
        if record is None:
            raise DocumentIndexingNotFoundError(
                f"未找到待索引的文档 {document_id}。"
            )
        return await self.index_record(session, record)

    async def index_record(
        self,
        session: AsyncSession,
        record: DocumentRecord,
    ) -> DocumentIndexingResult:
        """
        索引一篇新闻的当前 revision，并在成功/失败后更新 processing_status。

        其实就是将pg的谋篇文章，转为chunk，然后embedding，写入向量数据库。

        执行流程（try/except 保证失败也能留下状态痕迹）：
        1. claim_for_indexing：原子领取，抢不到直接返回 skipped；
        2. build_chunks → embed_chunks → replace_document_chunks：切分、向量化、
           写 Qdrant（把这篇新闻的旧 Chunk 一并替换掉）；
        3. mark_indexed：revision 条件更新成功 → indexed；失败说明处理期间有新版
           本，调用 release_stale_claim 把当前 processing 放回 pending；
        4. 任何一步异常 → 尽力 mark_failed（也带 revision 条件），再原样抛出。

        为什么失败也要写 failed 而不是直接抛：让文档保持可被下轮重新领取的状态，
        同时绝不吞掉原始异常（失败原因对排查很重要）。

        Args:
            session: 当前工作单元的 AsyncSession；服务会提交领取、成功或失败状态。
            record: 已 eager-load ``source`` relationship 的 DocumentRecord。

        Returns:
            ``indexed=True`` 表示当前 revision 已完整写入 Qdrant；``skipped=True``
            表示该 revision 已被其他 Worker 领取或已不再是当前版本。

        Raises:
            Exception: Chunk、Embedding、Qdrant 或 PostgreSQL 操作失败时，在尽力写入
                ``failed`` 状态后原样传播。错误状态不会吞掉原始异常。

        Notes:
            本方法执行 PostgreSQL、Embedding 和 Qdrant 网络 I/O，但不执行相似度检索，
            不写 PostgreSQL Chunk/Embedding 表。Qdrant 写入成功而最终数据库 revision
            条件不匹配时返回 ``indexed=False``，下一次 pending 任务会幂等重试。
        """

        repository = DocumentRepository(session)
        revision = record.index_revision
        # 1. 原子领取：把 pending/failed → processing（条件 UPDATE），抢不到就跳过
        claimed = await repository.claim_for_indexing(
            document_id=record.id,
            expected_revision=revision,
        )
        if not claimed:
            return DocumentIndexingResult(
                document_id=record.id,
                index_revision=revision,
                indexed=False,
                skipped=True,
            )

        try:
            # 2. 切分：ORM 文档 → LangChain Chunk（纯内存）
            chunks = self._chunk_pipeline.build_chunks(record)
            if not chunks:
                raise ValueError("文档分块流水线针对非空内容未返回任何分块。")
            # 3. 向量化：逐批调 Ollama，返回与 Chunks 一一对应的向量
            chunk_embeddings = await self._embedding_provider.embed_chunks(chunks)
            if self._embedding_provider.dimension != self._spec.dimension:
                raise ValueError(
                    f"嵌入维度 {self._embedding_provider.dimension} 与"
                    f"索引规格 {self._spec.dimension} 不匹配。"
                )
            # 4. 写入 Qdrant：整篇替换该新闻在 current Alias 下的 Point
            qdrant_result = await self._point_store.replace_document_chunks(
                str(record.id),
                chunks,
                [item.embedding for item in chunk_embeddings],
            )
            # 5. 确认：带 revision 条件标记 indexed；若处理期间内容已更新则条件不满足
            indexed = await repository.mark_indexed(
                document_id=record.id,
                index_revision=revision,
                content_hash=record.content_hash,
                schema_version=self._spec.schema_version,
            )
            if not indexed:
                # 被别人更新了：把这次占的 processing 放回 pending，让新版本重来
                await repository.release_stale_claim(
                    document_id=record.id,
                    stale_revision=revision,
                )
            return DocumentIndexingResult(
                document_id=record.id,
                index_revision=revision,
                indexed=indexed,
                skipped=not indexed,
                qdrant_result=qdrant_result,
            )
        except Exception as exc:
            # 6. 任何一步失败：尽力标记 failed（也带 revision 条件），再原样抛出
            try:
                failed = await repository.mark_failed(
                    document_id=record.id,
                    index_revision=revision,
                    error_message=self._safe_error(exc),
                )
                if not failed:
                    await repository.release_stale_claim(
                        document_id=record.id,
                        stale_revision=revision,
                    )
            except Exception as status_exc:
                # Qdrant/Ollama 原始失败是任务根因；状态记录失败通过 add_note 保留，
                # 不能用第二个数据库异常覆盖它，否则排查会看到错误的第一现场。
                exc.add_note(
                    "此外写入 processing_status=failed 状态也失败："
                    f"{type(status_exc).__name__}。"
                )
            raise

    @staticmethod
    def _safe_error(error: Exception) -> str:
        """生成不含秘密和完整远程响应的限长诊断文本。"""

        # 项目自己的异常已经在边界处移除了 URL 凭据、API Key 和远程响应正文；未知
        # 第三方异常只记录类型，避免它的 str() 意外包含认证 header 或敏感请求内容。
        module = type(error).__module__
        if module.startswith("news_vector_service"):
            message = str(error).replace("\r", " ").replace("\n", " ")
            return f"{type(error).__name__}: {message}"[:1000]
        return f"{type(error).__name__}: indexing operation failed"
