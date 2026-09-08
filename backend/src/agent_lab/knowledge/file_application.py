"""文件资料用例复用既有索引排队与删除待办，不在上传请求内执行 Embedding。"""

import logging
from contextlib import asynccontextmanager
from uuid import UUID

from agent_lab.domain.write_scope import WriteRecoveryRequiredError, WriteResourceBusyError
from agent_lab.knowledge.files import FileDocumentError, FileDocumentWork, TextFile
from agent_lab.knowledge.ports import KnowledgeWriteCoordinator

logger = logging.getLogger(__name__)


class FileDocumentService:
    def __init__(self, work: FileDocumentWork, coordinator: KnowledgeWriteCoordinator, deletion_store) -> None:
        self._work = work
        self._coordinator = coordinator
        self._deletion_store = deletion_store

    @asynccontextmanager
    async def _write(self, resources=("sync",)):
        try:
            async with self._coordinator.hold(resources, wait=False):
                async with self._work() as repository:
                    yield repository
        except WriteResourceBusyError:
            raise FileDocumentError("file_write_busy") from None
        except WriteRecoveryRequiredError:
            raise FileDocumentError("file_write_recovery_required") from None

    async def list(self, *, knowledge_base_id: UUID | None, offset: int, limit: int):
        async with self._work() as repository:
            return await repository.list(knowledge_base_id=knowledge_base_id, offset=offset, limit=limit)

    async def upload(self, file: TextFile, knowledge_base_id: UUID):
        async with self._write() as repository:
            return await repository.create(file, knowledge_base_id)

    async def replace(self, document_id: UUID, file: TextFile, revision: int):
        async with self._write() as repository:
            return await repository.replace(document_id, file, revision)

    async def retry(self, document_id: UUID, revision: int):
        async with self._write() as repository:
            return await repository.retry(document_id, revision)

    async def delete(self, document_id: UUID, revision: int) -> None:
        """删除确认跨过 Qdrant 和 PostgreSQL 后才报告成功；失败保留同一待办。"""
        async with self._write(("sync", "index")) as repository:
            record = await repository.prepare_deletion(document_id, revision)
            try:
                if not record.qdrant_deleted:
                    async with self._deletion_store() as store:
                        await store.delete_by_document_ids([str(document_id)])
                    await repository.mark_qdrant_deleted([record])
                await repository.finish([record])
            except Exception as exc:
                logger.error("文件删除未完成 error_type=%s", type(exc).__name__)
                try:
                    await repository.record_error([record], type(exc).__name__)
                except Exception as record_error:
                    logger.error("文件删除错误保存失败 error_type=%s", type(record_error).__name__)
                raise FileDocumentError("file_delete_failed") from None
