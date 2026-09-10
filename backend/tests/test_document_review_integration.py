"""人工审核的真实数据库事务和隔离索引验证；不访问业务 schema、真实模型或 FreshRSS。"""

import os

import httpx
import pytest
from sqlalchemy import func, select

from agent_lab.api.document_review import get_document_review_application
from agent_lab.knowledge.adapters.adoption import postgres_adoption_work
from agent_lab.knowledge.adapters.text_files import parse_text_file
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID as KB
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentReviewRecord, DocumentVersion
from agent_lab.models.user import UserRecord
from agent_lab.repositories.document_repository import DocumentRepository
from tests.app_helpers import create_offline_app
from tests.auth_helpers import SUPERUSER_ID, allow_superuser
from tests.test_processing_application import processor
from tests.test_processing_postgres_integration import current, hits, scenario
from tests.test_scheduler_postgres_integration import isolated_database, run

pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1", reason="需要授权隔离文档审核验证。")


async def actor(db):
    async with db.sessions() as session:
        session.add(UserRecord(id=SUPERUSER_ID, email="review@example.com", hashed_password="unused",
                               is_active=True, is_superuser=True, is_verified=True, is_environment_admin=False))
        await session.commit()


def command(detail):
    return {"management_revision": detail.document.management_revision, "candidate_revision": detail.candidate.candidate_revision}


async def draft(app, document_id, body):
    record = await current(app.db, document_id)
    receipt = await app.review.start(document_id, management_revision=record.management_revision)
    detail = await app.review.detail(document_id, receipt.processing_id)
    saved = await app.review.save(receipt.processing_id, **command(detail), title="修订标题", text=body)
    detail = await app.review.detail(document_id, saved.processing_id)
    await app.review.preview(saved.processing_id, **command(detail))
    assert (await app.processing.process(saved.processing_id)).state == "review"
    return await app.review.detail(document_id, saved.processing_id)


def test_review_http_saves_latest_draft_and_adopts_exact_preview(isolated_database, processor):
    async def verify():
        db = isolated_database
        await actor(db)
        async with scenario(db, processor) as app:
            app.db = db
            original = await app.files.upload(parse_text_file("guide.md", b"# Original\n\nOriginal body"), KB)
            await app.processing.process(original.processing_id)
            await app.adoption.process(original.processing_id)
            old = await current(db, original.document_id)
            http_app = allow_superuser(create_offline_app())
            http_app.dependency_overrides[get_document_review_application] = lambda: app.review
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=http_app), base_url="https://testserver") as http:
                started = await http.post(f"/document-management/{old.id}/draft", json={"management_revision": old.management_revision})
                assert started.status_code == 200, started.text
                identity = started.json()["processing_id"]
                detail = await app.review.detail(old.id)
                first_command = {**command(detail), "title": "人工版", "text": "# 大标题\n\n## 小标题\n\n人工修订正文。"}
                path = f"/document-management/candidates/{identity}"
                saved = await http.put(path + "/draft", json=first_command)
                assert saved.status_code == 200
                # 过期的编辑者不能覆盖最新正文，也不能采用上一次预览。
                conflict = await http.put(path + "/draft", json={**first_command, "text": "迟到编辑"})
                assert conflict.status_code == 409
                detail = await app.review.detail(old.id)
                assert detail.candidate.preview is None and detail.candidate.draft_text == first_command["text"]
                assert (await current(db, old.id)).content_hash == old.content_hash
                assert (await http.post(path + "/preview", json=command(detail))).status_code == 202
                assert (await app.processing.process(detail.candidate.processing_id)).state == "review"
                detail = await app.review.detail(old.id)
                assert await app.adoption.process(detail.candidate.processing_id) is None
                adopted = await http.post(path + "/adopt", json={**command(detail), "fingerprint": detail.candidate.preview_fingerprint})
                assert adopted.status_code == 202, adopted.text
                assert (await current(db, old.id)).content_hash == old.content_hash
                assert (await app.adoption.process(detail.candidate.processing_id)).state == "adopted"
                new = await current(db, old.id)
                assert new.content_text == first_command["text"] and new.index_revision == old.index_revision + 1
                assert (await hits(app))[0].matches[0].content_hash == new.content_hash
                versions = await http.get(f"/document-management/{old.id}/versions")
                assert versions.status_code == 200 and len(versions.json()["items"]) == 2
                history = await http.get(f"/document-management/{old.id}/reviews")
                assert history.status_code == 200 and history.json()["items"][0]["content_snapshot"]["body"] == new.content_text
                original_response = await http.get(f"/document-management/candidates/{original.processing_id}/original")
                assert original_response.content == b"# Original\n\nOriginal body"
            # 连续保存只有一份最新草稿，成功采用的历史不变。
            edited = await draft(app, old.id, "草稿一")
            for body in ("草稿二", "草稿三"):
                await app.review.save(edited.candidate.processing_id, **command(edited), title="修订标题", text=body)
                edited = await app.review.detail(old.id)
            async with db.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 2
                assert await session.scalar(select(func.count()).select_from(DocumentProcessingRecord).where(DocumentProcessingRecord.draft_text.is_not(None))) == 1
            # 来源更新保留正在编辑的草稿，显式换用才替换它。
            current_doc = await current(db, old.id)
            updated = await app.files.replace(old.id, parse_text_file("guide.md", b"# Source update\n\nNew source body"), current_doc.index_revision, current_doc.management_revision)
            await app.processing.process(updated.processing_id)
            detail = await app.review.detail(old.id)
            assert detail.candidate.draft_text == "草稿三" and detail.latest_source.processing_id == updated.processing_id
            await app.review.start(old.id, management_revision=detail.document.management_revision, use_latest=True)
            detail = await app.review.detail(old.id)
            assert "New source body" in detail.candidate.draft_text
            assert (await current(db, old.id)).content_text == new.content_text
    run(verify())


def test_reject_stops_inflight_adoption_and_keeps_records_for_correction(isolated_database, processor):
    async def verify():
        db = isolated_database
        await actor(db)
        async with scenario(db, processor) as app:
            app.db = db
            first = await app.files.upload(parse_text_file("guide.txt", b"original adopted body"), KB)
            await app.processing.process(first.processing_id)
            await app.adoption.process(first.processing_id)
            detail = await draft(app, first.document_id, "人工修订")
            await app.review.adopt(detail.candidate.processing_id, **command(detail), fingerprint=detail.candidate.preview_fingerprint, actor_id=SUPERUSER_ID)
            async with postgres_adoption_work(db.sessions) as repository:
                target = await repository.claim_next(app.spec.collection_metadata, detail.candidate.processing_id)
            await app.store.prepare_candidate(target, [[1.0, 0.0, 0.0] for _ in target.preview.chunk_result.chunks])
            detail = await app.review.detail(first.document_id)
            await app.review.reject(detail.candidate.processing_id, **command(detail), actor_id=SUPERUSER_ID, conclusion="需要重新核对")
            async with postgres_adoption_work(db.sessions) as repository:
                assert not await repository.complete(target)
            async with db.sessions() as session:
                assert await DocumentRepository(session).get_with_source(first.document_id) is None
                assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
            assert await hits(app) == []
            detail = await app.review.detail(first.document_id)
            assert detail.document.usage_status == "rejected"
            with pytest.raises(ProcessingApplicationError, match="document_draft_required"):
                await app.review.retry(detail.candidate.processing_id, **command(detail), actor_id=SUPERUSER_ID)
            assert await app.adoption.cleanup_one()
            assert await app.adoption.cleanup_one()
            # 修正后的草稿产生新目标，拒绝时的正文与结论保留。
            saved = await app.review.save(detail.candidate.processing_id, **command(detail), title="修正后", text="确认有效的正文")
            assert saved.processing_id != target.processing_id
            detail = await app.review.detail(first.document_id)
            await app.review.preview(saved.processing_id, **command(detail))
            await app.processing.process(saved.processing_id)
            detail = await app.review.detail(first.document_id)
            await app.review.adopt(saved.processing_id, **command(detail), fingerprint=detail.candidate.preview_fingerprint, actor_id=SUPERUSER_ID)
            assert (await app.adoption.process(saved.processing_id)).state == "adopted"
            assert (await current(db, first.document_id)).usage_status == "active"
            assert len(await hits(app)) == 1
            decisions = await app.review.decisions(first.document_id, offset=0, limit=20)
            rejected = next(item for item in decisions if item.decision == "reject")
            assert rejected.conclusion == "需要重新核对" and rejected.content_snapshot["body"] == "人工修订"
    run(verify())


def test_failed_adoption_retry_reuses_frozen_index_identity(isolated_database, processor):
    async def verify():
        db = isolated_database
        await actor(db)
        async with scenario(db, processor) as app:
            first = await app.files.upload(parse_text_file("retry.txt", b"body for retry"), KB)
            await app.processing.process(first.processing_id)
            app.embeddings.fail = True
            assert (await app.adoption.process(first.processing_id)).state == "adoption_failed"
            async with db.sessions() as session:
                frozen = (await session.get(DocumentProcessingRecord, first.processing_id)).index_target
            detail = await app.review.detail(first.document_id)
            await app.review.retry(first.processing_id, **command(detail), actor_id=SUPERUSER_ID)
            app.embeddings.fail = False
            assert (await app.adoption.process(first.processing_id)).state == "adopted"
            async with db.sessions() as session:
                assert (await session.get(DocumentProcessingRecord, first.processing_id)).index_target == frozen
                assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
                assert await session.scalar(select(func.count()).select_from(DocumentReviewRecord)) == 2
    run(verify())
