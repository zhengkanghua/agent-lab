"""文档待办与公共任务的事务交接；没有常驻消费者，也不轮询重建失败任务。"""

from datetime import UTC, datetime
from uuid import uuid4

from agent_lab.knowledge.adapters.pending_work import has_pending_work
from agent_lab.tasks.repository import TaskRepository, transaction_lock

DOCUMENT_PROCESSING_KEY = "knowledge:document-processing"


async def ensure_document_processing(session, *, predecessor=None):
    """调用方持有业务事务；只在有可处理待办且没有现存执行时受理。"""
    await transaction_lock(session, DOCUMENT_PROCESSING_KEY)
    repository = TaskRepository(session)
    active = await repository.active(concurrency_key=DOCUMENT_PROCESSING_KEY)
    if active is not None:
        return active.id
    if not await has_pending_work(session):
        return None
    from agent_lab.services.scheduled_task_registry import TASK_TYPE_SPECS
    spec = TASK_TYPE_SPECS["document_processing"]
    request_key = f"after:{predecessor.id}" if predecessor else str(uuid4())
    actor, operation = "system:documents", "document-processing"
    digest, accepted = await repository.replay(actor=actor, operation=operation, request_key=request_key, content={})
    if accepted:
        return accepted.run_id
    run = await repository.accept(spec=spec, params=spec.validate_params({}), actor=actor, operation=operation,
        request_key=request_key, digest=digest, now=datetime.now(UTC), trigger_type="business",
        concurrency_key=DOCUMENT_PROCESSING_KEY)
    return run.id


async def continue_document_processing(session, completed):
    """只有正常批次完成才调用，与本批终态一起提交必要的下一批。"""
    await ensure_document_processing(session, predecessor=completed)
