"""定时任务模块的领域异常：自带稳定 code 与安全中文 detail。

本模块是叶子模块，被周期配置 Service、cron 校验和 API 错误映射共同引用，自己
不 import 项目内任何模块。与 ``UserAdminDomainError`` 同一模式：异常携带的是**预写的
安全文案**，不是 ``str(error)``，因此把这些异常映射进 HTTP 响应不会泄露内部细节。
"""

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


__all__ = [
    "ScheduledJobDomainError",
    "ScheduledJobInvalidCronError",
    "ScheduledJobInvalidParamsError",
    "ScheduledJobKeyConflictError",
    "ScheduledJobNotFoundError",
    "ScheduledJobUnknownTypeError",
]
