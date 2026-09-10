"""原件持久接收和后台预览；正式采用与索引写入由独立用例负责。"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from agent_lab.knowledge.processing.contracts import DocumentPreview, DocumentProcessingError
from agent_lab.knowledge.processing.lifecycle import (
    ProcessingApplicationError, ProcessingReceipt, SourceIntake,
)
from agent_lab.knowledge.processing.ports import ProcessingWork
from agent_lab.knowledge.processing.processor import DocumentProcessor
from agent_lab.knowledge.storage import ObjectReference, ObjectStorage, ObjectStorageError, verify_object_bytes

logger = logging.getLogger(__name__)


class DocumentProcessingApplication:
    """HTTP 接收不加载 tokenizer，后台才在线程中取得解析与 Chunk 组件。"""

    def __init__(self, work: ProcessingWork, storage: ObjectStorage,
                 processor: Callable[[], DocumentProcessor]):
        self._work = work
        self._storage = storage
        self._processor = processor

    async def receive(self, *, document_id: UUID, source_kind: str, data: bytes,
                      mime_type: str, metadata: dict | None = None) -> ProcessingReceipt:
        intake = SourceIntake.prepare(
            document_id=document_id, source_kind=source_kind, data=data,
            mime_type=mime_type, metadata=metadata,
        )
        async with self._work() as repository:
            await repository.create_intent(intake)
        return await self.store_source(intake, data)

    async def store_source(self, intake: SourceIntake, data: bytes, *, queue_processing: bool = True) -> ProcessingReceipt:
        """入口已提交接收意图；只有原件核验与数据库确认完成才返回保存成功。"""
        try:
            verify_object_bytes(intake.reference, data)
            reference = await self._storage.put(intake.reference.key, data, content_type=intake.mime_type)
            if (reference.key, reference.size, reference.sha256) != (
                intake.reference.key, intake.reference.size, intake.reference.sha256,
            ):
                raise ObjectStorageError("object_storage_content_mismatch")
        except ObjectStorageError as exc:
            async with self._work() as repository:
                await repository.mark_receiving_failure(intake.id, exc.code)
            raise ProcessingApplicationError("document_source_storage_failed") from None
        async with self._work() as repository:
            if not await repository.mark_stored(intake.id, reference, queue_processing=queue_processing):
                raise ProcessingApplicationError("document_processing_conflict")
        return ProcessingReceipt(intake.id, intake.document_id, "pending" if queue_processing else "stored", reference.sha256)

    async def process(self, processing_id: UUID | None = None) -> ProcessingReceipt | None:
        """一次消费一条持久待办；失败留待人工处理，不在当前批次无限重领。"""
        async with self._work() as repository:
            claim = await repository.claim(processing_id)
        if claim is None:
            return None

        try:
            if claim.draft_text is None:
                data = await self._storage.get(claim.source_object_key, version_id=claim.source_object_version)
                verify_object_bytes(ObjectReference(
                    claim.source_object_key, claim.source_size, claim.source_sha256,
                    claim.source_object_version,
                ), data)
                mime_type = claim.source_mime_type
            else:
                data = claim.draft_text.encode("utf-8")
                mime_type = claim.draft_mime_type
            preview = await asyncio.to_thread(
                self._build_preview, data, mime_type=mime_type, title=claim.title,
            )
        except DocumentProcessingError as exc:
            state, failure = "review", exc.code
        except ObjectStorageError as exc:
            state, failure = "failed", exc.code
        except Exception as exc:
            logger.error("文档后台处理失败 processing_id=%s error_type=%s", claim.id, type(exc).__name__)
            state, failure = "failed", "document_processing_failed"
        else:
            state = "review" if preview.issues or claim.requires_review else "ready"
            async with self._work() as repository:
                saved = await repository.save_preview(claim, preview, state=state)
            return self._receipt(claim, state) if saved else None

        async with self._work() as repository:
            saved = await repository.save_failure(claim, failure, state=state)
        return self._receipt(claim, state, failure) if saved else None

    async def recover_source(self, processing_id: UUID) -> ProcessingReceipt:
        """只核对已保存意图指向的原件；回执丢失不触发新的对象写入。"""
        async with self._work() as repository:
            intake = await repository.get_receiving_intake(processing_id)
        if intake is None:
            raise ProcessingApplicationError("document_processing_conflict")
        try:
            reference = await self._storage.inspect(intake.reference.key, version_id=intake.reference.version_id)
            if reference is None:
                raise ProcessingApplicationError("document_source_not_found")
            if (reference.size, reference.sha256) != (intake.reference.size, intake.reference.sha256):
                raise ObjectStorageError("object_storage_content_mismatch")
        except ObjectStorageError as exc:
            raise ProcessingApplicationError(exc.code) from None
        async with self._work() as repository:
            if not await repository.mark_stored(processing_id, reference, queue_processing=intake.source_kind != "freshrss"):
                raise ProcessingApplicationError("document_processing_conflict")
        return ProcessingReceipt(processing_id, intake.document_id, "stored" if intake.source_kind == "freshrss" else "pending", reference.sha256)

    def _build_preview(self, data: bytes, *, mime_type: str, title: str) -> DocumentPreview:
        return self._processor().preview(data, mime_type=mime_type, title=title)

    @staticmethod
    def _receipt(claim, state, error_code=None):
        return ProcessingReceipt(claim.id, claim.document_id, state, claim.source_sha256, claim.candidate_revision, error_code)

    async def requeue_computations(self, *, started_before: datetime) -> int:
        """只重排超时纯计算，领取代次保证旧线程不能覆盖重排后的结果。"""
        async with self._work() as repository:
            return await repository.requeue_computations(started_before=started_before)

    async def preview(self, processing_id: UUID) -> DocumentPreview | None:
        async with self._work() as repository:
            return await repository.get_preview(processing_id)
