"""随机 PostgreSQL schema 内验证文档待办与任务交接的真实进程中断。

只建立合成业务记录，不访问原件、解析器、模型或消息代理。子进程在事务已经写入、
尚未提交时退出，父进程核对回滚和重新提交后的业务依据；不修改共享业务数据。
"""

import asyncio
from datetime import UTC, datetime
import multiprocessing
import os
from uuid import UUID

import pytest
from sqlalchemy import func, select

from agent_lab.domain.enums import ProcessingStatus
from agent_lab.knowledge.task_intake import continue_document_processing, ensure_document_processing
from agent_lab.knowledge.adapters.processing import PostgresProcessingRepository
from agent_lab.models.document_processing import DocumentProcessingRecord
from agent_lab.models.scheduled_job import JobRunRecord, TaskRequestRecord
from agent_lab.task_assembly import bootstrap_legacy_document_processing
from agent_lab.tasks.repository import TaskStore
from tests.test_scheduler_postgres_integration import child, database, isolated_database, run, seed_documents

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST") != "1",
    reason="需要显式授权随机 PostgreSQL schema 和测试子进程故障验证。",
)


async def add_pending(session, document_id):
    """只构造持久交接所需的合成待办，不声称验证了原件上传。"""
    pending = DocumentProcessingRecord(document_id=document_id, source_kind="freshrss",
        state="pending", source_stored_at=datetime.now(UTC))
    session.add(pending)
    await session.flush()
    return pending


def crash_before_handoff_commit(dsn, schema, stage, identity, claim_token, output, release):
    """等父进程收到已写入信号后直接退出，确保绕过连接和事务的正常收尾。"""
    async def scenario():
        engine, sessions = database(dsn, schema)

        async def stop():
            output.put("prepared")
            assert await asyncio.to_thread(release.wait, 30)
            os._exit(23)

        async def completion(session, completed):
            await continue_document_processing(session, completed)
            await stop()

        try:
            if stage == "intake":
                async with sessions() as session:
                    await add_pending(session, UUID(identity))
                    assert await ensure_document_processing(session) is not None
                    await stop()
            else:
                await TaskStore(sessions).finish(UUID(identity), UUID(claim_token), status="succeeded",
                    stats={"processed": 1, "failed": 1}, on_success=completion)
        finally:
            await engine.dispose()
    run(scenario())


def interrupt_handoff(db, stage, identity, claim_token=None):
    context = multiprocessing.get_context("spawn")
    output, release = context.Queue(), context.Event()
    try:
        with child(crash_before_handoff_commit, db.dsn, db.schema, stage, str(identity),
                   str(claim_token) if claim_token else None, output, release) as process:
            assert output.get(timeout=30) == "prepared"
            release.set()
            process.join(10)
            assert process.exitcode == 23
    finally:
        release.set()
        output.close()
        output.join_thread()


def test_intake_process_exit_keeps_pending_and_execution_atomic(isolated_database):
    db = isolated_database
    now = datetime.now(UTC)
    document, = run(seed_documents(db.sessions, [(now, now, ProcessingStatus.PENDING)]))
    interrupt_handoff(db, "intake", document.id)

    async def verify():
        async with db.sessions() as session:
            for model in (DocumentProcessingRecord, JobRunRecord, TaskRequestRecord):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
            pending = await add_pending(session, document.id)
            identity = await ensure_document_processing(session)
            await session.commit()
        async with db.sessions() as session:
            assert (await session.get(DocumentProcessingRecord, pending.id)).state == "pending"
            assert (await session.get(JobRunRecord, identity)).status == "queued"
            assert await session.scalar(select(TaskRequestRecord.run_id)) == identity
    run(verify())


def test_batch_completion_process_exit_keeps_pending_and_successor_atomic(isolated_database):
    db = isolated_database
    now = datetime.now(UTC)

    async def prepare():
        documents = await seed_documents(db.sessions, [(now, now, ProcessingStatus.PENDING)] * 2)
        async with db.sessions() as session:
            completed = await add_pending(session, documents[0].id)
            remaining = await add_pending(session, documents[1].id)
            identity = await ensure_document_processing(session)
            await session.commit()
        store = TaskStore(db.sessions)
        claim = await store.claim(identity, 1, "test:handoff-writer")
        assert await store.start(identity, claim.claim_token)
        # 单篇失败已经按业务边界提交，本批仍正常结束；下一篇待办必须有后续入口。
        async with db.sessions() as session:
            record = await session.get(DocumentProcessingRecord, completed.id)
            record.state, record.error_code = "failed", "synthetic_parse_failure"
            await session.commit()
        return claim, completed.id, remaining.id

    claim, completed_id, remaining_id = run(prepare())
    interrupt_handoff(db, "completion", claim.id, claim.claim_token)

    async def verify():
        async with db.sessions() as session:
            assert (await session.get(JobRunRecord, claim.id)).status == "running"
            assert (await session.get(DocumentProcessingRecord, completed_id)).state == "failed"
            assert (await session.get(DocumentProcessingRecord, remaining_id)).state == "pending"
            for model in (JobRunRecord, TaskRequestRecord):
                assert await session.scalar(select(func.count()).select_from(model)) == 1
        # 这里只重试已知结果的持久交接，不重新执行业务；崩溃后的执行者核实另由队列验收覆盖。
        store = TaskStore(db.sessions)
        for _ in range(2):
            assert await store.finish(claim.id, claim.claim_token, status="succeeded",
                stats={"processed": 1, "failed": 1}, on_success=continue_document_processing)
        async with db.sessions() as session:
            runs = list((await session.scalars(select(JobRunRecord))).all())
            assert len(runs) == 2
            assert (await session.get(JobRunRecord, claim.id)).status == "succeeded"
            successor = next(item for item in runs if item.id != claim.id)
            assert successor.status == "queued" and successor.concurrency_key == claim.concurrency_key
            assert await session.scalar(select(TaskRequestRecord.run_id).where(
                TaskRequestRecord.request_key == f"after:{claim.id}",
            )) == successor.id
            assert (await session.get(DocumentProcessingRecord, completed_id)).state == "failed"
            assert (await session.get(DocumentProcessingRecord, remaining_id)).state == "pending"
    run(verify())


def test_bootstrap_recovers_legacy_computations_without_restarting_uncertain_writes(isolated_database):
    """仅剩解析／预览领取时也有首批；旧计算结果失效，未决写入与失败状态保留。"""
    db = isolated_database
    now = datetime.now(UTC)

    async def verify():
        documents = await seed_documents(db.sessions, [(now, now, ProcessingStatus.PENDING)] * 5)
        async with db.sessions() as session:
            source = await add_pending(session, documents[0].id)
            draft = await add_pending(session, documents[1].id)
            for pending in (source, draft):
                pending.source_object_key = f"synthetic/legacy/{pending.id}"
                pending.source_sha256, pending.source_size, pending.source_mime_type = "a" * 64, 16, "text/plain"
            draft.requires_review, draft.draft_text, draft.draft_mime_type = True, "保留人工草稿", "text/markdown"
            protected = [DocumentProcessingRecord(document_id=document.id, source_kind="freshrss", state=state)
                         for document, state in zip(documents[2:], ("indexing", "publishing", "failed"), strict=True)]
            session.add_all(protected)
            await session.commit()
            repository = PostgresProcessingRepository(session)
            claims = [await repository.claim(identity) for identity in (source.id, draft.id)]
            assert all(claim is not None for claim in claims)
            assert await session.scalar(select(func.count()).select_from(JobRunRecord)) == 0
            # 新近领取也能在确认停止旧消费者后的交接中回收，不能依赖未来另一个批次唤醒。
            run_id = await bootstrap_legacy_document_processing(session)
            assert run_id is not None
            assert await bootstrap_legacy_document_processing(session) == run_id

        async with db.sessions() as session:
            accepted = await session.get(JobRunRecord, run_id)
            assert accepted.status == "queued" and accepted.task_type == "document_processing"
            assert await session.scalar(select(func.count()).select_from(JobRunRecord)) == 1
            assert await session.scalar(select(TaskRequestRecord.run_id)) == run_id
            for claim in claims:
                pending = await session.get(DocumentProcessingRecord, claim.id)
                assert pending.state == "pending" and pending.claim_token is None and pending.claimed_at is None
                assert not await PostgresProcessingRepository(session).save_failure(claim, "late_result", state="failed")
            retained_draft = await session.get(DocumentProcessingRecord, draft.id)
            assert retained_draft.draft_text == "保留人工草稿" and retained_draft.requires_review
            for original in protected:
                assert (await session.get(DocumentProcessingRecord, original.id)).state == original.state
    run(verify())
