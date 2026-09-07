"""Source 配置应用用例；通过端口协调写入和事务，不依赖具体数据库。"""

from dataclasses import replace
from uuid import UUID

from agent_lab.domain.write_scope import WriteResourceBusyError, WriteRecoveryRequiredError
from agent_lab.knowledge.contracts import SourceView
from agent_lab.knowledge.domain import SourceBindingError, require_active_knowledge_base
from agent_lab.knowledge.ports import KnowledgeWriteCoordinator, SourceBindingUnitOfWorkFactory


class SourceBindingService:
    """所有入口共用绑定规则；业务事务退出后才释放跨进程写占用。"""

    def __init__(self, work: SourceBindingUnitOfWorkFactory, coordinator: KnowledgeWriteCoordinator) -> None:
        self._work = work
        self._coordinator = coordinator

    async def list_sources(self) -> list[SourceView]:
        async with self._work() as work:
            return await work.sources.list()

    async def bind(self, source_id: UUID, knowledge_base_id: UUID | None) -> SourceView:
        try:
            async with self._coordinator.hold(("sync", "index"), wait=False):
                async with self._work() as work:
                    source = await work.sources.get_for_update(source_id)
                    if source is None:
                        raise SourceBindingError("source_not_found", "来源不存在。")
                    if source.knowledge_base_id == knowledge_base_id:
                        return source
                    if await work.sources.has_documents(source_id):
                        raise SourceBindingError("source_binding_conflict", "来源已有 Document，不能修改 KnowledgeBase 绑定。")
                    if await work.sources.has_pending_deletions(source_id):
                        raise SourceBindingError("source_binding_conflict", "来源存在删除待办，不能修改 KnowledgeBase 绑定。")
                    target = None
                    if knowledge_base_id is not None:
                        target = require_active_knowledge_base(await work.knowledge_bases.get_for_update(knowledge_base_id))
                    updated = replace(
                        source, knowledge_base_id=knowledge_base_id,
                        knowledge_base_key=target.key if target else None,
                        sync_checkpoint=None, sync_checkpoint_updated_at=None,
                    )
                    await work.sources.save_binding(updated)
                    await work.commit()
                    return updated
        except WriteResourceBusyError:
            raise SourceBindingError("source_binding_conflict", "来源存在未完成写入，暂时不能修改绑定。") from None
        except WriteRecoveryRequiredError:
            raise SourceBindingError("source_write_recovery_required", "写操作结果需要人工核实，暂时不能修改绑定。") from None


__all__ = ["SourceBindingError", "SourceBindingService", "SourceView"]
