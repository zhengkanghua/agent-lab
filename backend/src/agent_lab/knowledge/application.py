"""知识库配置用例，事务由端口提供，核心不创建外部客户端。"""

from dataclasses import replace
from uuid import UUID

from agent_lab.knowledge.contracts import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseUpdateRequest,
)
from agent_lab.knowledge.domain import (
    KnowledgeBase,
    KnowledgeBaseNotFoundError,
    NoActiveKnowledgeBasesError,
    require_active_knowledge_base,
)
from agent_lab.knowledge.ports import KnowledgeBaseUnitOfWorkFactory
from agent_lab.knowledge.scope import (
    KnowledgeBaseSelection, KnowledgeBaseSummary, ResolvedKnowledgeBaseScope,
)


class KnowledgeBaseService:
    """管理逻辑知识库配置；不迁移 Document、不执行索引或物理删除。"""

    def __init__(self, unit_of_work: KnowledgeBaseUnitOfWorkFactory) -> None:
        self._unit_of_work = unit_of_work

    async def list(self, *, include_inactive: bool = False) -> list[KnowledgeBase]:
        """默认只列启用库；管理调用方可明确读取停用配置。"""

        async with self._unit_of_work() as work:
            return await work.repository.list(include_inactive=include_inactive)

    async def require_active(self, knowledge_base_id: UUID) -> KnowledgeBase:
        """查询当前启用状态，事务在调用上游搜索之前结束，不持有行锁。"""

        async with self._unit_of_work() as work:
            return require_active_knowledge_base(await work.repository.get(knowledge_base_id))

    async def create(self, request: KnowledgeBaseCreateRequest) -> KnowledgeBase:
        """创建后提交；重复稳定键由适配器映射为业务冲突。"""

        async with self._unit_of_work() as work:
            knowledge_base = await work.repository.create(request)
            await work.commit()
            return knowledge_base

    async def resolve_scope(self, selection: KnowledgeBaseSelection) -> ResolvedKnowledgeBaseScope:
        """一次目录读取形成快照，避免多库逐项读取期间配置变化混入同一查询。"""

        records = await self.list(include_inactive=selection.mode == "selected")
        if selection.mode == "selected":
            by_id = {item.id: item for item in records}
            records = [
                require_active_knowledge_base(by_id.get(identifier))
                for identifier in dict.fromkeys(selection.knowledge_base_ids)
            ]
        if not records:
            raise NoActiveKnowledgeBasesError()
        return ResolvedKnowledgeBaseScope(
            mode=selection.mode,
            knowledge_bases=tuple(KnowledgeBaseSummary.model_validate(item) for item in records),
        )

    async def update(
        self, knowledge_base_id: UUID, request: KnowledgeBaseUpdateRequest
    ) -> KnowledgeBase:
        """锁定并合并当前快照，重复提交相同值不改变更新时间。"""

        async with self._unit_of_work() as work:
            current = await work.repository.get_for_update(knowledge_base_id)
            if current is None:
                raise KnowledgeBaseNotFoundError()
            updated = replace(current, **request.model_dump(exclude_unset=True))
            if updated == current:
                return current
            result = await work.repository.update(updated)
            await work.commit()
            return result
