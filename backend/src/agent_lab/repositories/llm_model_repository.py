"""可用模型的短事务存取。

本表**只有停用，没有删除**（与 ``KnowledgeBase``、``llm_providers`` 的「停用保留数据」一致），
所以这个仓储里没有 ``delete_*``：配置改错就改启用位或改字段。

**可用性一律在同一条查询里 join 出来**，不把「所属渠道启不启用」抄进模型的列里——模型
自己的 ``enabled`` 说「这条模型有没有被管理员停用」，渠道的 ``enabled`` 说「渠道有没有被
管理员停用」，两列各说一件事，用户能看到哪些模型由两者相与决定。抄一份到模型的列上就等于
同一件事有两个事实源，停用渠道时必须逐个改模型的启用位，两份状态必然漂移。

``llm_providers`` 没有删除路径，所以这里的 join 用内连接：每条模型的所属渠道一定还在。
"""

from uuid import uuid4

from sqlalchemy import select, update

from agent_lab.models.llm_model import LlmModelRecord
from agent_lab.models.llm_provider import LlmProviderRecord


class LlmModelRepository:
    def __init__(self, session):
        self._session = session

    async def list_models(self):
        """按添加顺序返回全部模型（含停用的），连所属渠道的展示名与启用位一起读出来。

        后台管理列表要的是「这个模型属于哪条渠道、那条渠道有没有被停用」，所以 join 一次
        把它带出来，而不是让路由层为每一行再查一次渠道。
        """

        result = await self._session.execute(
            select(LlmModelRecord, LlmProviderRecord.name, LlmProviderRecord.enabled)
            .join(LlmProviderRecord, LlmProviderRecord.id == LlmModelRecord.provider_id)
            .order_by(LlmModelRecord.created_at, LlmModelRecord.id)
        )
        return result.all()

    async def list_available_models(self):
        """只返回「自身启用 **且** 所属渠道也启用」的模型，供用户的选择列表使用。"""

        result = await self._session.execute(
            select(LlmModelRecord, LlmProviderRecord.name)
            .join(LlmProviderRecord, LlmProviderRecord.id == LlmModelRecord.provider_id)
            .where(
                LlmModelRecord.enabled.is_(True),
                LlmProviderRecord.enabled.is_(True),
            )
            .order_by(LlmModelRecord.created_at, LlmModelRecord.id)
        )
        return result.all()

    async def get_model(self, model_id):
        return await self._session.get(LlmModelRecord, model_id)

    async def get_model_with_provider(self, model_id):
        """按 id 取模型，连它所属渠道一起取回来。

        判「这一条能不能被用户用」要同时读两个启用位（模型自己的、渠道的），所以两条一起取；
        分两次查会在两次之间给一个不一致的快照。取不到时返回 ``None``。

        Returns:
            ``(模型行, 渠道行)``；模型不存在时为 ``None``。
        """

        result = await self._session.execute(
            select(LlmModelRecord, LlmProviderRecord)
            .join(LlmProviderRecord, LlmProviderRecord.id == LlmModelRecord.provider_id)
            .where(LlmModelRecord.id == model_id)
        )
        return result.first()

    async def default_available_model(self):
        """取当前默认模型，且它自身启用、所属渠道也启用；没有可用的默认时返回 ``None``。

        与会话行的选择为空时那条路径一一对应：没选模型就用默认的那个。筛选条件与
        ``list_available_models`` 同一套——不变量保证「有可用模型就恰好有一个可用默认」，
        这里再判一次是为了不把一条不可用的默认偷偷拿去用（那正好是「不静默换模型」要禁的事）。

        它和 ``earliest_available_model`` 不是一回事：那一个是**补默认**用的（目录里还没有默认
        时挑最早添加的），这一个用的是已经定下来的默认。
        """

        return await self._session.scalar(
            select(LlmModelRecord)
            .join(LlmProviderRecord, LlmProviderRecord.id == LlmModelRecord.provider_id)
            .where(
                LlmModelRecord.is_default.is_(True),
                LlmModelRecord.enabled.is_(True),
                LlmProviderRecord.enabled.is_(True),
            )
        )

    async def lock_model(self, model_id):
        """按 id 取模型并加行锁。

        更新是「读出来 → 按请求算出保存后的状态 → 校验 → 写回」，那两次读写必须在同一条行锁
        内，否则两个并发请求各自基于旧快照判断，可能一起写出被拒绝的状态。取不到时返回
        ``None``（由 Service 翻成 404）。
        """

        return await self._session.scalar(
            select(LlmModelRecord)
            .where(LlmModelRecord.id == model_id)
            .with_for_update()
        )

    async def get_default_model(self):
        """取当前默认模型；没有默认时返回 ``None``。"""

        return await self._session.scalar(
            select(LlmModelRecord).where(LlmModelRecord.is_default.is_(True))
        )

    async def earliest_available_model(self):
        """取「最早添加」的一条可用模型，没有可用模型时返回 ``None``。

        排序键是 ``(created_at, id)``：同一批里有多条同时变为可用时取最早添加的那一条；
        同一时刻添加的多条按 id 排，好让这个规则在任何库上都有确定答案（不用依赖库返回行的
        偶然顺序）。
        """

        return await self._session.scalar(
            select(LlmModelRecord)
            .join(LlmProviderRecord, LlmProviderRecord.id == LlmModelRecord.provider_id)
            .where(
                LlmModelRecord.enabled.is_(True),
                LlmProviderRecord.enabled.is_(True),
            )
            .order_by(LlmModelRecord.created_at, LlmModelRecord.id)
            .limit(1)
        )

    async def provider_has_default_model(self, provider_id) -> bool:
        """这条渠道下面有没有当前默认模型（渠道停用会把它一起带走，所以要问一次）。"""

        return (
            await self._session.scalar(
                select(LlmModelRecord.id)
                .where(
                    LlmModelRecord.provider_id == provider_id,
                    LlmModelRecord.is_default.is_(True),
                )
                .limit(1)
            )
        ) is not None

    async def create_model(self, **values):
        record = LlmModelRecord(id=uuid4(), **values)
        self._session.add(record)
        await self._session.commit()
        return record

    async def set_default(self, record):
        """在**同一事务**里先清掉别的默认、再把这行置真。

        库上那条部分唯一索引（``uq_llm_models_single_default``）是这一步的兜底：两个请求同时
        设默认时，落败的一方在这里撞上 ``IntegrityError``，由 Service 翻成明确的冲突错误。
        """

        await self._session.execute(
            update(LlmModelRecord)
            .where(
                LlmModelRecord.id != record.id,
                LlmModelRecord.is_default.is_(True),
            )
            .values(is_default=False)
        )
        record.is_default = True
        await self._session.commit()

    async def commit(self):
        await self._session.commit()

    async def refresh(self, record):
        """重新读回数据库写入的默认值（``created_at`` / ``updated_at`` 由 PostgreSQL 生成）。"""

        await self._session.refresh(record)
