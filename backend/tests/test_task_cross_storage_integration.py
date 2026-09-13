"""显式授权的 PostgreSQL + Qdrant + S3 组合恢复验收；不访问模型或 FreshRSS。

三个存储都使用真实客户端。故障只注入本测试的删除回执或 Repository 确认边界，
资源限定为随机 schema、Collection/Alias 和 acceptance/task-recovery/ 下的精确对象键。
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from qdrant_client import models
from sqlalchemy import select, update

from agent_lab.config.object_storage import ObjectStorageSettings
from agent_lab.config.qdrant import QdrantSettings
from agent_lab.domain.enums import ProcessingStatus
from agent_lab.domain.write_scope import remote_write
from agent_lab.knowledge.storage import S3ObjectStorage
from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentVersion
from agent_lab.models.scheduled_job import JobRunRecord
from agent_lab.models.write_operation import DocumentDeletionRecord, WriteOperationRecord
from agent_lab.qdrant.lifecycle import build_qdrant_client
from agent_lab.qdrant.store import QdrantDeletionStore
from agent_lab.scheduler_maintenance import inspect_or_recover
from agent_lab.services.document_retention_service import DocumentRetentionService
from agent_lab.repositories.document_retention_repository import DocumentRetentionRepository
from agent_lab.services.scheduled_job_service import ScheduledJobService
from agent_lab.services.scheduled_task_registry import TASK_TYPE_SPECS
from agent_lab.tasks.contracts import RetryUnavailable
from agent_lab.tasks.cron import CronSchedule
from agent_lab.tasks.registry import TaskRegistry
from agent_lab.tasks.repository import TaskStore
from agent_lab.tasks.service import TaskService
from agent_lab.tasks.worker import TaskWorker
from tests.test_scheduler_postgres_integration import isolated_database, run, seed_documents

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_TASK_CROSS_STORAGE_INTEGRATION_TEST") != "1",
    reason="需要明确授权 PostgreSQL/Qdrant/S3 随机资源读写与删除。",
)


@pytest.mark.parametrize("fault", [
    "qdrant_confirmation", "object_confirmation", "postgres_finish",
    "qdrant_ack_lost", "object_ack_lost",
])
def test_real_storage_recovery_keeps_completed_work_and_survives_history_cleanup(isolated_database, fault):
    endpoint = os.environ.get("SCHEDULER_TEST_QDRANT_URL")
    storage_settings = ObjectStorageSettings()
    if not endpoint or not storage_settings.endpoint:
        pytest.fail("需显式提供 SCHEDULER_TEST_QDRANT_URL 和 S3_* 私有桶配置。", pytrace=False)
    token = uuid4().hex
    settings = QdrantSettings(_env_file=None, base_url=endpoint,
        api_key=os.environ.get("SCHEDULER_TEST_QDRANT_API_KEY", ""), environment=f"scheduler_test_{token}",
        collection_schema_version="v3", vector_dimension=2, collection_generation=1)
    report = {"fault": fault, "schema": isolated_database.schema, "collection": settings.collection_name,
              "alias": settings.collection_alias, "keys": [], "stage": "preparing", "remote_resources_cleaned": False,
              "schema_cleanup": "由 isolated_database 在 pytest 夹具退出时执行"}

    async def verify():
        client = build_qdrant_client(settings)
        fault_enabled = False
        uncertain = fault in {"qdrant_ack_lost", "object_ack_lost"}
        sessions = isolated_database.sessions
        now = datetime.now(UTC)
        vector_deletions, object_deletions = [], []

        class ObservedPoints(QdrantDeletionStore):
            @remote_write
            async def delete_by_document_ids(self, identities):
                vector_deletions.extend(identities)
                await super().delete_by_document_ids(identities)
                if fault_enabled and fault == "qdrant_ack_lost":
                    raise RuntimeError("模拟 Qdrant 已删除但应用未收到回执")

        class ObservedOriginals(S3ObjectStorage):
            @remote_write
            async def delete(self, key, *, version_id=None):
                object_deletions.append(key)
                await super().delete(key, version_id=version_id)
                if fault_enabled and fault == "object_ack_lost":
                    raise RuntimeError("模拟 S3 已删除但应用未收到回执")

        class LostConfirmation(DocumentRetentionRepository):
            async def mark_qdrant_deleted(self, records):
                if fault == "qdrant_confirmation":
                    raise RuntimeError("模拟 Qdrant 已删除但确认未提交")
                await super().mark_qdrant_deleted(records)

            async def mark_object_deleted(self, document_id, reference):
                if fault == "object_confirmation":
                    raise RuntimeError("模拟 S3 已删除但确认未提交")
                await super().mark_object_deleted(document_id, reference)

            async def finish(self, records):
                if fault == "postgres_finish":
                    raise RuntimeError("模拟远端均已确认但数据库收尾未提交")
                return await super().finish(records)

        storage = ObservedOriginals(storage_settings)
        points = ObservedPoints(client, settings)
        repository_type = DocumentRetentionRepository

        class RetentionRuntime:
            session_factory = sessions

            async def prune_old_documents(self, **params):
                async with sessions() as session:
                    return await DocumentRetentionService(repository_type(session), points, lambda: storage,
                        clock=lambda: now).prune_old_documents(**params)

        registry = TaskRegistry([replace(TASK_TYPE_SPECS["prune_old_documents"], runtime_factory=RetentionRuntime)])
        tasks = TaskService(sessions, registry)
        worker = TaskWorker(TaskStore(sessions), registry, owner=f"cross-storage:{token}")

        async def seed():
            document = (await seed_documents(sessions, [(now - timedelta(days=200), now, ProcessingStatus.INDEXED)]))[0]
            references = []
            for index in range(2):
                key = f"acceptance/task-recovery/{token}/{document.id}/{index}.txt"
                report["keys"].append(key)  # 即使 S3 回执丢失，也保存本次唯一键用于清理。
                references.append(await storage.put(key, f"synthetic {index}".encode(), content_type="text/plain"))
            async with sessions() as session:
                version = await session.get(DocumentVersion, document.current_version_id)
                processing = await session.get(DocumentProcessingRecord, version.processing_id)
                # 当前版本与候选共享一份原件，另一份历史原件同样需要完整清理。
                for record in (version, processing):
                    record.source_object_key = references[0].key
                    record.source_object_version = references[0].version_id
                    record.source_size = references[0].size
                    record.source_sha256 = references[0].sha256
                session.add(DocumentProcessingRecord(document_id=document.id, source_kind="freshrss", state="superseded",
                    source_object_key=references[1].key, source_object_version=references[1].version_id,
                    source_size=references[1].size, source_sha256=references[1].sha256))
                await session.commit()
            await client.upsert(settings.collection_alias, [models.PointStruct(id=str(uuid4()), vector=[1.0, 0.0],
                payload={"document_id": str(document.id), "knowledge_base_id": str(document.knowledge_base_id)}) for _ in range(2)], wait=True)
            return document, references

        async def execute(receipt):
            await worker.execute(receipt.run_id, 1)
            return await tasks.get_run(receipt.run_id)

        try:
            await client.create_collection(settings.collection_name, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
            await client.update_collection_aliases([models.CreateAliasOperation(create_alias=models.CreateAlias(
                collection_name=settings.collection_name, alias_name=settings.collection_alias))])
            await client.create_payload_index(settings.collection_alias, "document_id", models.PayloadSchemaType.KEYWORD, wait=True)
            completed_document, completed_objects = await seed()
            params = {"retention_days": 180, "dry_run": False}
            async with sessions() as session:
                job = (await ScheduledJobService(session, CronSchedule(), registry).create_job(
                    key="cross-storage-prune", task_type="prune_old_documents", cron_expr="0 9 * * *", params=params, enabled=False)).record
            initial = await execute(await tasks.trigger(job.id, actor="test:storage", request_key="completed"))
            assert initial.status == "succeeded" and initial.stats["documents_deleted"] == 1

            target, references = await seed()
            repository_type = LostConfirmation
            fault_enabled = True
            report["stage"] = "partial_failure"
            partial = await execute(await tasks.trigger(job.id, actor="test:storage", request_key="partial"))
            assert partial.status == ("needs_attention" if uncertain else "succeeded")
            assert partial.stats["documents_deleted"] == 0 and partial.stats["failed_documents"] == 1
            report["partial_status"] = partial.status
            assert await points.count_by_document_ids([str(target.id)]) == 0
            async with sessions() as session:
                pending = await session.get(DocumentDeletionRecord, target.id)
                assert pending is not None and (await session.get(DocumentRecord, target.id)).usage_status == "deleting"
                assert pending.qdrant_deleted is (fault not in {"qdrant_confirmation", "qdrant_ack_lost"})
                assert sum(item["deleted"] for item in pending.objects) == (2 if fault == "postgres_finish" else 0)
                operations = list((await session.scalars(select(WriteOperationRecord).where(
                    WriteOperationRecord.run_id == partial.id,
                ))).all())
                if uncertain:
                    assert len(operations) == 1 and operations[0].status == "uncertain"
                    assert set(operations[0].resources) == {"sync", "index"}
                else:
                    assert not operations
            remaining_objects = sum([await storage.inspect(item.key, version_id=item.version_id) is not None for item in references])
            assert remaining_objects == {
                "qdrant_confirmation": 2, "object_confirmation": 1, "postgres_finish": 0,
                "qdrant_ack_lost": 2, "object_ack_lost": 1,
            }[fault]

            # 配置与普通执行详情清掉后，业务待办及原件确认依然独立存在。
            async with sessions() as session:
                await ScheduledJobService(session, CronSchedule(), registry).delete_job(job.id)
            assert await TaskStore(sessions, clock=lambda: now + timedelta(days=31)).prune_history() == (1 if uncertain else 2)
            async with sessions() as session:
                assert await session.get(DocumentDeletionRecord, target.id) is not None
            repository_type = DocumentRetentionRepository
            fault_enabled = False
            report["stage"] = "recovery"
            if uncertain:
                assert (await tasks.get_run(partial.id)).status == "needs_attention"
                with pytest.raises(RetryUnavailable):
                    await tasks.retry(partial.id, actor="test:storage", request_key="before-confirmation")
                # 夹具已等待执行结束，并直接核对真实远端结果；只模拟本次隔离执行的
                # 心跳静默期，再走现有明确恢复入口，不为测试放宽生产维护条件。
                async with sessions() as session:
                    stopped_at = datetime.now(UTC) - timedelta(minutes=1)
                    await session.execute(update(JobRunRecord).where(JobRunRecord.id == partial.id).values(heartbeat_at=stopped_at))
                    await session.execute(update(WriteOperationRecord).where(
                        WriteOperationRecord.run_id == partial.id,
                    ).values(heartbeat_at=stopped_at))
                    await session.commit()
                confirmation = await inspect_or_recover(sessions, run_id=partial.id, confirm_stopped=True)
                assert confirmation["released_operations"] == 1
                resumed = await tasks.retry(partial.id, actor="test:storage", request_key="resume")
            else:
                resumed = await tasks.submit("prune_old_documents", params, actor="test:storage", request_key="resume")
            recovered = await execute(resumed)
            assert recovered.status == "succeeded" and recovered.stats["documents_deleted"] == 1
            assert recovered.stats["failed_documents"] == 0 and recovered.stats["qdrant_points_deleted"] == 0
            async with sessions() as session:
                assert await session.get(DocumentRecord, target.id) is None
                assert await session.get(DocumentDeletionRecord, target.id) is None
            assert all([await storage.inspect(item.key, version_id=item.version_id) is None for item in references])
            assert vector_deletions.count(str(completed_document.id)) == 1
            assert all(object_deletions.count(item.key) == 1 for item in completed_objects)
            report["stage"] = "completed"
        finally:
            fault_enabled = False
            # 不列桶、不改共享 Alias，只核对并清理本次确切资源。
            try:
                for key in report["keys"]:
                    observed = await storage.inspect(key)
                    if observed is not None:
                        await storage.delete(key, version_id=observed.version_id)
                report["objects_cleaned"] = all([await storage.inspect(key) is None for key in report["keys"]])
            finally:
                try:
                    # 创建请求的回执也可能丢失，不能凭客户端标记跳过实际已存在的资源。
                    if any(item.alias_name == settings.collection_alias for item in (await client.get_aliases()).aliases):
                        await client.update_collection_aliases([models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=settings.collection_alias))])
                    report["alias_cleaned"] = not any(
                        item.alias_name == settings.collection_alias for item in (await client.get_aliases()).aliases
                    )
                finally:
                    try:
                        if await client.collection_exists(settings.collection_name):
                            await client.delete_collection(settings.collection_name)
                        report["collection_cleaned"] = not await client.collection_exists(settings.collection_name)
                    finally:
                        await client.close()
            report["remote_resources_cleaned"] = all(report.get(key) for key in ("objects_cleaned", "alias_cleaned", "collection_cleaned"))
            assert report["remote_resources_cleaned"], "本次随机远端资源尚未清理完成，核对测试报告。"

    try:
        run(verify())
    finally:
        output = Path(f".pytest_cache/task-cross-storage-{fault}.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
