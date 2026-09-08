"""文件边界、实际替换算法和 HTTP 权限；真实数据库互斥另由门控集成验证。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from agent_lab.api.file_documents import get_file_document_service
from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.adapters.files import PostgresFileDocumentRepository
from agent_lab.knowledge.adapters.text_files import markdown_index_text, parse_text_file
from agent_lab.knowledge.file_application import FileDocumentService
from agent_lab.knowledge.files import FileDocument, FileDocumentError, MAX_FILE_BYTES
from agent_lab.knowledge.document_contracts import DocumentDeletion
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_superuser
from tests.test_auth import auth_app, user
from tests.test_document_pipeline import build_record


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize("name,data,code", [
    ("notes.pdf", b"text", "file_format_unsupported"),
    ("notes.txt", b"\xff", "file_encoding_invalid"),
    ("notes.md", b" \n\t", "file_empty"),
    ("notes.txt", b"\xef\xbb\xbf", "file_empty"),
    ("notes.txt", b"text\0", "file_content_invalid"),
    ("notes.txt", b"a" * (MAX_FILE_BYTES + 1), "file_too_large"),
    ("a" * 256 + ".md", b"text", "file_name_invalid"),
], ids=["format", "encoding", "whitespace", "bom-only", "null-byte", "size", "name"])
def test_invalid_file_is_explicit(name, data, code):
    with pytest.raises(FileDocumentError) as caught:
        parse_text_file(name, data)
    assert caught.value.code == code


def test_bom_and_newlines_normalize_without_losing_indentation_or_repetition():
    content = "# 操作\n\n重复段落\n\n重复段落\n\n```py\nif True:\n    print('原样')\n```\n\n| 项目 | 数量 |\n| --- | --- |\n| A | 12 |"
    plain = parse_text_file("guide.md", content.encode())
    bom = parse_text_file("guide.md", ("\ufeff" + content.replace("\n", "\r\n")).encode())
    assert plain == bom
    assert plain.content_text == content
    indexed = markdown_index_text(content)
    assert indexed.count("重复段落") == 2
    assert "    print('原样')" in indexed
    assert "数量" in indexed and "12" in indexed
    assert "图片内容未读取" in markdown_index_text("![操作示意](https://example.com/image.png)")
    assert len(parse_text_file("limit.txt", b"x" * MAX_FILE_BYTES).content_text) == MAX_FILE_BYTES


def test_whitespace_filename_stem_still_produces_a_displayable_title():
    file = parse_text_file("   .txt", b"content")
    assert file.filename == "   .txt"
    assert file.title == ".txt"


def repository_case(status=ProcessingStatus.INDEXED):
    file = parse_text_file("guide.md", "# 旧资料\n\n正文".encode())
    document = build_record(content_text=file.content_text)
    document.source_id = None
    document.source = None
    document.upload_filename = file.filename
    document.title = file.title
    document.mime_type = file.mime_type
    document.content_hash = file.content_hash
    document.index_revision = 3
    document.processing_status = status
    document.updated_at = datetime(2026, 9, 1, tzinfo=UTC)
    knowledge_base = KnowledgeBaseRecord(
        id=document.knowledge_base_id, key="news", name="新闻", description=None, is_active=True,
        created_at=document.updated_at, updated_at=document.updated_at,
    )
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[document, knowledge_base]), get=AsyncMock(return_value=None),
        commit=AsyncMock(), flush=AsyncMock(), add=Mock(),
    )
    return file, document, knowledge_base, session, PostgresFileDocumentRepository(session)


@pytest.mark.parametrize("status", [ProcessingStatus.INDEXED, ProcessingStatus.FAILED, ProcessingStatus.PROCESSING])
def test_identical_replace_preserves_revision_timestamp_and_processing_state(status):
    file, document, _, _, repository = repository_case(status)
    before = document.updated_at
    view = run(repository.replace(document.id, file, 3))
    assert view.revision == 3 and view.updated_at == before and view.processing_status == status


@pytest.mark.parametrize("name,body", [("renamed.md", "# 旧资料\n\n正文"), ("guide.txt", "# 旧资料\n\n正文"), ("guide.md", "新正文")])
@pytest.mark.parametrize("status", [ProcessingStatus.INDEXED, ProcessingStatus.PROCESSING])
def test_replacement_keeps_identity_but_tracks_indexable_changes(name, body, status):
    _, document, _, _, repository = repository_case(status)
    view = run(repository.replace(document.id, parse_text_file(name, body.encode()), 3))
    assert view.document_id == document.id and view.knowledge_base_id == document.knowledge_base_id
    assert view.revision == 4 and document.content_text == body and view.upload_filename == name
    assert view.processing_status == (ProcessingStatus.PROCESSING if status == ProcessingStatus.PROCESSING else ProcessingStatus.PENDING)


@pytest.mark.parametrize("failure", ["revision", "source", "deletion"])
def test_replacement_conflicts_do_not_mutate_valid_content(failure):
    _, document, _, session, repository = repository_case()
    if failure == "source": document.source_id = uuid4()
    if failure == "deletion": session.get.return_value = object()
    previous = (document.content_text, document.index_revision)
    with pytest.raises(FileDocumentError):
        run(repository.replace(document.id, parse_text_file("new.txt", b"changed"), 2 if failure == "revision" else 3))
    assert (document.content_text, document.index_revision) == previous
    session.commit.assert_not_awaited()


def test_same_name_uploads_create_distinct_documents_and_retry_keeps_revision():
    file, document, knowledge_base, session, repository = repository_case(ProcessingStatus.FAILED)
    session.scalar.side_effect = None
    session.scalar.return_value = knowledge_base
    first = run(repository.create(file, knowledge_base.id))
    second = run(repository.create(file, knowledge_base.id))
    assert first.document_id != second.document_id
    assert first.processing_status == second.processing_status == ProcessingStatus.PENDING
    session.scalar.side_effect = [document, knowledge_base]
    retried = run(repository.retry(document.id, 3))
    assert retried.document_id == document.id and retried.revision == 3
    assert retried.processing_status == ProcessingStatus.PENDING


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
            replaced = await client.put(f"/file-documents/{view.document_id}/file", data={"revision": "1"}, files={"file": ("new.md", b"# Updated")})
            assert replaced.status_code == 200
            assert service.replace.call_args.args[0] == view.document_id
            assert service.replace.call_args.args[1].filename == "new.md"
            assert service.replace.call_args.args[2] == 1
            service.replace.reset_mock()
            invalid = await client.put(f"/file-documents/{view.document_id}/file", data={"revision": "1"}, files={"file": ("bad.txt", b"\xff")})
            assert invalid.status_code == 422 and invalid.json()["code"] == "file_encoding_invalid"
            service.replace.assert_not_awaited()
    run(verify())


@pytest.mark.parametrize("method,path", [("GET", "/file-documents"), ("POST", "/file-documents"), ("PUT", "/file-documents/{id}/file"), ("POST", "/file-documents/{id}/retry"), ("DELETE", "/file-documents/{id}")])
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
        repository = SimpleNamespace(prepare_deletion=AsyncMock(return_value=record), mark_qdrant_deleted=AsyncMock(), finish=AsyncMock(), record_error=AsyncMock())
        store = SimpleNamespace(delete_by_document_ids=AsyncMock(side_effect=RuntimeError("secret")))
        @asynccontextmanager
        async def work(): yield repository
        @asynccontextmanager
        async def deletion_store(): yield store
        @asynccontextmanager
        async def hold(*_args, **_kwargs): yield
        service = FileDocumentService(work, SimpleNamespace(hold=hold), deletion_store)
        with pytest.raises(FileDocumentError) as caught:
            await service.delete(record.document_id, 1)
        assert caught.value.code == "file_delete_failed"
        repository.finish.assert_not_awaited()
        assert repository.record_error.call_args.args[1] == "RuntimeError"
        from dataclasses import replace
        repository.prepare_deletion.return_value = replace(record, qdrant_deleted=True)
        await service.delete(record.document_id, 1)
        assert store.delete_by_document_ids.await_count == 1
        repository.finish.assert_awaited_once()
    run(verify())
