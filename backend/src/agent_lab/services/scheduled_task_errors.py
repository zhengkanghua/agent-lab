"""定时任务模块的领域异常：自带稳定 code 与安全中文 detail。

本模块是叶子模块，被调度器（runner）、请求级 Service 和 API 错误映射共同引用，自己
不 import 项目内任何模块。与 ``UserAdminDomainError`` 同一模式：异常携带的是**预写的
安全文案**，不是 ``str(error)``，因此把这些异常映射进 HTTP 响应不会泄露内部细节。
"""

from uuid import UUID


class ScheduledJobDomainError(Exception):
    """定时任务模块预期失败的基类；子类用类属性固定 code 与 detail。"""

    code: str = "scheduled_job_error"
    detail: str = "定时任务操作失败。"


class ScheduledJobNotFoundError(ScheduledJobDomainError):
    """按 id 找不到定时任务（或已被删除）。"""

    code = "scheduled_job_not_found"
    detail = "定时任务不存在。"


class ScheduledJobKeyConflictError(ScheduledJobDomainError):
    """创建时的业务唯一键（key）与既有任务重复。"""

    code = "scheduled_job_key_conflict"
    detail = "同名任务标识已存在。"


class ScheduledJobInvalidCronError(ScheduledJobDomainError):
    """cron 表达式无法被解析成合法的 5 段式触发器。"""

    code = "scheduled_job_invalid_cron"
    detail = "cron 表达式无效，需要 5 段式 cron（分 时 日 月 周）。"


class ScheduledJobInvalidParamsError(ScheduledJobDomainError):
    """任务参数不符合所选任务类型的 schema。"""

    code = "scheduled_job_invalid_params"
    detail = "任务参数与所选任务类型不匹配。"


class ScheduledJobUnknownTypeError(ScheduledJobDomainError):
    """task_type 不在代码注册表里。"""

    code = "scheduled_job_unknown_type"
    detail = "未知的任务类型。"


class ScheduledJobAlreadyRunningError(ScheduledJobDomainError):
    """手动触发时上一轮执行尚未结束；按运行策略跳过而不是排队。

    Attributes:
        job_id: 触发时指定的任务 id，便于日志定位（不含其他身份信息）。
    """

    code = "scheduled_job_already_running"
    detail = "任务正在执行中，请等待本轮结束后再触发。"

    def __init__(self, job_id: UUID) -> None:
        """记录冲突的任务 id，不携带任何异常文本。"""

        super().__init__(job_id)
        self.job_id = job_id


__all__ = [
    "ScheduledJobAlreadyRunningError",
    "ScheduledJobDomainError",
    "ScheduledJobInvalidCronError",
    "ScheduledJobInvalidParamsError",
    "ScheduledJobKeyConflictError",
    "ScheduledJobNotFoundError",
    "ScheduledJobUnknownTypeError",
]
