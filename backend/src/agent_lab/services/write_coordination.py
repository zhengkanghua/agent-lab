"""用短事务协调所有新闻写入口；失联不自动解锁，避免旧执行者恢复后写回。

事务咨询锁只保护占用表的检查与更新，不跨网络调用持有。持久占用覆盖整个业务
工作单元，heartbeat 只用于诊断，不作为抢占许可。
"""

import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text, update

from agent_lab.models.write_operation import WriteOperationRecord
from agent_lab.services.execution_cleanup import finish_cleanup
from agent_lab.domain.write_scope import (
    WriteRecoveryRequiredError, WriteScope, ensure_write_confirmed, write_scope as _scope,
)

logger = logging.getLogger(__name__)
WRITE_MUTEX = 714029831
OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"
current_run_id: ContextVar[UUID | None] = ContextVar("scheduled_run_id", default=None)


class WriteCoordinator:
    """占用同步和索引资源，清理一次取得两者；正常完成删除占用，异常保留。"""

    def __init__(self, session_factory, *, poll_seconds: float = 1) -> None:
        self._sessions = session_factory
        self._poll_seconds = poll_seconds

    @asynccontextmanager
    async def hold(self, resources: tuple[str, ...]):
        parent = _scope.get()
        if parent is not None:
            if not set(resources) <= set(parent.resources):
                raise RuntimeError("嵌套写操作不能扩大已经取得的资源范围。")
            ensure_write_confirmed()
            yield
            return
        scope = WriteScope(tuple(sorted(set(resources))))
        operation_id = uuid4()
        now = datetime.now(UTC)
        async with self._sessions() as session:
            session.add(WriteOperationRecord(
                id=operation_id, resources=list(scope.resources), owner=OWNER,
                status="waiting", started_at=now, heartbeat_at=now,
                run_id=current_run_id.get(),
            ))
            await session.commit()
        task = asyncio.current_task()
        heartbeat = asyncio.create_task(self._heartbeat(operation_id, task, scope))
        token = _scope.set(scope)
        acquired = False
        failure = None
        try:
            logger.info("写操作等待资源 operation_id=%s run_id=%s resources=%s owner=%s", operation_id, current_run_id.get(), scope.resources, OWNER)
            while not await self._acquire(operation_id, scope.resources):
                await asyncio.sleep(self._poll_seconds)
            acquired = True
            logger.info("写操作已取得资源 operation_id=%s run_id=%s resources=%s", operation_id, current_run_id.get(), scope.resources)
            yield
            ensure_write_confirmed()
        except asyncio.CancelledError:
            scope.uncertain = scope.uncertain or acquired
            raise
        except Exception as exc:
            failure = exc
            raise
        finally:
            try:
                await finish_cleanup(self._release(operation_id, scope, heartbeat))
            except Exception as exc:
                logger.error("写操作收尾失败 operation_id=%s error_type=%s", operation_id, type(exc).__name__)
                raise WriteRecoveryRequiredError(stats=getattr(failure, "stats", None)) from exc
            finally:
                _scope.reset(token)

    async def _release(self, operation_id, scope, heartbeat):
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        # 提交失败保留旧行，后续不会默默取得资源。
        async with self._sessions() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WRITE_MUTEX})
            if scope.uncertain:
                await session.execute(update(WriteOperationRecord).where(
                    WriteOperationRecord.id == operation_id,
                ).values(status="uncertain"))
                logger.error("写操作结果待核实 operation_id=%s owner=%s", operation_id, OWNER)
            else:
                await session.execute(delete(WriteOperationRecord).where(WriteOperationRecord.id == operation_id))
            await session.commit()

    async def _acquire(self, operation_id: UUID, resources: tuple[str, ...]) -> bool:
        async with self._sessions() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": WRITE_MUTEX})
            operations = list((await session.scalars(select(WriteOperationRecord).order_by(
                WriteOperationRecord.started_at, WriteOperationRecord.id,
            ))).all())
            own = next((operation for operation in operations if operation.id == operation_id), None)
            if own is None:
                raise WriteRecoveryRequiredError()
            for operation in operations:
                if operation.id == operation_id or not set(resources).intersection(operation.resources):
                    continue
                if operation.status == "uncertain" or operation.heartbeat_at < datetime.now(UTC) - timedelta(seconds=30):
                    raise WriteRecoveryRequiredError()
                if operation.status == "active" or (operation.started_at, operation.id) < (own.started_at, own.id):
                    return False
            own.status = "active"
            await session.commit()
            return True

    async def _heartbeat(self, operation_id: UUID, task, scope: WriteScope) -> None:
        try:
            while True:
                await asyncio.sleep(5)
                async with self._sessions() as session:
                    result = await session.execute(update(WriteOperationRecord).where(
                        WriteOperationRecord.id == operation_id,
                    ).values(heartbeat_at=datetime.now(UTC)))
                    if result.rowcount != 1:
                        raise WriteRecoveryRequiredError()
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            scope.uncertain = True
            logger.error("写操作心跳失败 operation_id=%s error_type=%s", operation_id, type(exc).__name__)
            task.cancel()
