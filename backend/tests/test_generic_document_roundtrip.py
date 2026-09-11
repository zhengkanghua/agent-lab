"""通用 Document 从构建、向量写入到两种搜索和全文读取的离线闭环。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient, models

from agent_lab.api.dependencies import get_vector_search_service
from agent_lab.api.documents import get_document_repository
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.domain.enums import DocumentType
from agent_lab.knowledge.processing.indexing import CandidateIndexer, IndexMetadata
from agent_lab.knowledge.visibility import AdoptedVectorSearch
from agent_lab.models.document_processing import DocumentVersion
from agent_lab.pipeline.ollama_embedding_provider import OllamaEmbeddingProvider
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.payload import QdrantPayloadMapper
from agent_lab.qdrant.search import QdrantVectorSearch
from agent_lab.qdrant.store import QdrantChunkStore
from agent_lab.schemas.vector_search import VectorSearchResult
from agent_lab.services.vector_search_service import VectorSearchService
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_reader
from tests.knowledge_helpers import ActiveKnowledgeBaseScope
from tests.document_fixtures import build_record
from tests.test_candidate_index import MemoryVisibility, candidate, processor
from tests.test_qdrant_vector_store import build_chunk
from tests.test_vector_search import ollama_settings


@pytest.mark.parametrize("missing", ["all", "source", "url", "external_id"])
def test_generic_document_roundtrip(missing, processor):
    async def verify():
        record = build_record(content_text="技术资料正文与具体操作记录。")
        record.knowledge_base_id = uuid4()
        record.document_type = DocumentType.OTHER
        record.mime_type = "text/markdown"
        record.index_revision = 1
        record.published_at = None
        if missing in ("all", "source"):
            record.source = None
            record.source_id = None
        if missing in ("all", "url"):
            record.url = None
        if missing in ("all", "external_id"):
            record.external_id = None
        settings = QdrantSettings(_env_file=None, environment="generic_test", vector_dimension=3, collection_schema_version="v3")
        spec = VectorIndexSpec(dimension=3, chunk_size=64)
        source = record.source
        metadata = IndexMetadata(document_type=record.document_type, source_id=record.source_id,
            source_name=source.name if source else None, source_provider=source.provider if source else None,
            source_external_id=source.external_id if source else None, document_external_id=record.external_id,
            url=record.url, authors=tuple(record.authors), labels=tuple(record.labels))
        target = candidate(processor, spec, record.knowledge_base_id, document_id=record.id, text=record.content_text)
        target = target.model_copy(update={"metadata": metadata})
        record.current_version_id = target.version_id
        record.current_version = DocumentVersion(id=target.version_id, metadata_snapshot=metadata.model_dump(mode="json"))
        record.content_hash = target.content_hash
        visibility = MemoryVisibility(record.knowledge_base_id)
        visibility.intent(target)
        embeddings = SimpleNamespace(
            aembed_documents=AsyncMock(side_effect=lambda texts: [[1.0, 0.0, 0.0] for _ in texts]),
            aembed_query=AsyncMock(return_value=[1.0, 0.0, 0.0]),
        )
        provider = OllamaEmbeddingProvider(ollama_settings(), embeddings=embeddings)
        client = AsyncQdrantClient(location=":memory:")
        try:
            await client.create_collection(settings.collection_name, vectors_config=spec.vector_params)
            await client.update_collection_aliases([models.CreateAliasOperation(create_alias=models.CreateAlias(
                collection_name=settings.collection_name, alias_name=settings.collection_alias,
            ))])
            await CandidateIndexer(provider, QdrantChunkStore(client, settings, spec), spec.collection_metadata).prepare(target)
            visibility.adopt(target)
            service = VectorSearchService(embedding_provider=provider, vector_search=AdoptedVectorSearch(QdrantVectorSearch(client, settings, spec), visibility), spec=spec, knowledge_base_scope=ActiveKnowledgeBaseScope())
            app = allow_reader(create_offline_app())
            app.dependency_overrides[get_vector_search_service] = lambda: service
            app.dependency_overrides[get_document_repository] = lambda: SimpleNamespace(get_with_source=AsyncMock(return_value=record))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as http:
                for path in ("/vector-search", "/document-search"):
                    response = await http.post(path, json={"query": "技术资料", "knowledge_base_id": str(record.knowledge_base_id)})
                    assert response.status_code == 200, response.text
                    result = response.json()[0]
                    assert result["document_id"] == str(record.id)
                    assert result["knowledge_base_id"] == str(record.knowledge_base_id)
                    assert result["mime_type"] == "text/markdown"
                    assert result["source_name"] == metadata.source_name
                    assert result["url"] == record.url
                    if path == "/vector-search":
                        assert result["document_type"] == "other"
                        assert result["document_external_id"] == record.external_id
                detail = await http.get(f"/documents/{record.id}")
                assert detail.status_code == 200, detail.text
                assert detail.json()["source_name"] == metadata.source_name
                assert detail.json()["url"] == record.url
                assert detail.json()["knowledge_base_id"] == str(record.knowledge_base_id)
                assert detail.json()["mime_type"] == "text/markdown"
                assert detail.json()["content_text"] == record.content_text
            assert embeddings.aembed_documents.await_args.args[0] == [chunk.embedding_text for chunk in target.preview.chunk_result.chunks]
        finally:
            await client.close()

    asyncio.run(verify())


@pytest.mark.parametrize("field", ["title", "knowledge_base_id", "document_id", "content_hash", "mime_type"])
def test_generic_result_still_rejects_missing_required_metadata(field):
    chunk = build_chunk()
    payload = QdrantPayloadMapper(VectorIndexSpec(dimension=3)).build(chunk)
    del payload[field]
    with pytest.raises(ValidationError):
        VectorSearchResult.model_validate({**payload, "chunk_id": chunk.id, "score": 1.0})


@pytest.mark.parametrize("field", ["source_name", "source_provider", "source_external_id", "document_external_id"])
def test_optional_result_text_accepts_null_but_not_blank_or_wrong_type(field):
    chunk = build_chunk()
    payload = QdrantPayloadMapper(VectorIndexSpec(dimension=3)).build(chunk)
    result = {**payload, "chunk_id": chunk.id, "score": 1.0}
    assert getattr(VectorSearchResult.model_validate({**result, field: None}), field) is None
    for invalid in (" ", "", 123):
        with pytest.raises(ValidationError):
            VectorSearchResult.model_validate({**result, field: invalid})
