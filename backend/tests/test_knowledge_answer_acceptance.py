"""第二阶段代表回答验收：真实 Embedding/模型，隔离 PostgreSQL/Qdrant 和内存会话。

只有显式模型验收开关及隔离数据库开关都开启才运行。只上传本仓库合成资料，
通过正常 HTTP 文件/搜索/Agent/全文入口观察结果；不启动调度器、不访问业务资料。
结果写 .pytest_cache/phase-two-answer-report.json，程序检查不能代替逐题事实核对。
"""

import asyncio
from datetime import timedelta
from functools import partial
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent_lab.agent.runtime import AgentRuntime
from agent_lab.api.dependencies import get_agent_runtime, get_vector_search_service
from agent_lab.api.file_documents import get_file_document_service
from agent_lab.config.llm import get_langsmith_settings, get_llm_settings
from agent_lab.config.ollama_embedding import OllamaEmbeddingSettings
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.db.session import get_db_session
from agent_lab.knowledge.adapters.postgres import postgres_knowledge_base_work
from agent_lab.knowledge.application import KnowledgeBaseService
from agent_lab.models.knowledge_base import KnowledgeBaseRecord
from agent_lab.pipeline.ollama_embedding_provider import OllamaEmbeddingProvider
from agent_lab.qdrant.index_spec import VectorIndexSpec
from agent_lab.qdrant.search import QdrantVectorSearch
from agent_lab.qdrant.store import QdrantChunkStore
from agent_lab.services.vector_search_service import VectorSearchService
from agent_lab.services.write_coordination import WriteCoordinator
from tests.agent_helpers import OFFLINE_LANGSMITH_SETTINGS
from tests.app_helpers import create_offline_app
from tests.auth_helpers import allow_superuser
from tests.document_fixtures import isolated_vectors, processing_services
from tests.test_processing_application import MemoryStorage
from agent_lab.knowledge.composition import build_document_processor
from agent_lab.knowledge.adapters.visibility import PostgresDocumentVisibility
from agent_lab.knowledge.visibility import AdoptedVectorSearch
from tests.test_scheduler_postgres_integration import isolated_database, run

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_KNOWLEDGE_ANSWER_ACCEPTANCE_TEST") != "1"
    or os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason="需要显式授权真实模型/Embedding 与隔离 PostgreSQL/Qdrant 验收。",
)

CORPUS = Path(__file__).parent / "fixtures" / "knowledge-base-phase-two"


def test_representative_answers_and_current_document_lifecycle(isolated_database):
    """保留真实答案与耗时供人工核对，自动断言范围、引用身份和回放一致性。"""
    report = {"cases": [], "documents": {}, "manual_semantic_review": "pending", "stage": "setup"}
    failures = []

    async def verify():
        db = isolated_database
        bases = {"operations": uuid4(), "delivery": uuid4()}
        async with db.sessions() as session:
            session.add_all([
                KnowledgeBaseRecord(id=bases["operations"], key="operations", name="运行资料", is_active=True),
                KnowledgeBaseRecord(id=bases["delivery"], key="delivery", name="交付资料", is_active=True),
                KnowledgeBaseRecord(id=uuid4(), key="archive", name="停用资料", is_active=False),
            ])
            await session.commit()

        embedding_settings = OllamaEmbeddingSettings()
        spec = VectorIndexSpec.from_settings(QdrantSettings(), embedding_settings)
        llm_settings = get_llm_settings()
        report["model"] = llm_settings.model
        report["embedding_model"] = embedding_settings.embedding_model
        provider = OllamaEmbeddingProvider(embedding_settings)
        try:
            async with isolated_vectors(db.schema, spec=spec) as vectors:
                search = VectorSearchService(
                    embedding_provider=provider,
                    vector_search=AdoptedVectorSearch(QdrantVectorSearch(vectors.client, vectors.settings, spec), PostgresDocumentVisibility(db.sessions)), spec=spec,
                    knowledge_base_scope=KnowledgeBaseService(partial(postgres_knowledge_base_work, db.sessions)),
                )
                services = processing_services(db.sessions, vectors, build_document_processor, provider, MemoryStorage())
                runtime = AgentRuntime.build(
                    llm_settings=llm_settings, search_service=search, session_factory=db.sessions,
                    database_url=db.dsn, checkpointer=InMemorySaver(),
                )
                app = allow_superuser(create_offline_app())
                app.dependency_overrides[get_file_document_service] = lambda: services.files
                app.dependency_overrides[get_vector_search_service] = lambda: search
                app.dependency_overrides[get_agent_runtime] = lambda: runtime
                app.dependency_overrides[get_langsmith_settings] = lambda: OFFLINE_LANGSMITH_SETTINGS

                async def session_dependency():
                    async with db.sessions() as session:
                        yield session

                app.dependency_overrides[get_db_session] = session_dependency
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acceptance") as client:
                    for key, base_id in bases.items():
                        for path in sorted((CORPUS / key).iterdir()):
                            report["stage"] = f"upload:{key}/{path.name}"
                            response = await client.post("/file-documents", data={"knowledge_base_id": str(base_id)}, files={"file": (path.name, path.read_bytes())})
                            assert response.status_code == 201
                            report["documents"][path.relative_to(CORPUS).as_posix()] = response.json()
                    report["stage"] = "index"
                    indexed = await services.batch.run( batch_size=20, stale_after=timedelta(minutes=15))
                    assert indexed.indexed_count == len(report["documents"]) and not indexed.failures

                    # 同一套问题既走普通检索，也走 Agent；每题单独保存原文与引用供核对。
                    threads = {}
                    for case in json.loads((CORPUS / "questions.json").read_text(encoding="utf-8")):
                        entry = {**case, "automatic_status": "running"}
                        report["cases"].append(entry)
                        previous_failures = len(failures)
                        scope = {"mode": "selected", "knowledge_base_ids": [str(bases[key]) for key in case["scope_keys"]]}
                        report["stage"] = f"{case['id']}:search"
                        found = await client.post("/document-search", json={"query": case["question"], "scope": scope})
                        entry["search_http_status"] = found.status_code
                        assert found.status_code == 200
                        entry["search"] = found.json()
                        body = {"message": case["question"], "scope": scope}
                        if case.get("continue_from"):
                            body["thread_id"] = threads[case["continue_from"]]
                        report["stage"] = f"{case['id']}:answer"
                        started = perf_counter()
                        try:
                            response = await asyncio.wait_for(client.post("/agent/chat", json=body), timeout=300)
                        finally:
                            entry["elapsed_seconds"] = round(perf_counter() - started, 3)
                        entry["answer_http_status"] = response.status_code
                        assert response.status_code == 200
                        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                        entry["events"] = events
                        assert events, "Agent 未返回 SSE 事件"
                        final = events[-1]
                        if final["event"] != "done":
                            failures.append(f"{case['id']}: {final.get('code', 'missing_done')}")
                            entry["automatic_status"] = "failed"
                            break
                        threads[case["id"]] = final["thread_id"]
                        report["stage"] = f"{case['id']}:replay"
                        replay_response = await client.get(f"/agent/threads/{final['thread_id']}/messages")
                        assert replay_response.status_code == 200
                        replay = replay_response.json()["turns"][-1]
                        entry["replay"] = replay
                        for field in ("answer", "status", "citations", "invalid_citations"):
                            assert replay[field] == final[field]
                        report["stage"] = f"{case['id']}:evidence"
                        if final["status"] != "completed" or final["invalid_citations"]:
                            failures.append(f"{case['id']}: incomplete_or_invalid_citation")
                        allowed = set(scope["knowledge_base_ids"])
                        assert all(item["knowledge_base_id"] in allowed for item in final["citations"])
                        if not case["allow_insufficient"]:
                            required_ids = {report["documents"][name]["document_id"] for name in case["expected_sources"]}
                            cited_ids = {item["document_id"] for item in final["citations"]}
                            if not required_ids <= cited_ids:
                                failures.append(f"{case['id']}: missing_expected_source")
                        if case["id"] == "narrow_by_text":
                            tool_scopes = [event["scope"] for event in events if event["event"] == "tool_result" and event.get("scope")]
                            if not tool_scopes or any({item["id"] for item in tool_scope["knowledge_bases"]} != {str(bases["delivery"])} for tool_scope in tool_scopes):
                                failures.append("narrow_by_text: tool_scope_not_narrowed")
                        entry["automatic_status"] = "passed" if len(failures) == previous_failures else "failed"

                    report["stage"] = "document_lifecycle"
                    original = report["documents"]["operations/backup-policy.txt"]
                    document_id = original["document_id"]
                    replacement = (CORPUS / "operations" / "backup-policy.txt").read_text(encoding="utf-8").replace("7 天", "14 天")
                    old_detail = (await client.get(f"/documents/{document_id}")).json()
                    management = await services.review.detail(UUID(document_id))
                    replaced = await client.put(f"/file-documents/{document_id}/file",
                        data={"revision": str(old_detail["revision"]), "management_revision": str(management.document.management_revision)},
                        files={"file": ("backup-policy.txt", replacement.encode())})
                    assert replaced.status_code == 200 and replaced.json()["revision"] == old_detail["revision"]
                    assert (await client.get(f"/documents/{document_id}")).json()["content_hash"] == old_detail["content_hash"]
                    assert (await services.batch.run(batch_size=20, stale_after=timedelta(minutes=15))).indexed_count == 1
                    current = (await client.get(f"/documents/{document_id}")).json()
                    assert current["content_hash"] != old_detail["content_hash"] and "14 天" in current["content_text"]
                    old = next((item for item in report["cases"] if item["id"] == "single_fact" and item.get("replay")), None)
                    if old:
                        reopened = (await client.get(f"/agent/threads/{threads['single_fact']}/messages")).json()
                        assert reopened["turns"][0]["answer"] == old["replay"]["answer"]
                        assert reopened["turns"][0]["citations"] == old["replay"]["citations"]
                    management = await services.review.detail(UUID(document_id))
                    deleted = await client.delete(f"/file-documents/{document_id}",
                        params={"revision": current["revision"], "management_revision": management.document.management_revision})
                    assert deleted.status_code == 204
                    assert (await client.get(f"/documents/{document_id}")).status_code == 404
                    report["current_document_lifecycle"] = "replacement_kept_id_and_old_evidence_then_deleted"
        finally:
            await provider.close()

    try:
        run(verify())
        report["stage"] = "completed"
    except Exception as error:
        # 保留当前失败题和阶段；不把上游错误文本（可能带连接信息）写进报告。
        failures.append(f"{report['stage']}: {type(error).__name__}")
        if report["cases"] and report["cases"][-1]["automatic_status"] == "running":
            report["cases"][-1]["automatic_status"] = "failed"
        raise
    finally:
        report["automatic_failures"] = failures
        output = Path(".pytest_cache/phase-two-answer-report.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not failures, "; ".join(failures)
