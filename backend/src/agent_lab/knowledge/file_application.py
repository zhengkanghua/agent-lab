"""文件入口持久接收候选；解析与正式采用交给统一处理能力。"""

import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID, uuid4

from agent_lab.domain.write_scope import WriteRecoveryRequiredError, WriteResourceBusyError
from agent_lab.knowledge.files import FileDocumentError, FileDocumentWork, TextFile
from agent_lab.knowledge.ports import KnowledgeWriteCoordinator
from agent_lab.knowledge.processing.lifecycle import SourceIntake

logger = logging.getLogger(__name__)


class FileDocumentService:
    def __init__(self, work: FileDocumentWork, coordinator: KnowledgeWriteCoordinator, deletion_store, processing) -> None:
        self._work = work
        self._coordinator = coordinator
        self._deletion_store = deletion_store
        self._processing = processing

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
        processing = self._processing()
        intake = self._intake(uuid4(), file)
        async with self._write() as repository:
            view = await repository.create(file, knowledge_base_id, intake)
            receipt = await processing.store_source(intake, file.raw_bytes)
        return replace(view, candidate_state=receipt.state)

    async def replace(self, document_id: UUID, file: TextFile, revision: int, management_revision: int):
        processing = self._processing()
        intake = self._intake(document_id, file)
        async with self._write() as repository:
            view = await repository.replace(document_id, file, revision, management_revision, intake)
            receipt = await processing.store_source(intake, file.raw_bytes)
        return replace(view, candidate_state=receipt.state)

    @staticmethod
    def _intake(document_id: UUID, file: TextFile) -> SourceIntake:
        return SourceIntake.prepare(
            document_id=document_id, source_kind="file", data=file.raw_bytes,
            mime_type=file.mime_type, metadata={"title": file.title, "filename": file.filename},
        )

    async def retry(self, document_id: UUID, revision: int, management_revision: int):
        async with self._write() as repository:
            return await repository.retry(document_id, revision, management_revision)

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
