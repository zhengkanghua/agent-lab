"""任务 HTTP 边界的请求身份与脱敏错误；不把数据库或上游异常文本带给浏览器。"""

from typing import Annotated

from fastapi import Header
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.api.error_contract import SanitizedValidationRoute, build_scheduled_job_error_response
from agent_lab.api.dependencies import SchedulerRuntimeUnavailableError
from agent_lab.services.scheduled_task_errors import ScheduledJobDomainError
from agent_lab.tasks.contracts import TaskError

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class TaskRoute(SanitizedValidationRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def handled(request):
            try:
                return await handler(request)
            except TaskError as error:
                return JSONResponse(status_code=error.status_code, content={
                    "code": error.code, "detail": error.detail, "retryable": False,
                    "run_id": str(error.run_id) if error.run_id else None,
                })
            except ScheduledJobDomainError as error:
                code = 404 if error.code == "scheduled_job_not_found" else 409 if error.code == "scheduled_job_key_conflict" else 422
                return JSONResponse(status_code=code, content={"code": error.code, "detail": error.detail, "retryable": False})
            except (SQLAlchemyError, SchedulerRuntimeUnavailableError) as error:
                return build_scheduled_job_error_response(error)
        return handled
