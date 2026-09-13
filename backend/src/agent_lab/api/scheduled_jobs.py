"""周期配置管理与人工受理；API 不启动调度循环或调用任务业务。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.api.dependencies import get_task_service
from agent_lab.api.task_routes import IdempotencyKey, TaskRoute
from agent_lab.auth.dependencies import current_superuser
from agent_lab.db.session import get_db_session
from agent_lab.models.user import UserRecord
from agent_lab.schemas.scheduled_jobs import (
    CronValidateRequest, CronValidateResponse, JobRunResponse, ScheduledJobCreateRequest,
    ScheduledJobErrorResponse, ScheduledJobResponse, ScheduledJobTriggerResponse,
    ScheduledJobUpdateRequest, ScheduledTaskTypeResponse,
)
from agent_lab.services.scheduled_job_service import ScheduledJobService, ScheduledJobView
from agent_lab.tasks.service import TaskService

router = APIRouter(prefix="/scheduled-jobs", tags=["tasks"], route_class=TaskRoute,
    responses={404: {"model": ScheduledJobErrorResponse}, 409: {"model": ScheduledJobErrorResponse},
               422: {"model": ScheduledJobErrorResponse}, 503: {"model": ScheduledJobErrorResponse}})


def get_scheduled_job_service(request: Request, session: Annotated[AsyncSession, Depends(get_db_session)]):
    return ScheduledJobService(session, request.app.state.task_cron, get_task_service(request).registry)


Service = Annotated[ScheduledJobService, Depends(get_scheduled_job_service)]


@router.get("/task-types", response_model=list[ScheduledTaskTypeResponse])
async def task_types(service: Annotated[TaskService, Depends(get_task_service)]):
    """返回注册的类型、参数默认值及约束，标明可配置周期的类型。"""
    return [ScheduledTaskTypeResponse(task_type=spec.task_type, description=spec.description,
        defaults=spec.params_model.model_construct().model_dump(mode="json"),
        params_schema=spec.params_model.model_json_schema(), schedulable=spec.schedulable,
    ) for spec in service.registry.values()]


@router.get("", response_model=list[ScheduledJobResponse])
async def list_jobs(service: Service):
    """列出周期配置及其最近执行和当前未结束执行。"""
    return [_job_response(view) for view in await service.list_jobs()]


@router.post("", response_model=ScheduledJobResponse, status_code=201)
async def create_job(body: ScheduledJobCreateRequest, service: Service):
    """创建周期配置，之后由 Beat 按 UTC 计划受理。"""
    return _job_response(await service.create_job(**body.model_dump()))


@router.post("/validate-cron", response_model=CronValidateResponse)
async def validate_cron(body: CronValidateRequest, service: Service):
    """按调度时区校验五段式 cron，并预览未来三次执行时刻。"""
    moments, local = service.validate_cron(body.cron_expr)
    return CronValidateResponse(next_run_times=moments, next_run_times_local=local, timezone=service.timezone)


@router.get("/{job_id}", response_model=ScheduledJobResponse)
async def get_job(job_id: UUID, service: Service):
    """读取周期配置。"""
    return _job_response(await service.get_job(job_id))


@router.patch("/{job_id}", response_model=ScheduledJobResponse)
async def update_job(job_id: UUID, body: ScheduledJobUpdateRequest, service: Service):
    """允许启用或执行期间修改配置，新参数只用于之后受理的执行。"""
    return _job_response(await service.update_job(job_id, **body.model_dump()))


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: UUID, service: Service):
    """删除周期配置并停止未来周期，保留已受理执行及历史。"""
    await service.delete_job(job_id)


@router.post("/{job_id}/trigger", response_model=ScheduledJobTriggerResponse, status_code=202)
async def trigger_job(job_id: UUID, request_key: IdempotencyKey,
                      service: Annotated[TaskService, Depends(get_task_service)],
                      actor: Annotated[UserRecord, Depends(current_superuser)]):
    """按当前配置受理一次执行；同请求重发返回原编号，停用配置也允许人工触发。"""
    return await service.trigger(job_id, actor=f"account:{actor.id}", request_key=request_key)


@router.get("/{job_id}/runs", response_model=list[JobRunResponse])
async def list_job_runs(job_id: UUID, service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    """按原配置身份查询历史，配置删除后仍保留执行。"""
    return await service.list_runs(job_id, limit=limit)


@router.get("/{job_id}/runs/{run_id}", response_model=JobRunResponse)
async def get_job_run(job_id: UUID, run_id: UUID, service: Service):
    """按原配置和执行编号读取旧入口的回执。"""
    return await service.get_run(job_id, run_id)


def _job_response(view: ScheduledJobView):
    return ScheduledJobResponse(id=view.record.id, key=view.record.key, task_type=view.record.task_type,
        cron_expr=view.record.cron_expr, params=view.record.params, enabled=view.record.enabled,
        next_run_at=view.next_run_at,
        last_run=JobRunResponse.model_validate(view.last_run) if view.last_run else None,
        active_run=JobRunResponse.model_validate(view.active_run) if view.active_run else None,
        created_at=view.record.created_at, updated_at=view.record.updated_at)
