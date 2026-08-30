"""封装 ``documents`` 表的幂等写入、索引候选读取和状态条件更新。

本模块位于 Repository 持久层，只执行 PostgreSQL I/O：它维护新闻业务字段、
``index_revision`` 和 ``processing_status`` 的并发约束，但不切分文本、不调用 Ollama、
不写 Qdrant，也不决定完整索引任务是否成功。跨 PostgreSQL、Embedding 和 Qdrant 的
顺序由 ``DocumentIndexingService`` 编排。
"""

from hashlib import sha256
from uuid import UUID, uuid4

from datetime import UTC, datetime

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.domain.source_document import SourceDocument
from agent_lab.models.document import DocumentRecord


class DocumentRepository:
    """在一个 AsyncSession 工作单元内维护文档及向量索引状态。

    Repository 实例与调用方提供的 ``AsyncSession`` 生命周期一致，不应跨并发任务共享。
    ``source_id + external_id`` 保证业务幂等；条件 UPDATE 保证每个 revision 最多由一个
    Worker 领取，并防止旧 Worker 把新版本标为 indexed。方法会按 docstring 说明决定
    是否提交事务，不持有 Qdrant 或 Ollama client。
    """

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前业务事务使用的 AsyncSession。

        Args:
            session: 由调用方管理事务边界的 SQLAlchemy 异步 Session。
        """

        self._session = session

    async def get_with_source(self, document_id: UUID) -> DocumentRecord | None:
        """读取一篇文档并 eager-load ``source`` relationship。

        Args:
            document_id: PostgreSQL ``documents.id`` 主键。

        Returns:
            可直接交给 ``DocumentBuilder`` 的 ORM 文档；不存在时返回 ``None``。

        Raises:
            Exception: PostgreSQL 查询失败时传播。

        Notes:
            这是 PostgreSQL 只读 I/O。``selectinload`` 避免后续同步 Pipeline 访问
            ``record.source`` 时触发异步隐式查询；不进行 Embedding 或 Qdrant I/O。
        """

        statement = (
            select(DocumentRecord)
            .options(selectinload(DocumentRecord.source))
            .where(DocumentRecord.id == document_id)
        )
        # scalar：获取查询结果中第一行第一列的那个数据。
        return await self._session.scalar(statement)

    async def list_index_candidate_ids(self, *, limit: int = 100) -> list[UUID]:
        """按稳定顺序读取一批待处理或失败文档 ID。

        Args:
            limit: 单次最多返回的候选数量，必须大于零。

        Returns:
            ``pending`` 和 ``failed`` 文档的 UUID 列表，先按更新时间、再按主键排序。

        Raises:
            ValueError: ``limit`` 不大于零。
            Exception: PostgreSQL 查询失败时传播。

        Notes:
            这是 PostgreSQL 只读 I/O，只返回 ID，不预先占用任务。真正的并发领取仍由
            ``claim_for_indexing`` 的条件 UPDATE 完成；列表过期时领取会安全返回 False。
        """

        if limit < 1:
            raise ValueError("limit 必须大于零")
        statement = (
            select(DocumentRecord.id)
            .where(
                DocumentRecord.processing_status.in_(
                    [ProcessingStatus.PENDING, ProcessingStatus.FAILED]
                )
            )
            .order_by(DocumentRecord.updated_at, DocumentRecord.id)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def upsert(
        self,
        document: SourceDocument,
        *,
        source_id: UUID,
    ) -> DocumentRecord:
        """按来源和外部 ID 幂等保存文档。

        Args:
            document: 已完成外部协议转换和正文清洗的统一文档。
            source_id: 文档所属 ``sources`` 行的 PostgreSQL UUID。

        Returns:
            新插入、实际更新或内容完全相同时查询到的 ORM 文档对象。

        Notes:
            ``source_id + external_id`` 是数据库唯一业务键。只有字段实际变化时
            才执行 ``ON CONFLICT DO UPDATE``；任何会进入 Chunk、Embedding 或 Payload
            的字段变化都会递增 ``index_revision``。若旧 revision 正在处理则暂时保持
            ``processing``，由旧 Worker 结束时释放新版为 ``pending``，避免两个版本
            并发覆盖同一 Qdrant Point。完全相同的重复同步不会改动 ``updated_at``。

        Raises:
            RuntimeError: 冲突分支未返回记录且无法查询到现有行时抛出。
        """

        # 1、计算规范化正文的 SHA-256（内容指纹，用于判断是否变化/是否需重建向量）
        content_hash = sha256(document.content_text.encode("utf-8")).hexdigest()
        # 2、准备「插入值」：新行默认 pending、index_revision=1
        values = {
            "id": uuid4(),
            "source_id": source_id,
            "external_id": document.external_id,
            "document_type": document.document_type,
            "title": document.title,
            "url": str(document.url),
            "published_at": document.published_at,
            "source_updated_at": document.source_updated_at,
            "authors": list(document.authors),
            "labels": list(document.labels),
            "image_urls": [str(image.url) for image in document.images],
            "content_text": document.content_text,
            "content_hash": content_hash,
            "processing_status": ProcessingStatus.PENDING,
            "index_revision": 1,
        }
        insert_statement = insert(DocumentRecord).values(**values)
        excluded = insert_statement.excluded
        # 3、「会影响向量/切分结果」的字段是否变化：变了就重建/重埋向量
        index_inputs_changed = or_(
            DocumentRecord.document_type.is_distinct_from(excluded.document_type),
            DocumentRecord.title.is_distinct_from(excluded.title),
            DocumentRecord.url.is_distinct_from(excluded.url),
            DocumentRecord.published_at.is_distinct_from(excluded.published_at),
            DocumentRecord.source_updated_at.is_distinct_from(excluded.source_updated_at),
            DocumentRecord.authors.is_distinct_from(excluded.authors),
            DocumentRecord.labels.is_distinct_from(excluded.labels),
            DocumentRecord.content_hash.is_distinct_from(excluded.content_hash),
        )
        # 4、「是否需要写这行」：索引相关字段变了，或纯图片变了（后者无需重埋向量）
        has_changes = or_(
            index_inputs_changed,
            # image_urls 是 PostgreSQL 业务字段，但当前不进入 page_content 或 Qdrant
            # Payload。图片变化仍应持久化，却不值得重新调用 Ollama 生成相同正文向量。
            DocumentRecord.image_urls.is_distinct_from(excluded.image_urls),
        )
        upsert_statement = (
            insert_statement.on_conflict_do_update(
                constraint="uq_documents_source_external_id",
                set_={
                    "document_type": excluded.document_type,
                    "title": excluded.title,
                    "url": excluded.url,
                    "published_at": excluded.published_at,
                    "source_updated_at": excluded.source_updated_at,
                    "authors": excluded.authors,
                    "labels": excluded.labels,
                    "image_urls": excluded.image_urls,
                    "content_text": excluded.content_text,
                    "content_hash": excluded.content_hash,
                    "processing_status": case(
                        (
                            index_inputs_changed,
                            case(
                                (
                                    DocumentRecord.processing_status
                                    == ProcessingStatus.PROCESSING,
                                    ProcessingStatus.PROCESSING.value,
                                ),
                                else_=ProcessingStatus.PENDING.value,
                            ),
                        ),
                        else_=DocumentRecord.processing_status,
                    ),
                    # 相关字段变了 → +1   没变 → 保持
                    "index_revision": case(
                        (index_inputs_changed, DocumentRecord.index_revision + 1),
                        else_=DocumentRecord.index_revision,
                    ),
                    "last_processing_error": case(
                        (index_inputs_changed, None),
                        else_=DocumentRecord.last_processing_error,
                    ),
                    "updated_at": func.now(),
                },
                where=has_changes,
            )
            .returning(DocumentRecord)
            # 同一 AsyncSession 可能已经缓存该 ORM 对象；populate_existing 让
            # RETURNING 的新 revision/status 覆盖 identity map 旧值，否则数据库已
            # 更新而调用方仍会读取旧快照，进而用错误 revision 领取索引任务。
            .execution_options(populate_existing=True)
        )

        # 5、执行 UPSERT；有返回行说明发生了 INSERT 或 UPDATE（内容确实变了）
        record = (await self._session.scalars(upsert_statement)).one_or_none()
        if record is not None:
            return record

        # 6、内容完全相同 → ON CONFLICT 的 WHERE 不匹配，没有 UPDATE，也不改
        #    updated_at。这里需再查一次现有行返回给调用方（纯幂等命中）。
        existing_statement = select(DocumentRecord).where(
            DocumentRecord.source_id == source_id,
            DocumentRecord.external_id == document.external_id,
        )
        existing = await self._session.scalar(existing_statement)
        if existing is None:
            raise RuntimeError("文档 upsert 未返回行")
        return existing

    async def claim_for_indexing(
        self,
        *,
        document_id: UUID,
        expected_revision: int,
    ) -> bool:
        """原子领取一篇指定版本的文档索引任务。

        Args:
            document_id: PostgreSQL ``documents.id``。
            expected_revision: 调用方读取到的 ``index_revision`` 快照。

        Returns:
            成功从 ``pending`` 或 ``failed`` 变为 ``processing`` 时返回 ``True``；
            版本或状态已经被其他 Worker 改变时返回 ``False``。

        Raises:
            Exception: 数据库更新失败时由调用方事务处理逻辑传播。

        Notes:
            这是 PostgreSQL I/O。条件更新把“检查状态”和“领取任务”放进同一条 SQL，
            避免两个 Worker 同时处理同一版本。方法会提交领取事务，让长时间 Ollama/
            Qdrant 网络 I/O 不占用未提交数据库事务。
        """

        # 一条带三重条件的 UPDATE：
        #   1、 文档要存在；2、 revision 还是调用方读到的那个（内容没被新版本顶掉）；
        #   3、 状态还是 pending/failed（还没被别人领走）。
        # 只有 123 同时满足，才被改为 processing。这就是「原子领取」——检查和
        # 占用在同一条 SQL 里完成，两个 Worker 同时抢时只有一个会返回 rowcount=1。
        statement = (
            update(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.index_revision == expected_revision,
                DocumentRecord.processing_status.in_(
                    [ProcessingStatus.PENDING, ProcessingStatus.FAILED]
                ),
            )
            .values(
                processing_status=ProcessingStatus.PROCESSING,
                processing_started_at=datetime.now(UTC),
                last_processing_error=None,
            )
        )
        result = await self._session.execute(statement)
        # 立即提交，让后续长时间的 Ollama/Qdrant 网络 I/O 不再占用未提交事务
        await self._session.commit()
        return result.rowcount == 1

    async def mark_indexed(
        self,
        *,
        document_id: UUID,
        index_revision: int,
        content_hash: str,
        schema_version: str,
    ) -> bool:
        """仅在版本未变化时把当前任务标记为 ``indexed``。

        Args:
            document_id: PostgreSQL 文档主键。
            index_revision: Worker 开始时保存的版本快照。
            content_hash: Worker 实际处理的正文哈希。
            schema_version: 写入 Qdrant 的索引契约版本。

        Returns:
            条件更新成功返回 ``True``；新闻已被更新或任务状态已改变时返回 ``False``。

        Raises:
            Exception: PostgreSQL 更新失败时传播。

        Notes:
            这是 PostgreSQL I/O。条件包含 revision 和 processing 状态，防止旧 Worker
            覆盖新版本；成功后提交数据库事务。Qdrant 已成功写入但这里返回 False 时，
            下一次 pending 任务会用稳定 Point ID 幂等覆盖。
        """

        statement = (
            update(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.index_revision == index_revision,
                DocumentRecord.content_hash == content_hash,
                DocumentRecord.processing_status == ProcessingStatus.PROCESSING,
            )
            .values(
                processing_status=ProcessingStatus.INDEXED,
                indexed_revision=index_revision,
                indexed_content_hash=content_hash,
                indexed_schema_version=schema_version,
                indexed_at=datetime.now(UTC),
                processing_started_at=None,
                last_processing_error=None,
            )
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.rowcount == 1

    async def mark_failed(
        self,
        *,
        document_id: UUID,
        index_revision: int,
        error_message: str,
    ) -> bool:
        """把仍属于指定版本的处理任务标记为 ``failed``。

        Args:
            document_id: PostgreSQL 文档主键。
            index_revision: Worker 开始时保存的版本快照。
            error_message: 已脱敏、限长的错误类别和说明。

        Returns:
            条件更新成功返回 ``True``；若文档已经进入新 revision 则返回 ``False``，
            保留新版本的 ``pending`` 状态。

        Raises:
            Exception: PostgreSQL 更新失败时传播。

        Notes:
            这是 PostgreSQL I/O。错误文本只作为有限诊断信息保存，不应包含 API Key、
            完整正文或远程响应原文。
        """

        safe_error = error_message[:1000]
        statement = (
            update(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.index_revision == index_revision,
                DocumentRecord.processing_status == ProcessingStatus.PROCESSING,
            )
            .values(
                processing_status=ProcessingStatus.FAILED,
                processing_started_at=None,
                last_processing_error=safe_error,
            )
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.rowcount == 1

    async def release_stale_claim(
        self,
        *,
        document_id: UUID,
        stale_revision: int,
    ) -> bool:
        """旧 Worker 结束后，把已更新的当前 revision 释放为 ``pending``。

        Args:
            document_id: PostgreSQL 文档主键。
            stale_revision: 旧 Worker 实际处理的 revision。

        Returns:
            文档仍为 ``processing`` 且 revision 已变化时返回 ``True``；其他情况返回
            ``False``，避免覆盖新任务的最终状态。

        Raises:
            Exception: PostgreSQL 更新失败时传播。

        Notes:
            这是 PostgreSQL I/O。FreshRSS 在旧任务运行中只递增 revision 并保留
            ``processing``，从而阻止新版并发领取；旧任务发现条件写入失败后调用本方法，
            新 revision 才进入 ``pending``。这样用短数据库事务实现单文档版本串行化，
            不必在 Ollama/Qdrant 网络 I/O 期间长期持有行锁。
        """

        statement = (
            update(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.index_revision != stale_revision,
                DocumentRecord.processing_status == ProcessingStatus.PROCESSING,
            )
            .values(
                processing_status=ProcessingStatus.PENDING,
                processing_started_at=None,
            )
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.rowcount == 1

    async def requeue_stale_processing(self, *, started_before: datetime) -> int:
        """把超过租约时间仍处于 ``processing`` 的任务重新放回队列。

        Args:
            started_before: 早于该带时区时间开始且仍未完成的任务视为 Worker 已失联。

        Returns:
            本次重置为 ``pending`` 的文档数量。

        Raises:
            ValueError: ``started_before`` 没有时区信息。
            Exception: PostgreSQL 更新失败时传播。

        Notes:
            这是 PostgreSQL I/O，并会提交事务。调用方必须把阈值设得明显大于正常
            Embedding/Qdrant 最大处理时间，避免把仍在工作的任务错误重复投递。
        """

        if started_before.utcoffset() is None:
            raise ValueError("started_before 必须包含时区信息")
        statement = (
            update(DocumentRecord)
            .where(
                DocumentRecord.processing_status == ProcessingStatus.PROCESSING,
                DocumentRecord.processing_started_at < started_before,
            )
            .values(
                processing_status=ProcessingStatus.PENDING,
                processing_started_at=None,
                last_processing_error="Stale processing lease was requeued.",
            )
        )
        result = await self._session.execute(statement)
        await self._session.commit()
        return result.rowcount
