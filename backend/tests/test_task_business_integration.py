"""真实 HTTP/Redis/Beat/solo Worker 联合验收业务装配与让出执行位置的资源等待。

使用随机 PostgreSQL schema 和共享 Redis 随机键；外部来源、原件、Embedding 与向量
端口为替身，Docling、tokenizer、任务注册、业务应用、资源协调及事务均为真实实现。
"""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from agent_lab.models.document import DocumentRecord
from agent_lab.models.document_processing import DocumentProcessingRecord, DocumentVersion
from agent_lab.models.scheduled_job import JobRunRecord
from agent_lab.models.source import SourceRecord
from tests.task_business_support import SOURCE
from tests.test_task_local_integration import LocalTaskEnvironment

pytestmark = pytest.mark.skipif(os.getenv("RUN_TASK_LOCAL_INTEGRATION_TEST") != "1",
    reason="需授权已有 PostgreSQL/Redis 的隔离资源及真实本地进程。")


@pytest.fixture
def business_environment(tmp_path):
    environment = LocalTaskEnvironment(tmp_path)
    environment.env.update(TASK_TEST_BUSINESS_DIRECTORY=str(tmp_path), DOCUMENT_CHUNK_MAX_TOKENS="128",
        DOCUMENT_TOKENIZER_PATH=str(Path(__file__).parents[1] / ".cache/tokenizers/bge-m3"))
    try:
        environment.open()
        yield environment
    finally:
        environment.close()


def test_business_entries_and_resource_wait_use_the_same_real_worker(business_environment):
    env = business_environment
    env.login()
    (env.directory / "source-page.txt").write_text("1")

    async def seed_source():
        async with env.sessions() as session:
            session.add(SourceRecord(provider=SOURCE.provider, external_id=SOURCE.external_id, name=SOURCE.name,
                knowledge_base_id=env.knowledge_base_id))
            await session.commit()
    env.runner.run(seed_source())

    def submit(task_type, key):
        response = env.client.post("/task-runs", headers={"Idempotency-Key": key},
            json={"task_type": task_type, "params": {}})
        assert response.status_code == 202
        return response.json()["run_id"]

    async def business_runs():
        async with env.sessions() as session:
            return list((await session.scalars(select(JobRunRecord).where(
                JobRunRecord.task_type == "document_processing").order_by(JobRunRecord.accepted_at))).all())

    holder = env.start("index-holder", "tests.task_business_support")
    env.wait(lambda: (env.directory / "index-held").exists())
    env.start_worker()  # 此随机队列只有一个 solo Worker，不能借另一执行位置绕过等待。
    cleanup = env.submit("wait-for-index")["run_id"]
    env.wait(lambda: env.detail(cleanup)["status"] == "waiting_resource")
    waiting = env.detail(cleanup)
    assert waiting["attempts"] == 0 and waiting["wait_reason"]

    # 索引资源仍由独立进程持有；同步只需要 sync，同一 Worker 必须能先完成它。
    sync = submit("freshrss_sync", "first-sync")
    assert env.wait_success(sync)["stats"]["synchronized_document_count"] == 1
    assert holder.poll() is None and env.detail(cleanup)["status"] == "waiting_resource"
    env.report["checks"].append("waiting_resource_releases_the_only_worker_without_an_attempt")
    (env.directory / "release-index").touch()
    assert holder.wait(timeout=20) == 0
    env.children.remove(holder)

    # Beat 尚未启动：显式 index_pending 处理第一份非空资料，业务批次回执仍持久排队。
    batch = env.runner.run(business_runs())
    assert len(batch) == 1 and batch[0].status == "queued"
    index = submit("index_pending", "explicit-index")
    indexed = env.wait_success(index)
    assert indexed["stats"]["parsed_count"] == indexed["stats"]["indexed_count"] == 1
    (env.directory / "source-page.txt").write_text("2")
    assert env.wait_success(submit("freshrss_sync", "second-sync"))["stats"]["synchronized_document_count"] == 1

    env.start_beat()
    assert env.wait_success(cleanup)["attempts"] == 1
    processed = env.wait_success(str(batch[0].id))
    assert processed["trigger_type"] == "business" and processed["job_id"] is None
    assert processed["stats"]["parsed_count"] == processed["stats"]["indexed_count"] == 1
    env.report["checks"].extend(["original_cleanup_resumes_after_release", "nonempty_index_pending",
        "source_intake_persists_and_beat_delivers_nonempty_document_batch"])

    # Pipeline HTTP 仍只受理；真实 Worker 中同步第三份资料并完成同一批次的采用。
    (env.directory / "source-page.txt").write_text("3")
    accepted = env.client.post("/pipeline/run-once", headers={"Idempotency-Key": "pipeline"}, json={})
    assert accepted.status_code == 202
    pipeline = env.wait_success(accepted.json()["run_id"])
    assert pipeline["stats"]["ok"] is True
    assert pipeline["stats"]["sync"]["synchronized_document_count"] == 1
    assert pipeline["stats"]["index"]["indexed_document_count"] == 1
    env.wait(lambda: all(item.status == "succeeded" for item in env.runner.run(business_runs())))

    async def document_state():
        async with env.sessions() as session:
            documents = list((await session.scalars(select(DocumentRecord).order_by(DocumentRecord.external_id))).all())
            candidates = list((await session.scalars(select(DocumentProcessingRecord))).all())
            versions = list((await session.scalars(select(DocumentVersion))).all())
            return documents, candidates, versions
    documents, candidates, versions = env.runner.run(document_state())
    assert len(documents) == len(candidates) == len(versions) == 3
    assert all(item.state == "adopted" for item in candidates)
    assert all(item.current_version_id and item.current_index_instance_id for item in documents)
    for number, document in enumerate(documents, start=1):
        assert f"Body for task execution {number}." in document.content_text
    points = [json.loads(path.read_text(encoding="utf-8")) for path in (env.directory / "points").glob("*.json")]
    assert len(points) == 3 and len({item["process_id"] for item in points}) == 1
    assert {item["run_id"] for item in points} == {index, str(batch[0].id), pipeline["id"]}
    assert all(item["vector_count"] == len(item["chunks"]) > 0 for item in points)
    env.report["checks"].extend(["http_pipeline_processed_nonempty_source_in_worker", "three_adopted_documents_and_versions"])
    env.report["executions"] = {"cleanup": cleanup, "sync": sync, "index": index,
        "document_processing": str(batch[0].id), "pipeline": pipeline["id"]}
    env.report["adopted_documents"] = [str(item.id) for item in documents]
    env.stop_worker()
    assert env.worker.returncode == 0 and "task_local_runtime_closed" in env.worker_log()
