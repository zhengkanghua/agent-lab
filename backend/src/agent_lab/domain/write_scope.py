"""跨库写入结果的本次状态；不依赖数据库或应用 Service。"""

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps


class WriteRecoveryRequiredError(RuntimeError):
    """远端写入或旧执行者结果未确认，需人工核实后才能继续。"""

    def __init__(self, *, stats: dict | None = None):
        super().__init__("写操作结果需要核实。")
        self.stats = stats or {}


class WriteResourceBusyError(RuntimeError):
    """写资源正被其他操作使用，要求立即受理的配置变更返回冲突。"""


class DocumentDeletionPendingError(RuntimeError):
    """Document 仍有删除待办，来源同步不得将其当作已成功保存。"""


@dataclass
class WriteScope:
    resources: tuple[str, ...]
    uncertain: bool = False


write_scope: ContextVar[WriteScope | None] = ContextVar("document_write_scope", default=None)


def ensure_write_confirmed() -> None:
    scope = write_scope.get()
    if scope is not None and scope.uncertain:
        raise WriteRecoveryRequiredError()


def remote_write(function):
    """写请求异常或取消并不证明远端未执行，保留不确定结果。"""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        ensure_write_confirmed()
        try:
            return await function(*args, **kwargs)
        except BaseException:
            scope = write_scope.get()
            if scope is not None:
                scope.uncertain = True
            raise
    return wrapped
