"""可用模型的管理与选择 HTTP 边界。

权限门**刻意不整组挂**（与 ``/llm-providers`` 那组不同）：本组里有一条「任何已登录账号都能
读」的模型选择列表，``include_router`` 级那道门会连它一起挡掉。所以照 ``api/knowledge_bases.py``
的做法，在每条管理路由上单独挂 ``dependencies=[Depends(current_superuser)]``——哪条路由要哪一档
权限，在路由自己身上看得见。

三条管理路由：列出、新增、修改（含启停与设默认）；选择列表一条。**没有删除路由**——本表只提供
停用，与渠道、知识库一致，而且停用当前默认模型、停用它所属的渠道都会被拒（见
``LlmModelService`` 与 ``LlmProviderService``）。

请求体里没有凭据，仍然沿用 ``SanitizedValidationRoute``：模型目录这一族对外只给一种校验失败
形状（稳定的 ``invalid_request``），前端不必为「哪条路由回哪套 422 结构」分支。代价是 422 不
逐字段说明——填错字段的那几句话由表单自己说。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.api.error_contract import (
    SanitizedValidationRoute,
    build_llm_catalog_error_response,
    build_llm_model_error_response,
)
from agent_lab.auth.dependencies import current_active_user, current_superuser
from agent_lab.db.session import get_db_session
from agent_lab.schemas.llm_models import (
    AvailableLlmModelResponse,
    LlmModelCreateRequest,
    LlmModelErrorResponse,
    LlmModelResponse,
    LlmModelUpdateRequest,
)
from agent_lab.services.llm_model_errors import LlmModelDomainError
from agent_lab.services.llm_model_service import LlmModelService, ModelView
from agent_lab.services.llm_provider_errors import LlmProviderDomainError


class LlmModelRoute(SanitizedValidationRoute):
    """把领域错误与数据库故障统一翻成脱敏响应。

    做在 route class 而不是每个 handler 里 try/except：四个 handler 的失败集合完全一样，
    写在四处只会漏掉一处。捕获两类领域错误是因为「所属渠道不存在」属于渠道那一族
    （``LlmProviderNotFoundError``）：同一条失败不该有两个 code，而它本来就是渠道不存在。
    """

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handled(request):
            try:
                return await handler(request)
            except (LlmModelDomainError, LlmProviderDomainError) as error:
                return _domain_error(error)
            except SQLAlchemyError as error:
                return build_llm_catalog_error_response(error)

        return handled


router = APIRouter(
    prefix="/llm-models",
    tags=["llm-catalog"],
    route_class=LlmModelRoute,
    responses={
        404: {"model": LlmModelErrorResponse},
        409: {"model": LlmModelErrorResponse},
        422: {"model": LlmModelErrorResponse},
        503: {"model": LlmModelErrorResponse},
    },
)


def get_llm_model_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LlmModelService:
    """用当前请求的数据库 Session 构造可用模型 Service。"""

    return LlmModelService(session)


Service = Annotated[LlmModelService, Depends(get_llm_model_service)]


@router.get(
    "/available",
    response_model=list[AvailableLlmModelResponse],
    dependencies=[Depends(current_active_user)],
    summary="列出可选的模型",
)
async def list_available_models(service: Service):
    """列出「自身启用且所属渠道也启用」的模型，供会话里的模型选择器使用。

    任何已登录账号都能读（它是用户挑模型用的，不是管理动作）。停用一条渠道之后它下面的模型
    自动从这里消失，再把渠道启用回来它们又自动回来——可用性是每次查询现算的。
    """

    return [_available_response(view) for view in await service.list_available_models()]


@router.get(
    "",
    response_model=list[LlmModelResponse],
    dependencies=[Depends(current_superuser)],
    summary="列出可用模型",
)
async def list_models(service: Service):
    """列出全部可用模型（含已停用的），带所属渠道的展示名与启用位。

    含停用的与所属渠道的状态一起给，管理员才看得出「这条模型自己开着、却选不到」是渠道停了。
    """

    return [_model_response(view) for view in await service.list_models()]


@router.post(
    "",
    response_model=LlmModelResponse,
    status_code=201,
    dependencies=[Depends(current_superuser)],
    summary="新增可用模型",
)
async def create_model(body: LlmModelCreateRequest, service: Service):
    """在一个上游渠道下面新增一条可用模型；目录里还没有默认时它可能自动成为默认。"""

    return _model_response(await service.create_model(body))


@router.patch(
    "/{model_id}",
    response_model=LlmModelResponse,
    dependencies=[Depends(current_superuser)],
    summary="修改可用模型",
)
async def update_model(model_id: UUID, body: LlmModelUpdateRequest, service: Service):
    """修改上游模型名、展示名、上下文窗口、所属渠道、启用位或默认标记。

    改挂渠道要求目标渠道处于启用状态；停用当前默认模型、把它所属的渠道停用、以及直接取消它的
    默认标记都会被拒——不变量的七条规则见 ``LlmModelService`` 的模块说明。
    """

    return _model_response(await service.update_model(model_id, body))


def _model_response(view: ModelView) -> LlmModelResponse:
    """把模型行与所属渠道的两个字段翻成后台管理视图（展示名保持原样，空就是空）。"""

    record = view.model
    return LlmModelResponse(
        id=record.id,
        provider_id=record.provider_id,
        provider_name=view.provider_name,
        provider_enabled=view.provider_enabled,
        upstream_model_name=record.upstream_model_name,
        display_name=record.display_name,
        context_window=record.context_window,
        is_default=record.is_default,
        enabled=record.enabled,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _available_response(view: ModelView) -> AvailableLlmModelResponse:
    """把模型行翻成选择列表视图；展示名留空时这里就给上游模型名。

    回落放在服务端：`display_name or upstream_model_name` 这条规则只写一次，选择器拿到什么就
    显示什么，不会各处再判一次空。
    """

    record = view.model
    return AvailableLlmModelResponse(
        id=record.id,
        display_name=record.display_name or record.upstream_model_name,
        context_window=record.context_window,
        provider_name=view.provider_name,
    )


def _domain_error(error) -> JSONResponse:
    """把领域错误的稳定 code 与预写 detail 翻成统一响应结构。

    读的是异常自带的 ``code`` / ``detail`` / ``status_code``，不是 ``str(error)``，所以不会把
    数据库文本带进响应。映射本体在错误契约层，与 ``POST /agent/chat`` 解析模型失败那一条路
    共用同一个构造器。
    """

    return build_llm_model_error_response(error)
