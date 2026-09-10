"""实际 Docling 与持久接收用例；替身只替换对象存储和数据库。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from agent_lab.knowledge.adapters.docling_chunker import DoclingStructuredChunker
from agent_lab.knowledge.adapters.docling_parser import DoclingDocumentParser
from agent_lab.knowledge.processing.application import DocumentProcessingApplication
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, ProcessingClaim
from agent_lab.knowledge.processing.processor import DocumentProcessor
from agent_lab.knowledge.storage import ObjectReference, ObjectStorageError


class MemoryStorage:
    def __init__(self, fail=False):
        self.data = {}
        self.fail = fail

    async def put(self, key, data, *, content_type):
        if self.fail:
            raise ObjectStorageError("object_storage_put_failed")
        self.data[key] = data
        return ObjectReference(key, len(data), sha256(data).hexdigest())

    async def get(self, key, *, version_id=None):
        if key not in self.data:
            raise ObjectStorageError("object_storage_get_failed")
        return self.data[key]


@dataclass
class Record:
    intake: object
    state: str = "received"
    revision: int = 1
    claim_token: str | None = None
    requires_review: bool = False
    stored: bool = False


class MemoryRepository:
    def __init__(self):
        self.records = {}
        self.failures = {}
        self.previews = {}
        self.fail_confirmation = False

    async def create_intent(self, intake):
        self.records[intake.id] = Record(intake)

    async def mark_stored(self, processing_id, reference):
        if self.fail_confirmation:
            raise ProcessingApplicationError("document_processing_storage_unavailable")
        record = self.records[processing_id]
        record.state = "pending"
        record.stored = True
        return True

    async def mark_receiving_failure(self, processing_id, code):
        self.records[processing_id].state = "receiving_failed"
        self.failures[processing_id] = code

    async def claim(self, processing_id=None):
        if processing_id is None:
            processing_id = next((key for key, value in self.records.items() if value.state == "pending"), None)
        record = self.records.get(processing_id)
        if record is None or record.state != "pending" or not record.stored:
            return None
        record.state = "processing"
        record.claim_token = str(uuid4())
        intake = record.intake
        return ProcessingClaim(
            id=intake.id, document_id=intake.document_id, candidate_revision=record.revision,
            claim_token=record.claim_token, source_object_key=intake.reference.key,
            source_object_version=intake.reference.version_id, source_sha256=intake.reference.sha256,
            source_size=intake.reference.size, source_mime_type=intake.mime_type,
            title=intake.metadata.get("title", "Document"), requires_review=record.requires_review,
        )

    def current(self, claim):
        record = self.records[claim.id]
        return record.state == "processing" and record.claim_token == claim.claim_token and record.revision == claim.candidate_revision

    async def save_preview(self, claim, preview, *, state):
        if not self.current(claim):
            return False
        self.records[claim.id].state = state
        self.previews[claim.id] = preview
        return True

    async def save_failure(self, claim, code, *, state):
        if not self.current(claim):
            return False
        self.records[claim.id].state = state
        self.failures[claim.id] = code
        return True

    async def get_preview(self, processing_id):
        return self.previews.get(processing_id)


@pytest.fixture(scope="module")
def processor():
    path = Path(__file__).parents[1] / ".cache/tokenizers/bge-m3"
    return DocumentProcessor(parser=DoclingDocumentParser(), chunker=DoclingStructuredChunker(tokenizer_path=path, max_tokens=128))


@pytest.fixture
def application(processor):
    repository, storage = MemoryRepository(), MemoryStorage()

    @asynccontextmanager
    async def work():
        yield repository

    return DocumentProcessingApplication(work, storage, lambda: processor), repository, storage


def test_receive_then_process_persists_actual_preview(application):
    async def verify():
        app, repository, storage = application
        receipt = await app.receive(document_id=uuid4(), source_kind="file", data=b"# Guide\n\nBody", mime_type="text/markdown", metadata={"title": "Guide"})
        assert receipt.state == "pending" and len(storage.data) == 1
        assert receipt.processing_id not in repository.previews
        processed = await app.process(receipt.processing_id)
        assert processed.state == "ready"
        assert (await app.preview(receipt.processing_id)).document.body == "# Guide\n\nBody"
        assert await app.process(receipt.processing_id) is None
    asyncio.run(verify())


@pytest.mark.parametrize("data", [b"", b"\xff", b"text\0", b"# Title only"])
def test_malformed_or_bodyless_source_is_preserved_for_review(application, data):
    async def verify():
        app, repository, storage = application
        receipt = await app.receive(document_id=uuid4(), source_kind="file", data=data, mime_type="text/markdown")
        processed = await app.process(receipt.processing_id)
        assert processed.state == repository.records[receipt.processing_id].state == "review"
        assert list(storage.data.values()) == [data]
        assert await app.process(receipt.processing_id) is None
    asyncio.run(verify())


def test_object_storage_failure_keeps_trackable_intent(application):
    async def verify():
        app, repository, storage = application
        storage.fail = True
        with pytest.raises(ProcessingApplicationError, match="document_source_storage_failed"):
            await app.receive(document_id=uuid4(), source_kind="file", data=b"text", mime_type="text/plain")
        record = next(iter(repository.records.values()))
        assert record.state == "receiving_failed" and not record.stored
        assert record.intake.reference.key
        assert list(repository.failures.values()) == ["object_storage_put_failed"]
    asyncio.run(verify())


def test_database_confirmation_failure_does_not_acknowledge_storage_success(application):
    async def verify():
        app, repository, storage = application
        repository.fail_confirmation = True
        with pytest.raises(ProcessingApplicationError, match="document_processing_storage_unavailable"):
            await app.receive(document_id=uuid4(), source_kind="file", data=b"text", mime_type="text/plain")
        record = next(iter(repository.records.values()))
        assert record.state == "received"
        assert storage.data[record.intake.reference.key] == b"text"
        assert await app.process() is None
    asyncio.run(verify())


def test_object_bytes_are_verified_before_parsing(application):
    async def verify():
        app, repository, storage = application
        receipt = await app.receive(document_id=uuid4(), source_kind="file", data=b"text", mime_type="text/plain")
        storage.data[next(iter(storage.data))] = b"tampered"
        assert (await app.process(receipt.processing_id)).state == "failed"
        assert repository.failures[receipt.processing_id] == "object_storage_content_mismatch"
        assert receipt.processing_id not in repository.previews
    asyncio.run(verify())


def test_late_preview_cannot_overwrite_newer_candidate(application):
    async def verify():
        app, repository, storage = application
        receipt = await app.receive(document_id=uuid4(), source_kind="file", data=b"text", mime_type="text/plain")
        original_get = storage.get
        async def concurrent_edit(key, **kwargs):
            record = repository.records[receipt.processing_id]
            record.revision += 1
            record.state = "pending"
            record.claim_token = None
            return await original_get(key, **kwargs)
        storage.get = concurrent_edit
        assert await app.process(receipt.processing_id) is None
        assert receipt.processing_id not in repository.previews
        assert repository.records[receipt.processing_id].state == "pending"
    asyncio.run(verify())


def test_manual_review_is_not_auto_accepted_after_successful_preview(application):
    async def verify():
        app, repository, storage = application
        receipt = await app.receive(document_id=uuid4(), source_kind="file", data=b"text", mime_type="text/plain")
        repository.records[receipt.processing_id].requires_review = True
        assert (await app.process(receipt.processing_id)).state == "review"
        assert await app.preview(receipt.processing_id) is not None
    asyncio.run(verify())
