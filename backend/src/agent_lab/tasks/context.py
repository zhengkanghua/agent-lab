"""执行上下文只传递数据库身份，不携带业务参数或连接。"""

from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

current_run_id: ContextVar[UUID | None] = ContextVar("task_run_id", default=None)


@dataclass
class TaskClaim:
    """本进程的领取身份；started 只在数据库允许开始后、调用业务前置为真。"""

    run_id: UUID
    token: UUID
    started: bool = False


current_claim: ContextVar[TaskClaim | None] = ContextVar("task_claim", default=None)
