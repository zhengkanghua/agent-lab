"""文档测试的共享 ORM 与隔离 Qdrant 夹具；不隐式接触业务资料。"""

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from qdrant_client import AsyncQdrantClient, models

from agent_lab.config.qdrant import QdrantSettings
from agent_lab.domain.enums import DocumentType
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.models.document import DocumentRecord
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.models.source import SourceRecord
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.lifecycle import build_qdrant_client
from agent_lab.qdrant.store import QdrantDeletionStore

def build_record(*, content_text: str = "正文内容") -> DocumentRecord:
    """构造无需数据库连接的已加载 ORM 文档。"""

    source_id = uuid4()
    source = SourceRecord(
        id=source_id,
        provider="freshrss_main",
        external_id="feed/2",
        name="示例来源",
    )
    return DocumentRecord(
        id=uuid4(),
        knowledge_base_id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID,
        knowledge_base=KnowledgeBaseRecord(id=DEFAULT_NEWS_KNOWLEDGE_BASE_ID, key="news", name="新闻", is_active=True),
        source_id=source_id,
        source=source,
        external_id="article/42",
        document_type=DocumentType.ARTICLE,
        mime_type="text/plain",
        title="示例标题",
        url="https://example.com/article/42",
        published_at=datetime(2026, 8, 13, 1, 2, 3, tzinfo=UTC),
        authors=["作者甲"],
        labels=["宏观"],
        image_urls=[],
        content_text=content_text,
        content_hash="0" * 64,
    )


@asynccontextmanager
async def isolated_vectors(schema, *, spec=None):
    spec = spec or VectorIndexSpec(dimension=3)
    remote = os.getenv("RUN_SCHEDULER_QDRANT_INTEGRATION_TEST") == "1"
    settings = QdrantSettings(
        _env_file=None,
        base_url=os.environ["SCHEDULER_TEST_QDRANT_URL"] if remote else "http://qdrant.example.test:6333",
        api_key=os.getenv("SCHEDULER_TEST_QDRANT_API_KEY", "") if remote else "",
        environment=schema, vector_dimension=spec.dimension, collection_schema_version=spec.schema_version,
        distance=spec.distance.value,
    )
    client = build_qdrant_client(settings) if remote else AsyncQdrantClient(location=":memory:")
    created = aliased = False
    try:
        await client.create_collection(settings.collection_name, vectors_config=spec.vector_params, metadata=spec.collection_metadata)
        created = True
        await client.update_collection_aliases([models.CreateAliasOperation(create_alias=models.CreateAlias(
            collection_name=settings.collection_name, alias_name=settings.collection_alias,
        ))])
        aliased = True
        if remote:
            await client.create_payload_index(settings.collection_alias, "document_id", models.PayloadSchemaType.KEYWORD, wait=True)
            await client.create_payload_index(settings.collection_alias, "index_instance_id", models.PayloadSchemaType.KEYWORD, wait=True)
            await client.create_payload_index(settings.collection_alias, "knowledge_base_id", models.PayloadSchemaType.UUID, wait=True)
        yield SimpleNamespace(client=client, settings=settings, spec=spec, store=QdrantDeletionStore(client, settings))
    finally:
        try:
            if aliased:
                await client.update_collection_aliases([models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=settings.collection_alias))])
        finally:
            try:
                if created:
                    await client.delete_collection(settings.collection_name)
            finally:
                await client.close()


def processing_services(sessions, vectors, processor_factory, embeddings, storage):
    """测试用生产端口装配；明确替换外部模型和原件，生命周期逻辑保持真实。"""
    from functools import partial
    from agent_lab.knowledge.adapters.adoption import postgres_adoption_work
    from agent_lab.knowledge.adapters.files import postgres_file_work
    from agent_lab.knowledge.adapters.processing import postgres_processing_work
    from agent_lab.knowledge.adapters.review import postgres_review_work
    from agent_lab.knowledge.deletion import DocumentDeletionApplication
    from agent_lab.knowledge.file_application import FileDocumentService
    from agent_lab.knowledge.processing.adoption import DocumentAdoptionApplication
    from agent_lab.knowledge.processing.application import DocumentProcessingApplication
    from agent_lab.knowledge.processing.batch import DocumentProcessingBatch
    from agent_lab.knowledge.processing.indexing import CandidateIndexer
    from agent_lab.knowledge.processing.review import DocumentReviewApplication
    from agent_lab.qdrant.store import QdrantChunkStore
    from agent_lab.repositories.document_retention_repository import postgres_deletion_work
    from agent_lab.services.write_coordination import WriteCoordinator

    processing = DocumentProcessingApplication(partial(postgres_processing_work, sessions), storage, processor_factory)
    store = QdrantChunkStore(vectors.client, vectors.settings, vectors.spec)

    @asynccontextmanager
    async def indexer():
        yield CandidateIndexer(embeddings, store, vectors.spec.collection_metadata)

    @asynccontextmanager
    async def points():
        yield vectors.store

    adoption = DocumentAdoptionApplication(partial(postgres_adoption_work, sessions), WriteCoordinator(sessions),
                                           indexer, vectors.spec.collection_metadata)
    deletion = DocumentDeletionApplication(partial(postgres_deletion_work, sessions), WriteCoordinator(sessions), points, lambda: storage)
    files = FileDocumentService(partial(postgres_file_work, sessions), WriteCoordinator(sessions), lambda: deletion, lambda: processing)
    review = DocumentReviewApplication(partial(postgres_review_work, sessions), WriteCoordinator(sessions),
                                        lambda: processing, lambda: adoption, lambda: storage)
    return SimpleNamespace(files=files, processing=processing, adoption=adoption, review=review,
                           deletion=deletion, batch=DocumentProcessingBatch(processing, adoption))
