"""独立于周期配置的任务执行与默认策略管理，统一保持超级用户权限。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from agent_lab.api.dependencies import get_task_service
from agent_lab.api.task_routes import IdempotencyKey, TaskRoute
from agent_lab.auth.dependencies import current_superuser
from agent_lab.models.user import UserRecord
from agent_lab.schemas.scheduled_jobs import ScheduledJobErrorResponse
from agent_lab.schemas.tasks import JobRunResponse, TaskAcceptedResponse, TaskPolicyChangeResponse, TaskSubmitRequest
from agent_lab.tasks.contracts import ExecutionPolicy, RunStatus
from agent_lab.tasks.service import TaskService

Service = Annotated[TaskService, Depends(get_task_service)]
Actor = Annotated[UserRecord, Depends(current_superuser)]
router = APIRouter(prefix="/task-runs", tags=["tasks"], route_class=TaskRoute,
    responses={404: {"model": ScheduledJobErrorResponse}, 409: {"model": ScheduledJobErrorResponse},
               410: {"model": ScheduledJobErrorResponse}, 503: {"model": ScheduledJobErrorResponse}})
policy_router = APIRouter(prefix="/task-policy", tags=["tasks"], route_class=TaskRoute)


@router.get("", response_model=list[JobRunResponse])
async def list_runs(service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                    offset: Annotated[int, Query(ge=0)] = 0, source_job_id: UUID | None = None,
                    status: RunStatus | None = None, task_type: str | None = None):
    """按受理时间从新到旧列出周期和一次性执行，支持状态、类型及原配置筛选。"""
    return await service.list_runs(limit=limit, offset=offset, source_job_id=source_job_id, status=status, task_type=task_type)


@router.post("", response_model=TaskAcceptedResponse, status_code=202)
async def submit(body: TaskSubmitRequest, service: Service, actor: Actor, request_key: IdempotencyKey):
    """持久受理一次性任务，同一请求标识重发返回原编号。"""
    return await service.submit(body.task_type, body.params, actor=f"account:{actor.id}", request_key=request_key,
                                content=body.model_dump(mode="json", exclude_unset=True))


@router.get("/{run_id}", response_model=JobRunResponse)
async def get_run(run_id: UUID, service: Service):
    """仅凭执行编号查询详情，周期配置删除后仍可查询。"""
    return await service.get_run(run_id)


@router.post("/{run_id}/cancel", response_model=JobRunResponse)
async def cancel(run_id: UUID, service: Service):
    """取消尚未开始或等待自动重试的执行，已开始的业务尝试拒绝取消。"""
    return await service.cancel(run_id)


@router.post("/{run_id}/retry", response_model=TaskAcceptedResponse, status_code=202)
async def retry(run_id: UUID, service: Service, actor: Actor, request_key: IdempotencyKey):
    """用原失败参数和当前默认策略新建执行，并保留原失败关联。"""
    return await service.retry(run_id, actor=f"account:{actor.id}", request_key=request_key)


@policy_router.get("", response_model=ExecutionPolicy)
async def get_policy(service: Service):
    """读取之后受理的执行所用默认策略。"""
    return await service.get_policy()


@policy_router.put("", response_model=ExecutionPolicy)
async def update_policy(body: ExecutionPolicy, service: Service, actor: Actor):
    """更新默认重试和历史保留策略并留痕，不影响已受理执行。"""
    return await service.update_policy(body, f"account:{actor.id}")


@policy_router.get("/changes", response_model=list[TaskPolicyChangeResponse])
async def policy_changes(service: Service):
    """列出最近的默认策略修改记录。"""
    return await service.policy_history()
