"""重建复用已采用 Chunk；发布与索引指向之间的窗口必须可检测、可恢复。"""

import logging

from agent_lab.domain.write_scope import WriteRecoveryRequiredError, ensure_write_confirmed, write_scope
from agent_lab.knowledge.document_contracts import IndexRebuildResult
from agent_lab.knowledge.ports import KnowledgeWriteCoordinator, RebuildRepository, RebuildTarget

logger = logging.getLogger(__name__)


def _require_recovery():
    scope = write_scope.get()
    if scope is not None:
        scope.uncertain = True


class IndexRebuildService:
    """构建时持有既有写资源，拒绝仍可生效；正文、预览和已采用历史始终不改写。"""

    def __init__(self, repository: RebuildRepository, target: RebuildTarget,
                 coordinator: KnowledgeWriteCoordinator) -> None:
        self._repository, self._target, self._coordinator = repository, target, coordinator

    async def rebuild(self, *, batch_size: int = 50) -> IndexRebuildResult:
        if batch_size < 1:
            raise ValueError("batch_size 必须大于零")
        async with self._coordinator.hold(("sync", "index"), wait=False):
            await self._repository.require_ready()
            await self._target.prepare()
            documents, points, after = [], 0, None
            try:
                while batch := await self._repository.list_documents(after=after, limit=batch_size):
                    for document_id in batch:
                        prepared = await self._repository.prepare_document(document_id,
                            index_spec=self._target.index_spec, location=self._target.location)
                        if prepared is None:
                            continue
                        snapshot, target = prepared
                        documents.append(snapshot)
                        ensure_write_confirmed()
                        points += await self._target.write_document(target)
                        await self._repository.mark_prepared(snapshot.processing_id)
                    after = batch[-1]
                await self._target.verify_total(points)
                await self._repository.begin_publication(documents)
            except BaseException:
                try:
                    await self._repository.fail_build(documents)
                except Exception as exc:
                    logger.error("重建失败记录未保存 error_type=%s", type(exc).__name__)
                raise
            try:
                ensure_write_confirmed()
                await self._target.publish()
                await self._repository.mark_rebuilt(documents, schema_version=self._target.schema_version)
            except BaseException as exc:
                _require_recovery()
                if isinstance(exc, Exception):
                    raise WriteRecoveryRequiredError() from exc
                raise
            return IndexRebuildResult(len(documents), points)

    async def recover(self) -> IndexRebuildResult:
        """显式恢复命令只核对既有写入，不重新 Embedding 或再次发布 Alias。"""
        async with self._coordinator.hold(("sync", "index"), wait=False):
            pending = await self._repository.pending_publication(self._target.collection_name)
            documents = [snapshot for snapshot, _, _ in pending]
            previous = {location["previous_collection"] for _, _, location in pending}
            current = await self._target.current_target()
            try:
                if current == self._target.collection_name:
                    points = 0
                    for _, target, _ in pending:
                        points += await self._target.verify_document(target)
                    await self._target.verify_total(points)
                    await self._repository.mark_rebuilt(documents, schema_version=self._target.schema_version)
                    return IndexRebuildResult(len(documents), points)
                if previous == {current}:
                    await self._repository.abort_publication(documents)
                    return IndexRebuildResult(0, 0, published=False)
                raise WriteRecoveryRequiredError()
            except BaseException as exc:
                _require_recovery()
                if isinstance(exc, Exception):
                    raise WriteRecoveryRequiredError() from exc
                raise
