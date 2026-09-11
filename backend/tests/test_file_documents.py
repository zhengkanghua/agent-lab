"""文件边界、实际替换算法和 HTTP 权限；真实数据库互斥另由门控集成验证。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from agent_lab.api.file_documents import get_file_document_service
from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.adapters.files import PostgresFileDocumentRepository
from agent_lab.knowledge.adapters.text_files import parse_text_file
from agent_lab.knowledge.file_application import FileDocumentService
from agent_lab.knowledge.deletion import DocumentDeletionApplication
from agent_lab.knowledge.files import FileDocument, FileDocumentError, MAX_FILE_BYTES
from agent_lab.knowledge.document_contracts import DocumentDeletion
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, ProcessingReceipt, SourceIntake
from agent_lab.models.document_processing import DocumentProcessingRecord
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_superuser
from tests.test_auth import auth_app, user
from tests.document_fixtures import build_record


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize("name,data,code", [
    ("notes.pdf", b"text", "file_format_unsupported"),
    ("notes.txt", b"a" * (MAX_FILE_BYTES + 1), "file_too_large"),
    ("a" * 256 + ".md", b"text", "file_name_invalid"),
], ids=["format", "size", "name"])
def test_invalid_file_is_explicit(name, data, code):
    with pytest.raises(FileDocumentError) as caught:
        parse_text_file(name, data)
    assert caught.value.code == code


def test_intake_preserves_raw_bom_newlines_indentation_and_repetition():
    content = "# 操作\n\n重复段落\n\n重复段落\n\n```py\nif True:\n    print('原样')\n```\n\n| 项目 | 数量 |\n| --- | --- |\n| A | 12 |"
    plain = parse_text_file("guide.md", content.encode())
    bom = parse_text_file("guide.md", ("\ufeff" + content.replace("\n", "\r\n")).encode())
    assert plain.raw_bytes == content.encode()
    assert bom.raw_bytes == ("\ufeff" + content.replace("\n", "\r\n")).encode()
    assert len(parse_text_file("limit.txt", b"x" * MAX_FILE_BYTES).raw_bytes) == MAX_FILE_BYTES


@pytest.mark.parametrize("data", [b"", b"\xff", b" \n\t", b"\xef\xbb\xbf", b"text\0"])
def test_content_anomalies_are_preserved_for_background_processing(data):
    assert parse_text_file("problem.txt", data).raw_bytes == data


def test_whitespace_filename_stem_still_produces_a_displayable_title():
    file = parse_text_file("   .txt", b"content")
    assert file.filename == "   .txt"
    assert file.title == ".txt"


def repository_case(status=ProcessingStatus.INDEXED):
    file = parse_text_file("guide.md", "# 旧资料\n\n正文".encode())
    document = build_record(content_text=file.raw_bytes.decode())
    document.source_id = None
    document.source = None
    document.upload_filename = file.filename
    document.title = file.title
    document.mime_type = file.mime_type
    document.content_hash = sha256(file.raw_bytes).hexdigest()
    document.index_revision = 3
    document.processing_status = status
    document.current_version_id = uuid4()
    document.management_revision = 7
    document.usage_status = "active"
    document.updated_at = datetime(2026, 9, 1, tzinfo=UTC)
    knowledge_base = KnowledgeBaseRecord(
        id=document.knowledge_base_id, key="news", name="新闻", description=None, is_active=True,
        created_at=document.updated_at, updated_at=document.updated_at,
    )
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[document.knowledge_base_id, knowledge_base, document, knowledge_base]), get=AsyncMock(return_value=None),
        commit=AsyncMock(), flush=AsyncMock(), add=Mock(),
    )
    return file, document, knowledge_base, session, PostgresFileDocumentRepository(session)


def intake_for(document_id, file):
    return SourceIntake.prepare(document_id=document_id, source_kind="file", data=file.raw_bytes,
                                mime_type=file.mime_type, metadata={"title": file.title, "filename": file.filename})


@pytest.mark.parametrize("name,body", [("renamed.md", "# 旧资料\n\n正文"), ("guide.txt", "# 旧资料\n\n正文"), ("guide.md", "新正文")])
@pytest.mark.parametrize("status", [ProcessingStatus.INDEXED, ProcessingStatus.PROCESSING])
def test_replacement_keeps_adopted_body_hash_metadata_and_revision(name, body, status):
    _, document, _, session, repository = repository_case(status)
    previous = (document.content_text, document.content_hash, document.title, document.mime_type, document.current_version_id)
    file = parse_text_file(name, body.encode())
    intake = intake_for(document.id, file)
    view = run(repository.replace(document.id, file, 3, 7, intake))
    assert view.document_id == document.id and view.knowledge_base_id == document.knowledge_base_id
    assert view.revision == 3 and view.management_revision == 8
    assert (document.content_text, document.content_hash, document.title, document.mime_type, document.current_version_id) == previous
    assert view.processing_status == status and view.candidate_state == "received"
    candidate = session.add.call_args.args[0]
    assert candidate.id == view.processing_id == intake.id
    assert candidate.source_metadata == {"title": file.title, "filename": name}
    assert candidate.source_sha256 == sha256(file.raw_bytes).hexdigest()


@pytest.mark.parametrize("failure", ["revision", "management_revision", "source", "deletion"])
def test_replacement_conflicts_do_not_mutate_valid_content(failure):
    _, document, _, session, repository = repository_case()
    if failure == "source": document.source_id = uuid4()
    if failure == "deletion": session.get.return_value = object()
    previous = (document.content_text, document.index_revision)
    file = parse_text_file("new.txt", b"changed")
    with pytest.raises(FileDocumentError):
        run(repository.replace(document.id, file, 2 if failure == "revision" else 3,
                               6 if failure == "management_revision" else 7, intake_for(document.id, file)))
    assert (document.content_text, document.index_revision) == previous
    session.commit.assert_not_awaited()


def test_same_name_uploads_create_distinct_documents_with_no_adopted_body():
    file, document, knowledge_base, session, repository = repository_case(ProcessingStatus.FAILED)
    session.scalar.side_effect = None
    session.scalar.return_value = knowledge_base
    first = run(repository.create(file, knowledge_base.id, intake_for(uuid4(), file)))
    second = run(repository.create(file, knowledge_base.id, intake_for(uuid4(), file)))
    assert first.document_id != second.document_id
    assert first.processing_status == second.processing_status == ProcessingStatus.PENDING
    assert first.current_version_id is None and first.content_hash is None
    documents = [call.args[0] for call in session.add.call_args_list if not isinstance(call.args[0], DocumentProcessingRecord)]
    assert all(item.content_text is None for item in documents)


def file_view():
    return FileDocument(uuid4(), uuid4(), "资料", True, "note.txt", "note", "text/plain", "a" * 64,
                        1, datetime.now(UTC), ProcessingStatus.PENDING, None, False, None)


def test_http_upload_replace_and_invalid_file_boundaries():
    async def verify():
        view = file_view()
        service = SimpleNamespace(upload=AsyncMock(return_value=view), replace=AsyncMock(return_value=view))
        app = allow_superuser(create_offline_app())
        app.dependency_overrides[get_file_document_service] = lambda: service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            created = await client.post("/file-documents", data={"knowledge_base_id": str(view.knowledge_base_id)}, files={"file": ("note.txt", b"saved")})
            assert created.status_code == 201 and created.json()["processing_status"] == "pending"
            replaced = await client.put(f"/file-documents/{view.document_id}/file", data={"revision": "1", "management_revision": "1"}, files={"file": ("new.md", b"# Updated")})
            assert replaced.status_code == 200
            assert service.replace.call_args.args[0] == view.document_id
            assert service.replace.call_args.args[1].filename == "new.md"
            assert service.replace.call_args.args[2] == 1
            service.replace.reset_mock()
            invalid = await client.put(f"/file-documents/{view.document_id}/file", data={"revision": "1", "management_revision": "1"}, files={"file": ("bad.pdf", b"\xff")})
            assert invalid.status_code == 422 and invalid.json()["code"] == "file_format_unsupported"
            service.replace.assert_not_awaited()
    run(verify())


@pytest.mark.parametrize("method,path", [("GET", "/file-documents"), ("POST", "/file-documents"), ("PUT", "/file-documents/{id}/file"), ("DELETE", "/file-documents/{id}")])
def test_file_management_requires_superuser(method, path):
    async def verify():
        app, _ = auth_app(user())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
            login = await client.post("/auth/login", data={"username": "reader@example.com", "password": "valid-password"})
            assert login.status_code == 204
            response = await client.request(method, path.format(id=uuid4()))
            assert response.status_code == 403
    run(verify())


def test_deletion_failure_keeps_intent_and_confirmed_remote_step_is_not_replayed():
    async def verify():
        record = DocumentDeletion(uuid4(), 1, None, datetime.now(UTC), False)
        repository = SimpleNamespace(prepare_explicit=AsyncMock(return_value=record), verify=AsyncMock(), mark_qdrant_deleted=AsyncMock(), finish=AsyncMock(), record_error=AsyncMock())
        store = SimpleNamespace(delete_by_document_ids=AsyncMock(side_effect=RuntimeError("secret")))
        @asynccontextmanager
        async def work(): yield repository
        @asynccontextmanager
        async def deletion_store(): yield store
        @asynccontextmanager
        async def hold(*_args, **_kwargs): yield
        service = DocumentDeletionApplication(work, SimpleNamespace(hold=hold), deletion_store, lambda: None)
        with pytest.raises(ProcessingApplicationError) as caught:
            await service.delete(record.document_id, revision=1, management_revision=1)
        assert caught.value.code == "document_delete_failed"
        repository.finish.assert_not_awaited()
        assert repository.record_error.call_args.args[1] == "RuntimeError"
        from dataclasses import replace
        repository.prepare_explicit.return_value = replace(record, qdrant_deleted=True)
        await service.delete(record.document_id, revision=1, management_revision=1)
        assert store.delete_by_document_ids.await_count == 1
        repository.finish.assert_awaited_once()
    run(verify())


def test_upload_and_replace_confirm_raw_object_after_durable_intent():
    async def verify():
        file = parse_text_file("guide.md", b"# Guide\n\nBody")
        view = file_view()
        repository = SimpleNamespace(
            create=AsyncMock(return_value=view), replace=AsyncMock(return_value=view),
        )
        async def stored(intake, data):
            assert data == file.raw_bytes
            return ProcessingReceipt(intake.id, intake.document_id, "pending", intake.reference.sha256)
        processing = SimpleNamespace(store_source=AsyncMock(side_effect=stored))

        @asynccontextmanager
        async def work():
            yield repository

        @asynccontextmanager
        async def hold(*_args, **_kwargs):
            yield

        service = FileDocumentService(work, SimpleNamespace(hold=hold), None, lambda: processing)
        uploaded = await service.upload(file, view.knowledge_base_id)
        replaced = await service.replace(view.document_id, file, view.revision, view.management_revision)

        assert uploaded.candidate_state == replaced.candidate_state == "pending"
        assert processing.store_source.await_count == 2
        first = processing.store_source.await_args_list[0].args[0]
        assert first.source_kind == "file"
        assert repository.create.call_args.args[2] == first

    run(verify())


def test_processing_failure_uses_safe_http_contract():
    async def verify():
        app = allow_superuser(create_offline_app())
        service = SimpleNamespace(upload=AsyncMock(side_effect=ProcessingApplicationError("object_storage_not_configured")))
        app.dependency_overrides[get_file_document_service] = lambda: service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post("/file-documents", data={"knowledge_base_id": str(uuid4())}, files={"file": ("note.txt", b"text")})
            assert response.status_code == 503
            assert response.json()["code"] == "object_storage_not_configured"
    run(verify())
