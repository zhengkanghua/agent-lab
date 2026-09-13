"""真实队列测试的无上游业务装配；生产受理、领取、收尾和进程循环保持原实现。"""

import asyncio
import os
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_lab.models.scheduled_job import JobRunRecord
from agent_lab.tasks.context import current_run_id
from agent_lab.tasks.contracts import FailureDecision, RecoveryDecision
from agent_lab.tasks.dispatch import TaskDispatcher
from agent_lab.tasks.registry import TaskRegistry, TaskTypeSpec
from agent_lab.tasks.repository import TaskStore
from agent_lab.tasks.service import TaskService
from agent_lab.tasks.worker import TaskWorker


class ProbeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = 1
    seconds: float = Field(default=0.1, ge=0, le=120)
    failures: int = Field(default=0, ge=0, le=4)
    after_completion_seconds: float = Field(default=0, ge=0, le=120)
    wait_for_release: bool = False


class ProbeTransientError(Exception):
    """测试业务已知无未决外部写入的短暂失败。"""


def database(dsn, schema):
    if not re.fullmatch(r"task_queue_test_[0-9a-f]{32}", schema):
        raise ValueError("只允许连接测试随机 schema。")
    engine = create_async_engine(dsn, pool_size=2, max_overflow=2, hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema} -ctimezone=UTC -cstatement_timeout=10000", "connect_timeout": 5})
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def registry(sessions):
    async def probe(_runtime, params):
        run_id = current_run_id.get()
        loop_id = id(asyncio.get_running_loop())
        async with sessions() as session:
            attempt = (await session.get(JobRunRecord, run_id)).attempts
            await session.execute(text("""INSERT INTO task_probe_events
                (run_id, attempt, process_id, loop_id, value, started_at)
                VALUES (:run_id, :attempt, :pid, :loop, :value, now())"""),
                {"run_id": run_id, "attempt": attempt, "pid": os.getpid(), "loop": loop_id, "value": params["value"]})
            await session.commit()
        await asyncio.sleep(params["seconds"])
        if params["wait_for_release"]:
            # 保持真实消息未确认，直到主测试实际观察到重投或完成断线实验。
            async with asyncio.timeout(150):
                while True:
                    async with sessions() as session:
                        released = await session.scalar(text("""SELECT released_at IS NOT NULL
                            FROM task_probe_events WHERE run_id = :id AND attempt = :attempt"""),
                            {"id": run_id, "attempt": attempt})
                    if released:
                        break
                    await asyncio.sleep(0.2)
        if attempt <= params["failures"]:
            raise ProbeTransientError()
        async with sessions() as session:
            await session.execute(text("""UPDATE task_probe_events SET completed_at = now()
                WHERE run_id = :id AND attempt = :attempt"""), {"id": run_id, "attempt": attempt})
            await session.commit()
        await asyncio.sleep(params["after_completion_seconds"])
        return {"value": params["value"], "process_id": os.getpid(), "loop_id": loop_id}

    async def recover(run):
        # 只有这项合成业务的提交证据足以确认完成；它不替真实远端业务做恢复判断。
        async with sessions() as session:
            evidence = (await session.execute(text("""SELECT value, process_id, loop_id
                FROM task_probe_events WHERE run_id = :id AND attempt = :attempt
                AND completed_at IS NOT NULL"""), {"id": run.id, "attempt": run.attempts})).mappings().first()
        return RecoveryDecision(action="succeeded", stats=dict(evidence), reason="合成业务完成记录已提交") if evidence else RecoveryDecision()

    return TaskRegistry([TaskTypeSpec(task_type="queue_probe", description="真实消息与进程验收探针",
        params_model=ProbeParams, execute=probe,
        classify_error=lambda error: FailureDecision(retryable=isinstance(error, ProbeTransientError)),
        recover=recover)])


def components(sessions, publisher, *, owner):
    store = TaskStore(sessions)
    types = registry(sessions)
    dispatcher = TaskDispatcher(store, publisher, retry_seconds=1)
    return (TaskService(sessions, types, dispatcher=dispatcher),
            TaskWorker(store, types, owner=owner, heartbeat_seconds=1), dispatcher, store)


async def event_rows(sessions, run_id: UUID | None = None):
    async with sessions() as session:
        statement = "SELECT * FROM task_probe_events"
        if run_id is not None:
            statement += " WHERE run_id = :id"
        return list((await session.execute(text(statement), {"id": run_id} if run_id else {})).mappings())
