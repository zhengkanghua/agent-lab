"""随机 PostgreSQL schema 与隔离 Qdrant 验证真实接收、采用和可见性。"""

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from agent_lab.knowledge.adapters.adoption import postgres_adoption_work
from agent_lab.knowledge.adapters.files import postgres_file_work
from agent_lab.knowledge.adapters.processing import postgres_processing_work
from agent_lab.knowledge.adapters.importing import postgres_import_work
from agent_lab.knowledge.adapters.text_files import parse_text_file
from agent_lab.knowledge.adapters.visibility import PostgresDocumentVisibility
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID as KB
from agent_lab.knowledge.file_application import FileDocumentService
from agent_lab.knowledge.importing import SourceImportService
from agent_lab.knowledge.processing.adoption import DocumentAdoptionApplication
from agent_lab.knowledge.processing.application import DocumentProcessingApplication
from agent_lab.knowledge.processing.indexing import CandidateIndexer
from agent_lab.knowledge.visibility import AdoptedVectorSearch
from agent_lab.models.document import DocumentRecord
from agent_lab.models.source import SourceRecord
from agent_lab.domain.source_document import SourceDocument, SourceInfo
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentReviewRecord, DocumentVersion
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.search import QdrantVectorSearch
from agent_lab.qdrant.store import QdrantChunkStore
from agent_lab.repositories.document_repository import DocumentRepository
from agent_lab.schemas.vector_search import VectorSearchFilters
from agent_lab.services.write_coordination import WriteCoordinator
from tests.test_file_documents_integration import isolated_vectors
from tests.test_processing_application import MemoryStorage, processor
from tests.test_scheduler_postgres_integration import isolated_database, run

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason="需要明确授权隔离 PostgreSQL/Qdrant 文档处理验证。",
)


@asynccontextmanager
async def scenario(db, processor):
    storage = MemoryStorage()
    processing = DocumentProcessingApplication(partial(postgres_processing_work, db.sessions), storage, lambda: processor)
    spec = VectorIndexSpec(dimension=3, chunk_size=128)
    async with isolated_vectors(db.schema, spec=spec) as vectors:
        class Embeddings:
            embedding_model = spec.embedding_model
            fail = False

            async def embed_documents(self, texts):
                if self.fail:
                    raise RuntimeError("synthetic embedding failure")
                return [[1.0, 0.0, 0.0] for _ in texts]

        embeddings = Embeddings()
        store = QdrantChunkStore(vectors.client, vectors.settings, spec)

        @asynccontextmanager
        async def indexer():
            yield CandidateIndexer(embeddings, store, spec.collection_metadata)

        files = FileDocumentService(partial(postgres_file_work, db.sessions), WriteCoordinator(db.sessions), None, lambda: processing)
        adoption = DocumentAdoptionApplication(
            partial(postgres_adoption_work, db.sessions), WriteCoordinator(db.sessions), indexer, spec.collection_metadata,
        )
        search = AdoptedVectorSearch(QdrantVectorSearch(vectors.client, vectors.settings, spec), PostgresDocumentVisibility(db.sessions))
        yield SimpleNamespace(files=files, processing=processing, adoption=adoption, search=search,
                              vectors=vectors, embeddings=embeddings, storage=storage, spec=spec, store=store)


async def current(db, document_id):
    async with db.sessions() as session:
        return await session.get(DocumentRecord, document_id)


async def hits(app):
    return await app.search.search_groups([1.0, 0.0, 0.0], document_limit=10, matches_per_document=3,
                                         score_threshold=None, filters=VectorSearchFilters(knowledge_base_id=KB))


def test_upload_replace_adopt_and_cleanup_keep_frozen_history(isolated_database, processor):
    async def verify():
        db = isolated_database
        async with scenario(db, processor) as app:
            first = await app.files.upload(parse_text_file("第一版.md", "# 条款\n\n旧正文。".encode()), KB)
            async with db.sessions() as session:
                assert await DocumentRepository(session).get_with_source(first.document_id) is None
            assert await hits(app) == []
            assert (await app.processing.process(first.processing_id)).state == "ready"
            assert await hits(app) == []
            assert (await app.adoption.process(first.processing_id)).state == "adopted"
            old = await current(db, first.document_id)
            assert old.current_version_id is not None and "旧正文" in old.content_text
            assert (await hits(app))[0].matches[0].content_hash == old.content_hash

            second = await app.files.replace(first.document_id, parse_text_file("第二版.md", "# 条款\n\n新正文。".encode()),
                                             old.index_revision, old.management_revision)
            await app.processing.process(second.processing_id)
            assert (await current(db, first.document_id)).content_hash == old.content_hash
            assert (await hits(app))[0].matches[0].content_hash == old.content_hash
            assert (await app.adoption.process(second.processing_id)).state == "adopted"
            new = await current(db, first.document_id)
            assert new.current_version_id != old.current_version_id
            assert new.index_revision == old.index_revision + 1 and new.upload_filename == "第二版.md"
            assert (await hits(app))[0].matches[0].content_hash == new.content_hash
            assert await app.adoption.cleanup_one()
            assert not await app.adoption.cleanup_one()
            async with db.sessions() as session:
                versions = list((await session.scalars(select(DocumentVersion).order_by(DocumentVersion.revision))).all())
                assert [item.content_hash for item in versions] == [old.content_hash, new.content_hash]
                assert await session.scalar(select(func.count()).select_from(DocumentReviewRecord)) == 2
                assert (await session.get(DocumentProcessingRecord, first.processing_id)).index_deleted_at is not None
            assert len(app.storage.data) == 2
    run(verify())


def test_embedding_failure_leaves_adopted_body_and_is_not_automatically_retried(isolated_database, processor):
    async def verify():
        db = isolated_database
        async with scenario(db, processor) as app:
            first = await app.files.upload(parse_text_file("资料.txt", b"original body"), KB)
            await app.processing.process(first.processing_id)
            await app.adoption.process(first.processing_id)
            old = await current(db, first.document_id)
            second = await app.files.replace(first.document_id, parse_text_file("资料.txt", b"updated body"),
                                             old.index_revision, old.management_revision)
            await app.processing.process(second.processing_id)
            app.embeddings.fail = True
            assert (await app.adoption.process(second.processing_id)).state == "adoption_failed"
            assert await app.adoption.process(second.processing_id) is None
            assert (await current(db, first.document_id)).content_hash == old.content_hash
            assert (await hits(app))[0].matches[0].content_hash == old.content_hash
            async with db.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
    run(verify())


def test_late_preview_and_outdated_index_spec_cannot_be_adopted(isolated_database, processor):
    async def verify():
        db = isolated_database
        async with scenario(db, processor) as app:
            item = await app.files.upload(parse_text_file("资料.md", b"## Section\n\nBody"), KB)
            work = partial(postgres_processing_work, db.sessions)
            async with work() as repository:
                old_claim = await repository.claim(item.processing_id)
            async with work() as repository:
                assert await repository.requeue_computations(started_before=datetime.now(UTC) + timedelta(seconds=1)) == 1
            async with work() as repository:
                new_claim = await repository.claim(item.processing_id)
            preview = processor.preview(b"## Section\n\nBody", mime_type="text/markdown", title="资料")
            async with work() as repository:
                assert not await repository.save_preview(old_claim, preview, state="ready")
                assert await repository.save_preview(new_claim, preview, state="ready")
            async with postgres_adoption_work(db.sessions) as repository:
                assert (await repository.claim_next({**app.spec.collection_metadata, "chunk_size": 64}, item.processing_id)).state == "review"
            async with db.sessions() as session:
                record = await session.get(DocumentProcessingRecord, item.processing_id)
                assert record.state == "review" and record.error_code == "document_index_spec_changed"
            assert await app.adoption.process(item.processing_id) is None
            assert await hits(app) == []
    run(verify())


def test_newer_source_prevents_late_automatic_adoption(isolated_database, processor):
    async def verify():
        db = isolated_database
        async with scenario(db, processor) as app:
            first = await app.files.upload(parse_text_file("资料.md", b"older candidate"), KB)
            await app.processing.process(first.processing_id)
            async with postgres_adoption_work(db.sessions) as repository:
                target = await repository.claim_next(app.spec.collection_metadata, first.processing_id)
            before = await current(db, first.document_id)
            second = await app.files.replace(first.document_id, parse_text_file("资料.md", b"latest candidate"),
                                             before.index_revision, before.management_revision)
            await app.store.prepare_candidate(target, [[1.0, 0.0, 0.0] for _ in target.preview.chunk_result.chunks])
            async with postgres_adoption_work(db.sessions) as repository:
                assert not await repository.complete(target)
                await repository.fail(target, "document_adoption_conflict")
            assert await hits(app) == []
            await app.processing.process(second.processing_id)
            await app.adoption.process(second.processing_id)
            assert "latest candidate" in (await current(db, first.document_id)).content_text
            assert len(await hits(app)) == 1
    run(verify())


def test_freshrss_intake_recovery_and_bad_content_do_not_skip_unstored_items(isolated_database, processor):
    async def verify():
        db = isolated_database
        source_id = uuid4()
        source = SourceInfo(provider="test", external_id="feed/1", name="测试来源")
        async with db.sessions() as session:
            session.add(SourceRecord(id=source_id, provider=source.provider, external_id=source.external_id,
                                     name=source.name, knowledge_base_id=KB, sync_checkpoint="1"))
            await session.commit()
        storage = MemoryStorage(fail=True)
        processing = DocumentProcessingApplication(partial(postgres_processing_work, db.sessions), storage, lambda: processor)
        importer = SourceImportService(None, partial(postgres_import_work, db.sessions), lambda: processing)
        documents = [SourceDocument(external_id=str(index), title=title, url=f"https://example.com/{index}",
                                    raw_bytes=html, source=source)
                     for index, title, html in [(2, "", b"<p>Body</p>"), (3, "normal", b"<h1>Chapter</h1><p>Normal body</p>")]]
        with pytest.raises(ProcessingApplicationError, match="document_source_storage_failed"):
            await importer.save_source_page(documents=documents, existing_source_id=source_id,
                                            expected_checkpoint="1", new_checkpoint="3")
        async with db.sessions() as session:
            assert (await session.get(SourceRecord, source_id)).sync_checkpoint == "1"
            original_ids = set((await session.scalars(select(DocumentProcessingRecord.id))).all())
            assert len(original_ids) == 2
        storage.fail = False
        assert await importer.save_source_page(documents=documents, existing_source_id=source_id,
                                              expected_checkpoint="1", new_checkpoint="3") == (2, True)
        async with db.sessions() as session:
            assert (await session.get(SourceRecord, source_id)).sync_checkpoint == "3"
            records = list((await session.scalars(select(DocumentProcessingRecord))).all())
            assert {record.id for record in records} == original_ids
        states = {(await processing.process(record.id)).state for record in records}
        assert states == {"ready", "review"} and len(storage.data) == 2
        # 同一原件与元数据重复接收，不新建候选、不重新排队失败正文。
        assert await importer.save_source_page(documents=documents, existing_source_id=source_id,
                                              expected_checkpoint="3", new_checkpoint="3") == (2, False)
        async with db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(DocumentProcessingRecord)) == 2
    run(verify())


def test_source_metadata_candidate_keeps_adopted_name_and_original(isolated_database, processor):
    async def verify():
        db = isolated_database
        source_id = uuid4()
        source = SourceInfo(provider="test", external_id="feed/1", name="原来源")
        async with db.sessions() as session:
            session.add(SourceRecord(id=source_id, provider=source.provider, external_id=source.external_id,
                                     name=source.name, knowledge_base_id=KB))
            await session.commit()
        async with scenario(db, processor) as app:
            importer = SourceImportService(None, partial(postgres_import_work, db.sessions), lambda: app.processing)
            incoming = SourceDocument(external_id="item/1", title="标题", url="https://example.com/1",
                                      raw_bytes=b"<h1>Heading</h1><p>Body</p>", source=source)
            await importer.save_source_page(documents=[incoming], existing_source_id=source_id,
                                            expected_checkpoint=None, new_checkpoint="1")
            async with db.sessions() as session:
                document = await session.scalar(select(DocumentRecord))
            await app.processing.process(document.latest_processing_id)
            await app.adoption.process(document.latest_processing_id)
            original = await current(db, document.id)
            async with db.sessions() as session:
                await session.execute(update(DocumentRecord).where(DocumentRecord.id == document.id).values(manual_review_required=True))
                await session.commit()
            renamed = source.model_copy(update={"name": "新来源"})
            async with postgres_import_work(db.sessions) as work:
                await work.sources.upsert(renamed)
                await work.commit()
            async with db.sessions() as session:
                current_doc = await DocumentRepository(session).get_with_source(document.id)
                assert current_doc.source.name == "新来源"
                assert current_doc.current_version.metadata_snapshot["source_name"] == "原来源"
                assert current_doc.content_hash == original.content_hash
                assert current_doc.latest_processing_id != original.latest_processing_id
                candidate_id = current_doc.latest_processing_id
                candidate = await session.get(DocumentProcessingRecord, candidate_id)
                old = await session.get(DocumentProcessingRecord, original.latest_processing_id)
                assert candidate.source_object_key == old.source_object_key
                assert candidate.requires_review
            assert (await app.processing.process(candidate_id)).state == "review"
            assert await app.adoption.process(candidate_id) is None
            assert (await hits(app))[0].matches[0].source_name == "原来源"
            assert len(app.storage.data) == 1
    run(verify())
