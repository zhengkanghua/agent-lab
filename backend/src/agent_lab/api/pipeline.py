"""手动 Pipeline 只持久受理，最终业务统计通过任务执行编号查询。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from agent_lab.api.dependencies import get_task_service
from agent_lab.api.task_routes import IdempotencyKey, TaskRoute
from agent_lab.auth.dependencies import current_superuser
from agent_lab.models.user import UserRecord
from agent_lab.schemas.pipeline import PipelineRunOnceRequest
from agent_lab.schemas.scheduled_jobs import ScheduledJobErrorResponse
from agent_lab.schemas.tasks import TaskAcceptedResponse
from agent_lab.tasks.service import TaskService

router = APIRouter(prefix="/pipeline", tags=["pipeline"], route_class=TaskRoute)


@router.post("/run-once", response_model=TaskAcceptedResponse, status_code=202,
             responses={409: {"model": ScheduledJobErrorResponse}, 503: {"model": ScheduledJobErrorResponse}})
async def run_pipeline_once(body: PipelineRunOnceRequest, request_key: IdempotencyKey,
                            service: Annotated[TaskService, Depends(get_task_service)],
                            actor: Annotated[UserRecord, Depends(current_superuser)]):
    """受理一次同步与文档处理批次，结果从 GET /task-runs/{run_id} 查询。"""
    return await service.submit("pipeline_run_once", body.model_dump(mode="json"),
        actor=f"account:{actor.id}", operation="pipeline:run-once", request_key=request_key,
        content=body.model_dump(mode="json", exclude_unset=True))
