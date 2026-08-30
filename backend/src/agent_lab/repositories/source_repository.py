"""封装 ``sources`` 表幂等写入及来源变化后的文档重新排队。

本模块位于 Repository 持久层，只执行当前 PostgreSQL 事务内的来源和关联文档更新；
不访问 FreshRSS 网络、不构建 Payload、不调用 Embedding 或 Qdrant。来源展示字段变化
会影响未来 Point Payload，因此本层递增关联文档 revision，完整索引仍由 Service 完成。
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.domain.source_document import SourceInfo
from agent_lab.domain.enums import ProcessingStatus
from agent_lab.models.document import DocumentRecord
from agent_lab.models.source import SourceRecord


class SourceRepository:
    """在当前 AsyncSession 中通过业务唯一键维护一个外部来源。

    实例与调用方事务工作单元同生命周期，不跨并发任务共享。``provider + external_id``
    是来源唯一键；实际来源变化会让已有文档重新排队，完全相同同步保持 updated_at 和
    revision 不变。Repository 不提交事务，由上层 FreshRSSImportService 统一提交或回滚。
    """

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前业务事务使用的 AsyncSession。

        Args:
            session: 由调用方管理事务边界的 SQLAlchemy 异步 Session。
        """

        self._session = session

    async def upsert(self, source: SourceInfo) -> SourceRecord:
        """按提供方和外部来源 ID 幂等保存来源。

        Args:
            source: 已规范化的来源信息。

        Returns:
            新插入、实际更新或内容完全相同时查询到的 ORM 来源对象。

        Notes:
            ``provider + external_id`` 是数据库唯一业务键。名称、Feed URL 和
            主页 URL 都未变化时，``ON CONFLICT`` 不执行无意义更新，因此不会
            改动 ``updated_at``。

        Raises:
            RuntimeError: 冲突分支未返回记录且无法查询到现有行时抛出。
        """

        # 1、备好要写的值。id 先生成一个，冲突时这个值会被丢掉，不会占用。
        values = {
            "id": uuid4(),
            "provider": source.provider,
            "external_id": source.external_id,
            "name": source.name,
            "feed_url": str(source.feed_url) if source.feed_url else None,
            "home_url": str(source.home_url) if source.home_url else None,
        }
        insert_statement = insert(SourceRecord).values(**values)
        excluded = insert_statement.excluded
        # 2、定义「什么算真的变了」。用 is_distinct_from 而不是 !=：NULL != NULL 在 SQL 里
        #    是 NULL（不成立），会把「原来没有 home_url、现在还是没有」误判成有变化。
        has_changes = or_(
            SourceRecord.name.is_distinct_from(excluded.name),
            SourceRecord.feed_url.is_distinct_from(excluded.feed_url),
            SourceRecord.home_url.is_distinct_from(excluded.home_url),
        )
        # 3、一条语句解决插入和更新：冲突时只在真的有变化时才 UPDATE。
        upsert_statement = (
            insert_statement.on_conflict_do_update(
                constraint="uq_sources_provider_external_id",
                set_={
                    "name": excluded.name,
                    "feed_url": excluded.feed_url,
                    "home_url": excluded.home_url,
                    "updated_at": func.now(),
                },
                where=has_changes,
            )
            .returning(SourceRecord)
            # 来源对象也可能已存在于 Session identity map；实际 UPDATE 后必须让
            # RETURNING 的新名称和 URL 覆盖旧属性，后续 DocumentBuilder 才能生成
            # 与数据库一致的 Payload。
            .execution_options(populate_existing=True)
        )

        record = (await self._session.scalars(upsert_statement)).one_or_none()
        # 4、有返回行 = 真的插了或改了。来源的展示名称会进 Qdrant Payload，所以它一变，
        #    名下所有文档的 Payload 就旧了，必须重新排队索引。新来源名下还没有文档，
        #    这一步影响 0 行。Feed/home URL 变化也一起触发这次保守重索引：来源配置极少
        #    变动，第一版宁可多索引一次，也不引入「取旧值比对」那类数据库技巧。
        if record is not None:
            await self._mark_documents_for_reindex(record.id)
            return record

        # 5、没有返回行 = 记录已存在且内容完全一样（上面那个 WHERE 让 UPDATE 没执行）。
        #    这是幂等路径：不改 updated_at、不重新索引，只要把现有行查出来还给调用方。
        existing_statement = select(SourceRecord).where(
            SourceRecord.provider == source.provider,
            SourceRecord.external_id == source.external_id,
        )
        existing = await self._session.scalar(existing_statement)
        if existing is None:
            raise RuntimeError("来源 upsert 未返回行")
        return existing

    async def get_by_business_key(
        self,
        *,
        provider: str,
        external_id: str,
    ) -> SourceRecord | None:
        """按来源业务唯一键读取 ORM 记录和已提交 checkpoint。

        Args:
            provider: 数据提供方稳定标识，与 ``sources.provider`` 对应。
            external_id: 提供方中的来源 ID，与 ``sources.external_id`` 对应。

        Returns:
            已存在的来源记录；首次见到该来源时返回 ``None``。

        Raises:
            Exception: PostgreSQL 查询失败时传播。

        Notes:
            这是短 PostgreSQL 只读 I/O，不提交事务。增量同步调用方会在发起网络请求
            前结束该读事务，只保留主键和 checkpoint 值，不跨网络等待使用 ORM 状态。
        """

        statement = select(SourceRecord).where(
            SourceRecord.provider == provider,
            SourceRecord.external_id == external_id,
        )
        return await self._session.scalar(statement)

    async def update_sync_checkpoint(
        self,
        *,
        source_id: UUID,
        expected_checkpoint: str | None,
        new_checkpoint: str,
    ) -> bool:
        """在来源仍处于预期游标时推进 FreshRSS checkpoint。

        Args:
            source_id: ``sources.id`` 主键。
            expected_checkpoint: 发起本页 FreshRSS 请求前读取到的旧游标，可为 ``None``。
            new_checkpoint: 本页新闻成功持久化后得到的十进制 continuation 游标。

        Returns:
            条件更新影响一行时返回 ``True``；另一个同步进程已推进游标时返回 ``False``。
            后者不是错误，表示并发执行已由较新的提交获胜。

        Raises:
            ValueError: 新游标不是非负十进制字符串。
            ValueError: 新游标数值小于旧游标，可能导致已处理新闻被重复追赶。
            Exception: PostgreSQL UPDATE 失败时传播；调用方应回滚包含文档 upsert 的事务。

        Notes:
            这是当前事务内的 PostgreSQL 写入，不自行提交。条件 UPDATE 防止较旧的网络
            请求把游标回退；只有调用方随后 commit 成功，checkpoint 才对下一次执行可见。
            不修改 ``updated_at``，避免把运行时同步进度误当作来源展示字段变化。
        """

        # 1、规范化新游标。走一遍 int() 再转回字符串，是为了把 "007" 和 "7" 统一成同一个
        #    写法，否则下面那个相等比较会把它们当成两个不同的游标。
        normalized = new_checkpoint.strip()
        if not normalized or not normalized.isascii() or not normalized.isdecimal():
            raise ValueError("同步检查点必须是十进制字符串")
        normalized = str(int(normalized))
        # 2、旧游标同样规范化，两边写法一致才能比。
        if expected_checkpoint is not None:
            expected = expected_checkpoint.strip()
            if not expected.isascii() or not expected.isdecimal():
                raise ValueError("期望的同步检查点必须是十进制字符串")
            expected = str(int(expected))
        else:
            expected = None

        # 3、游标只能往前。往回走意味着已经处理过的新闻会被重新抓一遍。
        if expected is not None and int(normalized) < int(expected):
            raise ValueError("同步检查点不能回退")

        # 4、完全没动就直接返回，连时间戳也不写——重复执行保持完全幂等。
        if expected == normalized:
            return True

        # 5、条件 UPDATE：只在游标仍是我们读到的那个值时才写。这样两个并发同步进程里，
        #    慢的那个会更新 0 行、返回 False，不会把快的那个推进的游标压回去。
        statement = (
            update(SourceRecord)
            .where(
                SourceRecord.id == source_id,
                SourceRecord.sync_checkpoint.is_not_distinct_from(expected),
            )
            .values(
                sync_checkpoint=normalized,
                sync_checkpoint_updated_at=datetime.now(UTC),
            )
        )
        result = await self._session.execute(statement)
        return result.rowcount == 1

    async def _mark_documents_for_reindex(self, source_id: UUID) -> None:
        """来源实际变化时，让所有关联文档进入新的索引 revision。

        Args:
            source_id: 已写入 ``sources.id`` 的 PostgreSQL UUID。

        Notes:
            这是当前事务内的 PostgreSQL 写入，不提交事务。正在处理的文档保持
            ``processing``，旧 Worker 结束后再把新 revision 释放为 ``pending``；其他
            文档立即设为 ``pending``。方法不会修改 Qdrant。
        """

        await self._session.execute(
            update(DocumentRecord)
            .where(DocumentRecord.source_id == source_id)
            .values(
                index_revision=DocumentRecord.index_revision + 1,
                updated_at=func.now(),
                last_processing_error=None,
                processing_status=case(
                    (
                        DocumentRecord.processing_status
                        == ProcessingStatus.PROCESSING,
                        ProcessingStatus.PROCESSING.value,
                    ),
                    else_=ProcessingStatus.PENDING.value,
                ),
            )
        )
