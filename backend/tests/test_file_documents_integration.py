"""文件 HTTP 从保存回执到正式采用；范围、替换冲突及来源空值继续受保护。"""

from datetime import timedelta
from functools import partial
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from agent_lab.api.dependencies import get_vector_search_service
from agent_lab.api.file_documents import get_file_document_service
from agent_lab.db.session import get_db_session
from agent_lab.knowledge.adapters.postgres import postgres_knowledge_base_work
from agent_lab.knowledge.application import KnowledgeBaseService
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID as KB
from agent_lab.knowledge.processing.batch import DocumentProcessingBatch
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.pipeline.ollama_embedding_provider import OllamaEmbeddingProvider
from agent_lab.services.vector_search_service import VectorSearchService
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_superuser
from tests.test_processing_application import processor
from tests.test_processing_postgres_integration import scenario
from tests.test_scheduler_postgres_integration import isolated_database, run
from tests.test_vector_search import ollama_settings

pytestmark = pytest.mark.skipif(os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1", reason="需要授权隔离文件生命周期验证。")


@pytest.mark.parametrize("filename,content", [
    ("运行说明.txt", "备份保留 7 天。"),
    ("运行说明.md", "# 备份规则\n\n备份保留 7 天。\n\n```py\nif ready:\n    backup()\n```"),
])
def test_http_upload_adopt_search_read_and_replace(isolated_database, processor, filename, content):
    async def verify():
        db, other_id = isolated_database, uuid4()
        async with db.sessions() as session:
            session.add(KnowledgeBaseRecord(id=other_id, key="manuals", name="操作资料", is_active=True))
            await session.commit()
        async with scenario(db, processor) as state:
            provider = OllamaEmbeddingProvider(ollama_settings(), embeddings=SimpleNamespace(
                aembed_query=AsyncMock(return_value=[1.0, 0.0, 0.0])))
            search = VectorSearchService(embedding_provider=provider, vector_search=state.search, spec=state.spec,
                knowledge_base_scope=KnowledgeBaseService(partial(postgres_knowledge_base_work, db.sessions)))
            batch = DocumentProcessingBatch(state.processing, state.adoption)
            app = allow_superuser(create_offline_app())
            app.dependency_overrides[get_file_document_service] = lambda: state.files
            app.dependency_overrides[get_vector_search_service] = lambda: search

            async def session_dependency():
                async with db.sessions() as session:
                    yield session
            app.dependency_overrides[get_db_session] = session_dependency
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
                    uploads = []
                    for identity in (KB, other_id):
                        response = await http.post("/file-documents", data={"knowledge_base_id": str(identity)},
                                                   files={"file": (filename, content.encode())})
                        assert response.status_code == 201 and response.json()["candidate_state"] == "pending"
                        uploads.append(response.json())
                    identity = uploads[1]["document_id"]
                    assert uploads[0]["document_id"] != identity
                    assert (await http.get(f"/documents/{identity}")).status_code == 404
                    result = await batch.run(batch_size=10, stale_after=timedelta(minutes=15))
                    assert result.indexed_count == 2 and not result.failures
                    selection = {"mode": "selected", "knowledge_base_ids": [str(other_id)]}
                    async def search_now():
                        response = await http.post("/document-search", json={"query": "备份规则", "scope": selection})
                        assert response.status_code == 200
                        return response.json()["results"]
                    found = await search_now()
                    assert len(found) == 1 and found[0]["document_id"] == identity
                    detail = (await http.get(f"/documents/{identity}")).json()
                    assert detail["content_text"] == content and detail["content_hash"] == found[0]["content_hash"]
                    assert detail["source_name"] is None and detail["url"] is None and detail["published_at"] is None
                    managed = await state.review.detail(UUID(identity))
                    previous_revision = {"revision": str(detail["revision"]),
                                         "management_revision": str(managed.document.management_revision)}
                    updated_text = content.replace("7 天", "14 天")
                    response = await http.put(f"/file-documents/{identity}/file", data=previous_revision,
                                              files={"file": ("new-" + filename, updated_text.encode())})
                    assert response.status_code == 200 and response.json()["revision"] == detail["revision"]
                    assert response.json()["knowledge_base_id"] == str(other_id)
                    assert (await http.get(f"/documents/{identity}")).json()["content_text"] == content
                    assert (await search_now())[0]["content_hash"] == found[0]["content_hash"]
                    stale = await http.put(f"/file-documents/{identity}/file", data=previous_revision,
                                           files={"file": (filename, b"late overwrite")})
                    assert stale.status_code == 409
                    await batch.run(batch_size=10, stale_after=timedelta(minutes=15))
                    updated = (await http.get(f"/documents/{identity}")).json()
                    assert updated["content_text"] == updated_text and updated["revision"] == detail["revision"] + 1
                    assert (await search_now())[0]["content_hash"] == updated["content_hash"]
            finally:
                await provider.close()
    run(verify())
