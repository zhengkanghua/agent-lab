"""真实 PostgreSQL 与隔离 Qdrant 的完整删除和保留期保护；原件使用可故障注入的替身。"""

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from agent_lab.domain.enums import DocumentType, ProcessingStatus
from agent_lab.knowledge.adapters.text_files import parse_text_file
from agent_lab.knowledge.deletion import DocumentDeletionApplication
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID as KB
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError, SourceIntake
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentReviewRecord, DocumentVersion
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.write_operation import DocumentDeletionRecord
from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository
from agent_lab.services.write_coordination import WriteCoordinator
from tests.test_processing_application import processor
from tests.test_processing_postgres_integration import current, hits, scenario
from tests.test_scheduler_postgres_integration import isolated_database, run, seed_documents

pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1", reason="需要授权隔离文档删除验证。")


def test_delete_cleans_originals_history_and_unconfirmed_intake_with_recovery(isolated_database, processor):
    async def verify():
        db = isolated_database
        async with scenario(db, processor) as app:
            first = await app.files.upload(parse_text_file("old.txt", b"first body"), KB)
            await app.processing.process(first.processing_id)
            await app.adoption.process(first.processing_id)
            document = await current(db, first.document_id)
            second = await app.files.replace(document.id, parse_text_file("new.txt", b"second body"), document.index_revision, document.management_revision)
            await app.processing.process(second.processing_id)
            await app.adoption.process(second.processing_id)
            # 保存成功但回执未确认的原件也在删除清单中。
            orphan_receipt = SourceIntake.prepare(document_id=document.id, source_kind="file", data=b"unconfirmed bytes", mime_type="text/plain")
            from agent_lab.knowledge.adapters.processing import postgres_processing_work
            async with postgres_processing_work(db.sessions) as repository:
                await repository.create_intent(orphan_receipt)
            await app.storage.put(orphan_receipt.reference.key, b"unconfirmed bytes", content_type="text/plain")
            retained = await app.files.upload(parse_text_file("retained.txt", b"other document"), KB)
            retained_key = next(key for key in app.storage.data if str(retained.document_id) in key)
            failure = {"finish": False}

            class Repository(DocumentRetentionRepository):
                async def finish(self, records):
                    if failure["finish"]:
                        failure["finish"] = False
                        raise RuntimeError("synthetic database finish failure")
                    return await super().finish(records)

            @asynccontextmanager
            async def work():
                async with db.sessions() as session:
                    yield Repository(session)

            @asynccontextmanager
            async def points():
                yield app.vectors.store

            deletion = DocumentDeletionApplication(work, WriteCoordinator(db.sessions), points, lambda: app.storage)
            async def attempt():
                target = await current(db, document.id)
                await deletion.delete(document.id, revision=target.index_revision, management_revision=target.management_revision)

            app.storage.fail = True
            with pytest.raises(ProcessingApplicationError, match="document_delete_failed"):
                await attempt()
            async with db.sessions() as session:
                assert await DocumentRepository(session).get_with_source(document.id) is None
                record = await session.get(DocumentDeletionRecord, document.id)
                assert record.qdrant_deleted and len(record.objects) == 3
                assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == document.id)) == 2
            assert await hits(app) == []
            app.storage.fail, failure["finish"] = False, True
            with pytest.raises(ProcessingApplicationError, match="document_delete_failed"):
                await attempt()
            assert list(app.storage.data) == [retained_key]
            # 已确认的远端删除不重复执行；即使此时原件服务故障，数据库仍可收尾。
            app.storage.fail = True
            await attempt()
            async with db.sessions() as session:
                assert await session.get(DocumentRecord, document.id) is None
                assert await session.get(DocumentRecord, retained.document_id) is not None
                assert await session.get(DocumentDeletionRecord, document.id) is None
                for model in (DocumentProcessingRecord, DocumentReviewRecord, DocumentVersion):
                    assert await session.scalar(select(func.count()).select_from(model).where(model.document_id == document.id)) == 0
            assert list(app.storage.data) == [retained_key]
    run(verify())


def test_retention_selects_only_fully_adopted_documents_without_protected_records(isolated_database):
    async def verify():
        db = isolated_database
        now, old = datetime.now(UTC), datetime.now(UTC) - timedelta(days=200)
        other_kb = uuid4()
        identities = {}
        async with db.sessions() as session:
            session.add(KnowledgeBaseRecord(id=other_kb, key="retained", name="范围外", is_active=True))
            await session.flush()
            for case in ("eligible", "no_date", "new", "outside", "review", "failed", "rejected", "draft", "receiving_failed", "unadopted"):
                document_id, processing_id, version_id, index_id = uuid4(), uuid4(), uuid4(), uuid4()
                identities[case] = document_id
                document = DocumentRecord(id=document_id, knowledge_base_id=other_kb if case == "outside" else KB,
                    title=case, document_type=DocumentType.OTHER, mime_type="text/plain", upload_filename=f"{case}.txt",
                    content_text="body", content_hash="a" * 64, processing_status=ProcessingStatus.INDEXED,
                    index_revision=1, indexed_revision=1, management_revision=1, usage_status="rejected" if case == "rejected" else "active",
                    authors=[], labels=[], image_urls=[], published_at=None if case == "no_date" else now if case == "new" else old,
                    created_at=old, updated_at=old)
                session.add(document)
                await session.flush()
                state = case if case in {"review", "failed", "receiving_failed", "draft"} else "adopted"
                session.add(DocumentProcessingRecord(id=processing_id, document_id=document_id, source_kind="file", state=state,
                    source_metadata={"title": case}, candidate_revision=1, draft_revision=0, requires_review=False, issue_codes=[]))
                await session.flush()
                if case != "unadopted":
                    session.add(DocumentVersion(id=version_id, document_id=document_id, processing_id=processing_id, revision=1,
                        title=case, mime_type="text/plain", content_text="body", content_hash="a" * 64,
                        parsed_document={}, chunk_result={}, processing_spec={}, metadata_snapshot={}))
                    await session.flush()
                    document.current_version_id, document.current_index_instance_id = version_id, index_id
                document.latest_processing_id = processing_id
                if case == "draft":
                    document.draft_processing_id = processing_id
            await session.commit()
        async with db.sessions() as session:
            repository = DocumentRetentionRepository(session)
            candidates = await repository.candidates(now - timedelta(days=180), None, 100, knowledge_base_ids=(KB,))
            assert {item.document_id for item in candidates} == {identities["eligible"], identities["no_date"]}
    run(verify())


def test_retention_does_not_resume_or_reselect_explicit_deletion(isolated_database):
    async def verify():
        db = isolated_database
        now = datetime.now(UTC)
        old = now - timedelta(days=200)
        documents = await seed_documents(db.sessions, [(old, old, ProcessingStatus.INDEXED)] * 3)
        async with db.sessions() as session:
            repository = DocumentRetentionRepository(session)
            await repository.prepare_explicit(documents[0].id, revision=1, management_revision=1)
            candidates = await repository.candidates(now - timedelta(days=180), None, 10, knowledge_base_ids=(KB,))
            assert {item.document_id for item in candidates} == {item.id for item in documents[1:]}
            await repository.prepare(candidates[:1], now - timedelta(days=180))
            pending = await repository.pending(None, 10, knowledge_base_ids=(KB,))
            assert len(pending) == 1 and pending[0].document_id != documents[0].id
    run(verify())
