"""显式授权后验证开发 PostgreSQL/Qdrant 部分成功恢复，只创建随机隔离测试资源。

可用 --scheduler-configured-services 读取现有开发配置，或同时设置 PostgreSQL 门控、
SCHEDULER_TEST_DATABASE_URL、RUN_SCHEDULER_QDRANT_INTEGRATION_TEST=1 与
SCHEDULER_TEST_QDRANT_URL。不访问 FreshRSS、Ollama 或模型；退出删除本测试 Collection/Alias。
"""

from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest
from qdrant_client import models
from sqlalchemy import func, select

from agent_lab.config.qdrant import QdrantSettings
from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.domain import DEFAULT_NEWS_KNOWLEDGE_BASE_ID
from agent_lab.models.document import DocumentRecord
from agent_lab.models.write_operation import DocumentDeletionRecord
from agent_lab.qdrant.lifecycle import build_qdrant_client
from agent_lab.qdrant.store import QdrantDeletionStore
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository
from agent_lab.services.document_retention_service import DocumentRetentionService
from agent_lab.services.write_coordination import WriteCoordinator
from tests.test_scheduler_postgres_integration import isolated_database, run, seed_documents

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1" or os.getenv("RUN_SCHEDULER_QDRANT_INTEGRATION_TEST") != "1",
    reason="需要显式授权开发 PostgreSQL 和 Qdrant 验证。",
)


def test_remote_delete_then_postgres_failure_recovers_without_orphan_points(isolated_database):
    url = os.environ.get("SCHEDULER_TEST_QDRANT_URL")
    if not url:
        pytest.fail("必须显式指定 SCHEDULER_TEST_QDRANT_URL。", pytrace=False)
    settings = QdrantSettings(
        _env_file=None, base_url=url, api_key=os.environ.get("SCHEDULER_TEST_QDRANT_API_KEY", ""),
        environment=f"scheduler_test_{uuid4().hex}", collection_schema_version="v2",
        collection_generation=1, vector_dimension=2, distance="Cosine", write_batch_size=50,
    )

    async def verify():
        # 使用生产装配入口：HTTPS 反代默认端口是 443；直接调用 AsyncQdrantClient
        # 的默认 port=6333 会把本应访问 443 的请求发到错误端口，测试会把环境故障
        # 误报成清理恢复失败。
        client = build_qdrant_client(settings)
        created = aliased = False
        now = datetime.now(UTC)
        sessions = isolated_database.sessions
        documents = await seed_documents(sessions, [(now - timedelta(days=200), now, ProcessingStatus.INDEXED)] * 3)
        try:
            await client.create_collection(settings.collection_name, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
            created = True
            await client.update_collection_aliases([models.CreateAliasOperation(create_alias=models.CreateAlias(collection_name=settings.collection_name, alias_name=settings.collection_alias))])
            aliased = True
            await client.create_payload_index(settings.collection_alias, "document_id", models.PayloadSchemaType.KEYWORD, wait=True)
            await client.upsert(settings.collection_alias, [models.PointStruct(id=str(uuid4()), vector=[1.0, 0.0], payload={"document_id": str(document.id), "knowledge_base_id": str(document.knowledge_base_id)}) for document in documents for _ in range(2)], wait=True)
            store = QdrantDeletionStore(client, settings)
            coordinator = WriteCoordinator(sessions)

            class FailingFinish(DocumentRetentionRepository):
                async def finish(self, records):
                    raise RuntimeError("模拟远端成功后 PostgreSQL 收尾失败")

            async with coordinator.hold(("sync", "index")):
                async with sessions() as session:
                    result = await DocumentRetentionService(FailingFinish(session), store, clock=lambda: now).prune_old_documents(180, False, knowledge_base_ids=(DEFAULT_NEWS_KNOWLEDGE_BASE_ID,))
                    assert result.documents_deleted == 0 and result.qdrant_points_deleted == 6
                    assert await session.scalar(select(func.count()).select_from(DocumentRecord)) == 3
                    intents = (await session.scalars(select(DocumentDeletionRecord))).all()
                    assert len(intents) == 3 and all(item.qdrant_deleted for item in intents)
            assert await store.count_by_document_ids([str(item.id) for item in documents]) == 0
            async with coordinator.hold(("sync", "index")):
                async with sessions() as session:
                    resumed = await DocumentRetentionService(DocumentRetentionRepository(session), store, clock=lambda: now).prune_old_documents(180, False, knowledge_base_ids=(DEFAULT_NEWS_KNOWLEDGE_BASE_ID,))
                    assert resumed.documents_deleted == 3 and resumed.qdrant_points_deleted == 0
                    assert await session.scalar(select(func.count()).select_from(DocumentRecord)) == 0
                    assert await session.scalar(select(func.count()).select_from(DocumentDeletionRecord)) == 0
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
    run(verify())
