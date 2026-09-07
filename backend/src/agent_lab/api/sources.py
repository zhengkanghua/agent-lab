"""超级用户 Source 发现与 KnowledgeBase 绑定管理接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.api.error_contract import SanitizedValidationRoute, build_error_response
from agent_lab.knowledge.composition import build_source_binding_service
from agent_lab.schemas.sources import SourceErrorResponse, SourceKnowledgeBaseBindingRequest, SourceResponse
from agent_lab.services.source_binding_service import SourceBindingError, SourceBindingService


router = APIRouter(prefix="/sources", tags=["sources"], route_class=SanitizedValidationRoute)


def get_source_binding_service() -> SourceBindingService:
    """取得共享应用用例，事务和写协调由组件自身保证。"""

    return build_source_binding_service()


def _source_error(error: SourceBindingError) -> JSONResponse:
    code_status = {
        "source_not_found": status.HTTP_404_NOT_FOUND,
        "source_binding_conflict": status.HTTP_409_CONFLICT,
        "source_write_recovery_required": status.HTTP_409_CONFLICT,
        "source_database_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    }
    return build_error_response(code_status[error.code], error.code, error.detail, retryable=error.code == "source_database_unavailable")


@router.get("", response_model=list[SourceResponse], responses={503: {"model": SourceErrorResponse}}, summary="列出外部来源")
async def list_sources(service: Annotated[SourceBindingService, Depends(get_source_binding_service)]) -> list[SourceResponse] | JSONResponse:
    try:
        return [SourceResponse.model_validate(item) for item in await service.list_sources()]
    except SourceBindingError as error:
        return _source_error(error)


@router.patch(
    "/{source_id}/knowledge-base",
    response_model=SourceResponse,
    responses={404: {"model": SourceErrorResponse}, 409: {"model": SourceErrorResponse}, 422: {"model": SourceErrorResponse}, 503: {"model": SourceErrorResponse}},
    summary="修改来源 KnowledgeBase 绑定",
)
async def bind_source(
    source_id: UUID,
    body: SourceKnowledgeBaseBindingRequest,
    service: Annotated[SourceBindingService, Depends(get_source_binding_service)],
) -> SourceResponse | JSONResponse:
    try:
        return SourceResponse.model_validate(await service.bind(source_id, body.knowledge_base_id))
    except SourceBindingError as error:
        return _source_error(error)
    except SQLAlchemyError:
        return build_error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "source_database_unavailable", "来源存储当前不可用。", retryable=True)
