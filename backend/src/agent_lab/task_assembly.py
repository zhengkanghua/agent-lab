"""公共任务与知识库的装配边界；新增业务只在这里注册，不修改任务核心。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from agent_lab.tasks.dispatch import TaskDispatcher
from agent_lab.tasks.registry import TaskRegistry
from agent_lab.tasks.repository import TaskStore
from agent_lab.tasks.service import TaskService
from agent_lab.tasks.worker import TaskWorker


def build_task_registry(sessions):
    from agent_lab.pipeline.assembly import build_pipeline_write_runtime
    from agent_lab.services.scheduled_task_registry import TASK_TYPE_SPECS
    from agent_lab.knowledge.task_intake import continue_document_processing
    from agent_lab.services.write_coordination import discard_task_preparation
    return TaskRegistry(replace(spec,
        runtime_factory=lambda: build_pipeline_write_runtime(sessions),
        on_success=continue_document_processing if spec.task_type == "document_processing" else None,
        discard_preparation=discard_task_preparation,
    ) for spec in TASK_TYPE_SPECS.values())


async def publish_message(run_id, generation):
    from agent_lab.tasks.celery_app import app
    # Celery 的发布客户端是同步的；网络不占 API 事件循环或数据库事务。
    await asyncio.to_thread(app.send_task, "agent_lab.execute", args=[run_id, generation], retry=False)


def build_task_components(sessions, *, owner="api"):
    from agent_lab.config.task_queue import get_task_queue_settings
    registry = build_task_registry(sessions)
    store = TaskStore(sessions)
    dispatcher = TaskDispatcher(store, publish_message, retry_seconds=get_task_queue_settings().redelivery_seconds)
    return (TaskService(sessions, registry, dispatcher=dispatcher),
            TaskWorker(store, registry, owner=owner), dispatcher, store)


def build_task_service():
    from agent_lab.db.session import async_session_factory
    return build_task_components(async_session_factory)[0]


async def bootstrap_legacy_document_processing(session):
    """旧执行入口已停止后的一次性交接；先回收遗留纯计算，再受理首批。"""
    from agent_lab.knowledge.adapters.processing import PostgresProcessingRepository
    from agent_lab.knowledge.task_intake import ensure_document_processing

    # 旧消费者已经停止，无需再等解析领取超时。复用业务回收规则及其事务受理，
    # 不触碰索引准备、发布和接收等可能仍需核实的远端写入。
    await PostgresProcessingRepository(session).requeue_computations(started_before=datetime.now(UTC))
    run_id = await ensure_document_processing(session)
    await session.commit()
    return run_id
