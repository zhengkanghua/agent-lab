"""提供仅超级用户可访问的定时任务管理 HTTP API。

本层校验 OpenAPI 输入、调用 ScheduledJobService，并把领域错误或数据库故障转换成稳定
脱敏响应。写操作（创建/修改/删除/触发）成功后由 Service 同步到运行中的调度器；本层
不直接接触 APScheduler。``params`` 是任意 JSON，因此和账号管理一样挂
``SanitizedValidationRoute``，请求校验失败统一换成固定 ``invalid_request``，不回显原始输入。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.api.dependencies import get_scheduler_runner
from agent_lab.api.error_contract import (
    SanitizedValidationRoute,
    build_error_response,
    build_scheduled_job_error_response,
)
from agent_lab.db.session import get_db_session
from agent_lab.schemas.scheduled_jobs import (
    CronValidateRequest,
    CronValidateResponse,
    JobRunResponse,
    ScheduledJobCreateRequest,
    ScheduledJobErrorResponse,
    ScheduledJobResponse,
    ScheduledJobTriggerResponse,
    ScheduledJobUpdateRequest,
)
from agent_lab.services.scheduler_runner import ScheduledJobRunner
from agent_lab.services.scheduled_job_service import ScheduledJobService, ScheduledJobView
from agent_lab.services.scheduled_task_errors import ScheduledJobDomainError


router = APIRouter(
    prefix="/scheduled-jobs",
    tags=["scheduler"],
    route_class=SanitizedValidationRoute,
)


def get_scheduled_job_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ScheduledJobService:
    """用请求数据库 Session 与进程级调度器构造定时任务管理 Service。"""

    runner: ScheduledJobRunner = get_scheduler_runner(request)
    return ScheduledJobService(session, runner)


@router.get(
    "",
    response_model=list[ScheduledJobResponse],
    responses={503: {"model": ScheduledJobErrorResponse}},
    summary="列出全部定时任务",
    description=(
        "返回任务配置、下次计划执行时间（UTC；调度器未启动或任务停用为空）与最近一次"
        "执行摘要。列表不含任何正文、凭据或异常文本。"
    ),
)
async def list_jobs(
    service: Annotated[ScheduledJobService, Depends(get_scheduled_job_service)],
) -> list[ScheduledJobResponse] | JSONResponse:
    """返回任务列表；只有数据库故障一种失败路径。"""

    try:
        views = await service.list_jobs()
    except SQLAlchemyError as error:
        return _database_error(error)
    return [_job_response(view) for view in views]


@router.post(
    "",
    response_model=ScheduledJobResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": ScheduledJobErrorResponse},
        422: {"model": ScheduledJobErrorResponse},
        503: {"model": ScheduledJobErrorResponse},
    },
    summary="创建定时任务",
    description=(
        "校验任务类型、cron 与参数后创建任务；创建成功即按 enabled 状态注册进调度器。"
        "key 与已存在任务重复返回 409；类型、cron 或参数不合法返回 422。"
    ),
)
async def create_job(
    body: ScheduledJobCreateRequest,
    service: Annotated[ScheduledJobService, Depends(get_scheduled_job_service)],
) -> ScheduledJobResponse | JSONResponse:
    """创建任务并注册进调度器。"""

    try:
        view = await service.create_job(
            key=body.key,
            task_type=body.task_type,
            cron_expr=body.cron_expr,
            params=body.params,
            enabled=body.enabled,
        )
    except ScheduledJobDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError as error:
        return _database_error(error)
    return _job_response(view)


@router.post(
    "/validate-cron",
    response_model=CronValidateResponse,
    responses={422: {"model": ScheduledJobErrorResponse}},
    summary="校验 cron 表达式并预览未来执行时间",
    description=(
        "按服务端解释时区（SCHEDULER_TIMEZONE，默认上海）解析 5 段式 cron，"
        "返回未来 3 次执行的 UTC 时刻与本地展示时刻；解析失败返回 422。"
    ),
)
async def validate_cron(
    body: CronValidateRequest,
    service: Annotated[ScheduledJobService, Depends(get_scheduled_job_service)],
) -> CronValidateResponse | JSONResponse:
    """校验 cron 并计算未来 3 次执行时间，供提交前预览。"""

    try:
        utc_times, local_times = service.validate_cron(body.cron_expr)
    except ScheduledJobDomainError as error:
        return _domain_error(error)
    return CronValidateResponse(
        next_run_times=utc_times,
        next_run_times_local=local_times,
    )


@router.get(
    "/{job_id}",
    response_model=ScheduledJobResponse,
    responses={
        404: {"model": ScheduledJobErrorResponse},
        503: {"model": ScheduledJobErrorResponse},
    },
    summary="查询单个定时任务",
)
async def get_job(
    job_id: UUID,
    service: Annotated[ScheduledJobService, Depends(get_scheduled_job_service)],
) -> ScheduledJobResponse | JSONResponse:
    """返回单个任务的完整视图。"""

    try:
        view = await service.get_job(job_id)
    except ScheduledJobDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError as error:
        return _database_error(error)
    return _job_response(view)


@router.patch(
    "/{job_id}",
    response_model=ScheduledJobResponse,
    responses={
        404: {"model": ScheduledJobErrorResponse},
        422: {"model": ScheduledJobErrorResponse},
        503: {"model": ScheduledJobErrorResponse},
    },
    summary="修改定时任务的 cron、参数或启停状态",
    description=(
        "只修改请求中出现的字段；key 与任务类型不可修改。修改成功后调度器立即生效，"
        "无需重启服务。"
    ),
)
async def update_job(
    job_id: UUID,
    body: ScheduledJobUpdateRequest,
    service: Annotated[ScheduledJobService, Depends(get_scheduled_job_service)],
) -> ScheduledJobResponse | JSONResponse:
    """按字段增量修改任务配置。"""

    try:
        view = await service.update_job(
            job_id,
            cron_expr=body.cron_expr,
            params=body.params,
            enabled=body.enabled,
        )
    except ScheduledJobDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError as error:
        return _database_error(error)
    return _job_response(view)


@router.delete(
    "/{job_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ScheduledJobErrorResponse},
        503: {"model": ScheduledJobErrorResponse},
    },
    summary="删除定时任务",
    description="删除任务配置；执行历史随数据库级联删除，调度器条目同步摘除。",
)
async def delete_job(
    job_id: UUID,
    service: Annotated[ScheduledJobService, Depends(get_scheduled_job_service)],
) -> JSONResponse | None:
    """删除任务；成功返回 204 无内容。"""

    try:
        await service.delete_job(job_id)
    except ScheduledJobDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError as error:
        return _database_error(error)
    return None


@router.post(
    "/{job_id}/trigger",
    response_model=ScheduledJobTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": ScheduledJobErrorResponse},
        409: {"model": ScheduledJobErrorResponse},
        503: {"model": ScheduledJobErrorResponse},
    },
    summary="手动立即执行一次定时任务",
    description=(
        "在后台按任务当前配置执行一轮（与 cron 到点同一执行包装器），立即返回受理回执；"
        "上一轮尚未结束时返回 409，不排队。执行结果通过执行历史查询。"
    ),
)
async def trigger_job(
    job_id: UUID,
    service: Annotated[ScheduledJobService, Depends(get_scheduled_job_service)],
) -> ScheduledJobTriggerResponse | JSONResponse:
    """手动触发一次执行；受理后执行在后台进行。"""

    try:
        run_id = await service.trigger(job_id)
    except ScheduledJobDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError as error:
        return _database_error(error)
    return ScheduledJobTriggerResponse(
        job_id=job_id,
        run_id=run_id,
        status="running",
    )


@router.get(
    "/{job_id}/runs",
    response_model=list[JobRunResponse],
    responses={
        404: {"model": ScheduledJobErrorResponse},
        503: {"model": ScheduledJobErrorResponse},
    },
    summary="查询定时任务的执行历史",
    description="按开始时间新→旧返回执行记录（含被跳过的记录），默认 20 条、上限 100 条。",
)
async def list_job_runs(
    job_id: UUID,
    service: Annotated[ScheduledJobService, Depends(get_scheduled_job_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[JobRunResponse] | JSONResponse:
    """返回任务的执行历史列表。"""

    try:
        runs = await service.list_runs(job_id, limit=limit)
    except ScheduledJobDomainError as error:
        return _domain_error(error)
    except SQLAlchemyError as error:
        return _database_error(error)
    return [JobRunResponse.model_validate(run) for run in runs]


def _job_response(view: ScheduledJobView) -> ScheduledJobResponse:
    """把 Service 视图转换成对外响应模型。"""

    return ScheduledJobResponse(
        id=view.record.id,
        key=view.record.key,
        task_type=view.record.task_type,
        cron_expr=view.record.cron_expr,
        params=view.record.params,
        enabled=view.record.enabled,
        next_run_at=view.next_run_at,
        last_run=(
            JobRunResponse.model_validate(view.last_run)
            if view.last_run is not None
            else None
        ),
        created_at=view.record.created_at,
        updated_at=view.record.updated_at,
    )


def _domain_error(error: ScheduledJobDomainError) -> JSONResponse:
    """把定时任务领域错误的稳定 code 映射成 HTTP 状态码并复用统一响应结构。

    与账号管理同一模式：读取的是领域层预写的安全字段，不是 ``str(error)``；同一个类
    可能携带不同 code，所以不进共享错误表。新增 code 时记得往这张表里加一行。
    """

    status_code = {
        "scheduled_job_not_found": status.HTTP_404_NOT_FOUND,
        "scheduled_job_key_conflict": status.HTTP_409_CONFLICT,
        "scheduled_job_already_running": status.HTTP_409_CONFLICT,
        "scheduled_job_invalid_cron": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "scheduled_job_invalid_params": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "scheduled_job_unknown_type": status.HTTP_422_UNPROCESSABLE_CONTENT,
    }.get(error.code, status.HTTP_409_CONFLICT)
    return build_error_response(
        status_code,
        error.code,
        error.detail,
        retryable=False,
    )


def _database_error(error: SQLAlchemyError) -> JSONResponse:
    """把数据库故障交给共享错误表映射成稳定 503（只读异常类型）。"""

    return build_scheduled_job_error_response(error)


__all__ = [
    "get_scheduled_job_service",
    "router",
]
