"""普通函数注册：类型校验、执行、资源准备与恢复判断均由业务显式提供。"""

from collections.abc import Awaitable, Callable, Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agent_lab.services.scheduled_task_errors import ScheduledJobUnknownTypeError
from agent_lab.tasks.contracts import FailureDecision


@dataclass(frozen=True)
class TaskTypeSpec:
    task_type: str
    description: str
    params_model: type[BaseModel]
    execute: Callable[[Any, dict], Awaitable[dict]]
    version: int = 1
    runtime_factory: Callable[[], Any] = lambda: None
    execution_scope: Callable = lambda runtime, params: nullcontext()
    classify_error: Callable[[Exception], FailureDecision] = lambda error: FailureDecision()
    recover: Callable | None = None
    on_success: Callable | None = None
    discard_preparation: Callable | None = None
    concurrency_key: str | None = None
    schedulable: bool = True

    def validate_params(self, raw: Any) -> dict:
        return self.params_model.model_validate(raw if raw is not None else {}).model_dump(mode="json")


class TaskRegistry:
    def __init__(self, specs: Iterable[TaskTypeSpec]):
        self._specs = {}
        for spec in specs:
            if spec.task_type in self._specs:
                raise ValueError("任务类型重复注册。")
            self._specs[spec.task_type] = spec

    def require(self, task_type: str, version: int | None = None) -> TaskTypeSpec:
        spec = self._specs.get(task_type)
        if spec is None or version is not None and spec.version != version:
            raise ScheduledJobUnknownTypeError()
        return spec

    def values(self):
        return self._specs.values()

    def get(self, task_type: str) -> TaskTypeSpec | None:
        return self._specs.get(task_type)
