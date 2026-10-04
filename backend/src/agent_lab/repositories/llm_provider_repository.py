"""上游渠道的短事务存取。

本表**只有停用，没有删除**（与 KnowledgeBase 的「停用保留数据」一致），所以这个仓储里没有
``delete_*``：渠道被引用之后按 id 保留，配置改错就改回启用位或改地址。
"""

from uuid import uuid4

from sqlalchemy import select

from agent_lab.models.llm_provider import LlmProviderRecord


class LlmProviderRepository:
    def __init__(self, session):
        self._session = session

    async def list_providers(self):
        """按添加顺序返回全部渠道，供后台管理列表使用（含已停用的）。"""

        result = await self._session.scalars(
            select(LlmProviderRecord).order_by(
                LlmProviderRecord.created_at, LlmProviderRecord.id
            )
        )
        return result.all()

    async def get_provider(self, provider_id):
        return await self._session.get(LlmProviderRecord, provider_id)

    async def lock_provider(self, provider_id):
        """按 id 取渠道并加行锁。

        更新是「读出来 → 按请求算出保存后的状态 → 校验 → 写回」，中间那两次读写必须在同一条
        行锁内，否则两个并发请求各自基于旧快照判断，可能一起写出「要求凭据却没有凭据」的行。
        取不到时返回 ``None``（由 Service 翻成 404）。
        """

        return await self._session.scalar(
            select(LlmProviderRecord)
            .where(LlmProviderRecord.id == provider_id)
            .with_for_update()
        )

    async def create_provider(self, **values):
        record = LlmProviderRecord(id=uuid4(), **values)
        self._session.add(record)
        await self._session.commit()
        return record

    async def commit(self):
        await self._session.commit()

    async def refresh(self, record):
        """重新读回数据库写入的默认值（``created_at`` / ``updated_at`` 由 PostgreSQL 生成）。"""

        await self._session.refresh(record)
