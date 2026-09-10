"""整篇资料删除，上传入口与统一管理共用；拒绝不调用此能力。"""

import logging

from agent_lab.domain.write_scope import WriteRecoveryRequiredError, WriteResourceBusyError
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError

logger = logging.getLogger(__name__)


async def delete_originals(repository, storage_factory, records):
    """按冻结引用逐项保存删除确认，同一 Document 的共享原件只删除一次。"""
    if not any(record.objects for record in records):
        return
    storage = storage_factory()
    for record in records:
        for reference in record.objects:
            await storage.delete(reference.key, version_id=reference.version_id)
            await repository.mark_object_deleted(record.document_id, reference)


class DocumentDeletionApplication:
    def __init__(self, work, coordinator, point_store, storage):
        self._work, self._coordinator = work, coordinator
        self._point_store, self._storage = point_store, storage

    async def delete(self, document_id, *, revision, management_revision, require_file=False):
        try:
            async with self._coordinator.hold(("sync", "index"), wait=False):
                async with self._work() as repository:
                    record = await repository.prepare_explicit(document_id, revision=revision,
                        management_revision=management_revision, require_file=require_file)
                    try:
                        await repository.verify([record])
                        if not record.qdrant_deleted:
                            async with self._point_store() as store:
                                await store.delete_by_document_ids([str(document_id)])
                            await repository.mark_qdrant_deleted([record])
                        await delete_originals(repository, self._storage, [record])
                        await repository.finish([record])
                    except Exception as exc:
                        logger.error("文档删除未完成 document_id=%s error_type=%s", document_id, type(exc).__name__)
                        await repository.record_error([record], type(exc).__name__)
                        raise ProcessingApplicationError("document_delete_failed") from None
        except WriteResourceBusyError:
            raise ProcessingApplicationError("document_write_busy") from None
        except WriteRecoveryRequiredError:
            raise ProcessingApplicationError("document_write_recovery_required") from None
