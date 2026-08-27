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
        has_changes = or_(
            SourceRecord.name.is_distinct_from(excluded.name),
            SourceRecord.feed_url.is_distinct_from(excluded.feed_url),
            SourceRecord.home_url.is_distinct_from(excluded.home_url),
        )
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
        if record is not None:
            # 返回行意味着实际 INSERT 或 UPDATE；新来源尚无文档，更新为 0。已有来源
            # 的展示名称会进入 Qdrant Payload，因此其关联文档必须递增 revision。
            # Feed/home URL 变化也会触发这次保守重索引，来源配置变化非常少，第一版
            # 优先保证 Payload 不陈旧，避免引入额外“旧值返回”数据库技巧。
            await self._mark_documents_for_reindex(record.id)
            return record

        # ON CONFLICT 的 WHERE 在“内容完全相同”时不执行 UPDATE，因此再查询现有行。
        existing_statement = select(SourceRecord).where(
            SourceRecord.provider == source.provider,
            SourceRecord.external_id == source.external_id,
        )
        existing = await self._session.scalar(existing_statement)
        if existing is None:
            raise RuntimeError("来源 upsert 未返回行")
        return existing

    async def get_sync_checkpoint(
        self,
        *,
        provider: str,
        external_id: str,
    ) -> str | None:
        """读取一个来源的增量 continuation 游标。

        Args:
            provider: 数据提供方稳定标识，与 ``sources.provider`` 对应。
            external_id: 提供方中的来源 ID，与 ``sources.external_id`` 对应。

        Returns:
            已成功提交的 FreshRSS continuation 字符串；来源不存在或尚未同步时为
            ``None``。

        Raises:
            Exception: PostgreSQL 查询失败时传播。

        Notes:
            这是短 PostgreSQL 只读 I/O。调用方应在发起 FreshRSS 网络请求前结束该
            事务；游标最终是否推进由 ``update_sync_checkpoint`` 的条件 UPDATE 决定。
        """

        statement = select(SourceRecord.sync_checkpoint).where(
            SourceRecord.provider == provider,
            SourceRecord.external_id == external_id,
        )
        return await self._session.scalar(statement)

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

        normalized = new_checkpoint.strip()
        if not normalized or not normalized.isascii() or not normalized.isdecimal():
            raise ValueError("同步检查点必须是十进制字符串")
        normalized = str(int(normalized))
        if expected_checkpoint is not None:
            expected = expected_checkpoint.strip()
            if not expected.isascii() or not expected.isdecimal():
                raise ValueError("期望的同步检查点必须是十进制字符串")
            expected = str(int(expected))
        else:
            expected = None

        if expected is not None and int(normalized) < int(expected):
            raise ValueError("同步检查点不能回退")

        # 相同游标无需写入时间；这也让重复执行保持完全幂等。
        if expected == normalized:
            return True

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
