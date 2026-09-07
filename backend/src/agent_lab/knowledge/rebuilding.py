"""全量构建派生索引，验收完成后才发布；不依赖数据库和向量库客户端。"""

from agent_lab.domain.write_scope import WriteRecoveryRequiredError, ensure_write_confirmed, write_scope
from agent_lab.knowledge.document_contracts import IndexRebuildResult, RebuiltDocument
from agent_lab.knowledge.ports import KnowledgeWriteCoordinator, RebuildRepository, RebuildTarget


class IndexRebuildService:
    """以现有写协调冻结同步、索引和清理，保持数据库版本与新索引一致。"""

    def __init__(self, repository: RebuildRepository, target: RebuildTarget,
                 coordinator: KnowledgeWriteCoordinator) -> None:
        self._repository = repository
        self._target = target
        self._coordinator = coordinator

    async def rebuild(self, *, batch_size: int = 50) -> IndexRebuildResult:
        if batch_size < 1:
            raise ValueError("batch_size 必须大于零")
        async with self._coordinator.hold(("sync", "index"), wait=False):
            await self._repository.require_ready()
            await self._target.prepare()
            documents: list[RebuiltDocument] = []
            points = 0
            after = None
            while batch := await self._repository.list_documents(after=after, limit=batch_size):
                for document in batch:
                    ensure_write_confirmed()
                    points += await self._target.write_document(document)
                    documents.append(RebuiltDocument(
                        document.id, document.knowledge_base_id, document.index_revision, document.content_hash,
                    ))
                after = batch[-1].id

            await self._repository.verify_versions(documents)
            await self._target.verify_total(points)
            # 发布与成功快照不在同一存储事务内；此后的失败必须保留占用供人工核实。
            try:
                ensure_write_confirmed()
                await self._target.publish()
                await self._repository.mark_rebuilt(documents, schema_version=self._target.schema_version)
            except BaseException as exc:
                scope = write_scope.get()
                if scope is not None:
                    scope.uncertain = True
                if isinstance(exc, Exception):
                    raise WriteRecoveryRequiredError() from exc
                raise
            return IndexRebuildResult(len(documents), points)
