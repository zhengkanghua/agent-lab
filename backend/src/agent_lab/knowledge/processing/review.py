"""人工审核用例；数据库保存命令后返回，解析和索引仍由持久后台消费。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol
from uuid import UUID

from agent_lab.domain.write_scope import WriteRecoveryRequiredError, WriteResourceBusyError
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.knowledge.storage import ObjectStorageError, verify_object_bytes


class ReviewRepository(Protocol):
    async def list(self, *, knowledge_base_id, source_kind, state, offset, limit): ...
    async def detail(self, document_id, processing_id=None): ...
    async def candidates(self, document_id, *, offset, limit): ...
    async def start(self, document_id, *, management_revision, processing_id=None, use_latest=False): ...
    async def save(self, processing_id, *, management_revision, candidate_revision, title, text): ...
    async def preview(self, processing_id, *, management_revision, candidate_revision): ...
    async def reject(self, processing_id, *, management_revision, candidate_revision, actor_id, conclusion): ...
    async def retry(self, processing_id, *, management_revision, candidate_revision, actor_id, index_spec): ...
    async def original(self, processing_id): ...
    async def versions(self, document_id, *, offset, limit): ...
    async def version(self, document_id, version_id): ...
    async def decisions(self, document_id, *, offset, limit): ...


ReviewWork = Callable[[], AbstractAsyncContextManager[ReviewRepository]]


class DocumentReviewApplication:
    """管理读取不要求 S3 在线；只有原件核对、下载才构造对象存储客户端。"""

    def __init__(self, work: ReviewWork, coordinator, processing, adoption, storage):
        self._work, self._coordinator = work, coordinator
        self._processing, self._adoption, self._storage = processing, adoption, storage

    async def list(self, **filters):
        async with self._work() as repository:
            return await repository.list(**filters)

    async def detail(self, document_id: UUID, processing_id: UUID | None = None):
        async with self._work() as repository:
            return await repository.detail(document_id, processing_id)

    async def candidates(self, document_id, **page):
        async with self._work() as repository:
            return await repository.candidates(document_id, **page)

    async def start(self, document_id, **command):
        async with self._work() as repository:
            return await repository.start(document_id, **command)

    async def save(self, processing_id, **command):
        async with self._work() as repository:
            return await repository.save(processing_id, **command)

    async def preview(self, processing_id, **command):
        async with self._work() as repository:
            return await repository.preview(processing_id, **command)

    async def adopt(self, processing_id, **command):
        return await self._adoption().adopt(processing_id, **command)

    async def reject(self, processing_id, **command):
        # 拒绝先撤销数据库使用资格，不等待正在执行的索引网络请求。
        async with self._work() as repository:
            return await repository.reject(processing_id, **command)

    @asynccontextmanager
    async def _recoverable_write(self):
        try:
            async with self._coordinator.hold(("sync", "index"), wait=False):
                yield
        except WriteResourceBusyError:
            raise ProcessingApplicationError("document_write_busy") from None
        except WriteRecoveryRequiredError:
            raise ProcessingApplicationError("document_write_recovery_required") from None

    async def retry(self, processing_id, **command):
        """旧远端执行者未核实退出时不能重试采用；冻结身份在重试间不变。"""
        async with self._recoverable_write():
            async with self._work() as repository:
                receipt = await repository.retry(processing_id, index_spec=self._adoption().index_spec, **command)
            if receipt.state in {"received", "receiving_failed"}:
                return await self._processing().recover_source(processing_id)
            return receipt

    async def original(self, processing_id):
        async with self._work() as repository:
            reference, filename = await repository.original(processing_id)
        try:
            data = await self._storage().get(reference.key, version_id=reference.version_id)
            verify_object_bytes(reference, data)
        except ObjectStorageError as exc:
            raise ProcessingApplicationError(exc.code) from None
        return data, filename

    async def versions(self, document_id, **page):
        async with self._work() as repository:
            return await repository.versions(document_id, **page)

    async def version(self, document_id, version_id):
        async with self._work() as repository:
            return await repository.version(document_id, version_id)

    async def decisions(self, document_id, **page):
        async with self._work() as repository:
            return await repository.decisions(document_id, **page)
