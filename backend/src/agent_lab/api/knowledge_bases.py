"""KnowledgeBase 配置 HTTP 边界；读启用库需登录，管理配置需超级用户。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from agent_lab.api.error_contract import SanitizedValidationRoute, build_error_response
from agent_lab.auth.dependencies import current_active_user, current_superuser
from agent_lab.knowledge.application import KnowledgeBaseService
from agent_lab.knowledge.composition import build_knowledge_base_service
from agent_lab.knowledge.contracts import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
)
from agent_lab.models.user import UserRecord
from agent_lab.schemas.knowledge_base import KnowledgeBaseErrorResponse


router = APIRouter(
    prefix="/knowledge-bases",
    tags=["knowledge-bases"],
    route_class=SanitizedValidationRoute,
)


def get_knowledge_base_service() -> KnowledgeBaseService:
    """独立依赖便于 HTTP 测试替换，用例自行按需打开短事务。"""

    return build_knowledge_base_service()


@router.get(
    "",
    response_model=list[KnowledgeBaseResponse],
    responses={403: {"model": KnowledgeBaseErrorResponse}, 503: {"model": KnowledgeBaseErrorResponse}},
    summary="列出知识库",
)
async def list_knowledge_bases(
    user: Annotated[UserRecord, Depends(current_active_user)],
    service: Annotated[KnowledgeBaseService, Depends(get_knowledge_base_service)],
    include_inactive: bool = False,
) -> list[KnowledgeBaseResponse] | JSONResponse:
    """默认返回启用库，超级用户可用 include_inactive 读取全部配置。"""

    if include_inactive and not user.is_superuser:
        return build_error_response(
            status.HTTP_403_FORBIDDEN,
            "knowledge_base_forbidden",
            detail="只有超级用户可以读取停用的知识库配置。",
            retryable=False,
        )
    return [
        KnowledgeBaseResponse.model_validate(item)
        for item in await service.list(include_inactive=include_inactive)
    ]


@router.post(
    "",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(current_superuser)],
    responses={409: {"model": KnowledgeBaseErrorResponse}, 422: {"model": KnowledgeBaseErrorResponse}, 503: {"model": KnowledgeBaseErrorResponse}},
    summary="创建知识库",
)
async def create_knowledge_base(
    body: KnowledgeBaseCreateRequest,
    service: Annotated[KnowledgeBaseService, Depends(get_knowledge_base_service)],
) -> KnowledgeBaseResponse:
    """创建具有唯一稳定键的逻辑知识库配置。"""

    return KnowledgeBaseResponse.model_validate(await service.create(body))


@router.patch(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseResponse,
    dependencies=[Depends(current_superuser)],
    responses={404: {"model": KnowledgeBaseErrorResponse}, 422: {"model": KnowledgeBaseErrorResponse}, 503: {"model": KnowledgeBaseErrorResponse}},
    summary="修改知识库配置",
)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    body: KnowledgeBaseUpdateRequest,
    service: Annotated[KnowledgeBaseService, Depends(get_knowledge_base_service)],
) -> KnowledgeBaseResponse:
    """修改名称、说明或启停状态；稳定键不可修改，不提供物理删除。"""

    return KnowledgeBaseResponse.model_validate(await service.update(knowledge_base_id, body))
