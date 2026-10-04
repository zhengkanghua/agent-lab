"""上游渠道配置的超级用户管理 HTTP 边界。

只有超级用户能读写（与知识库、来源、定时任务同一道门，不新开一档权限，见 spec 0002 的
「实现决策」）。四条路由：列出、读取、新增、修改；**没有删除路由**——本表只提供停用。

请求体里有凭据明文，所以路由挂 ``SanitizedValidationRoute``：校验失败一律换成固定的
``invalid_request``，不回显原始正文。响应 schema 本身没有凭据字段，构造视图时逐字段写出来
（而不是 ``model_validate(record)``），这样将来给表加一列敏感字段也不会被自动带出去。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.api.error_contract import (
    SanitizedValidationRoute,
    build_error_response,
    build_llm_catalog_error_response,
)
from agent_lab.db.session import get_db_session
from agent_lab.schemas.llm_providers import (
    LlmProviderCreateRequest,
    LlmProviderErrorResponse,
    LlmProviderResponse,
    LlmProviderUpdateRequest,
)
from agent_lab.services.llm_credential_cipher import LlmCredentialKeyUnavailableError
from agent_lab.services.llm_model_errors import LlmModelDomainError
from agent_lab.services.llm_provider_errors import LlmProviderDomainError
from agent_lab.services.llm_provider_service import LlmProviderService


class LlmProviderRoute(SanitizedValidationRoute):
    """把领域错误、数据库故障与「主密钥不可用」统一翻成脱敏响应。

    做在 route class 而不是每个 handler 里 try/except：四个 handler 的失败集合完全一样，
    写在四处只会漏掉一处。继承 ``SanitizedValidationRoute`` 是必须的——请求体带凭据明文。

    密钥不可用只会在真的需要加密时才冒出来（见 ``LlmProviderService._credential_cipher``），
    所以它是一条请求级失败，不是启动失败，在这里映射成 503 就够。
    """

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handled(request):
            try:
                return await handler(request)
            except (LlmProviderDomainError, LlmModelDomainError) as error:
                # 模型目录的领域错误也能从这条路由冒出来：启停渠道会碰到「目录里恰好有一个默认」
                # 这条不变量，而它的守卫在 LlmModelService（两边共用一个映射就够，两个类都有
                # ``code``/``detail``/``status_code`` 三个类属性）。
                return _domain_error(error)
            except (SQLAlchemyError, LlmCredentialKeyUnavailableError) as error:
                return build_llm_catalog_error_response(error)

        return handled


router = APIRouter(
    prefix="/llm-providers",
    tags=["llm-catalog"],
    route_class=LlmProviderRoute,
    responses={
        404: {"model": LlmProviderErrorResponse},
        422: {"model": LlmProviderErrorResponse},
        503: {"model": LlmProviderErrorResponse},
    },
)


def get_llm_provider_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LlmProviderService:
    """用当前请求的数据库 Session 构造上游渠道 Service。"""

    return LlmProviderService(session)


Service = Annotated[LlmProviderService, Depends(get_llm_provider_service)]


@router.get("", response_model=list[LlmProviderResponse], summary="列出上游渠道")
async def list_providers(service: Service):
    """返回全部渠道配置（含已停用的）；凭据只以「已配置 / 未配置」出现。"""

    return [_provider_response(record) for record in await service.list_providers()]


@router.post("", response_model=LlmProviderResponse, status_code=201, summary="新增上游渠道")
async def create_provider(body: LlmProviderCreateRequest, service: Service):
    """新增一条渠道；凭据明文只出现在本次请求里，加密后落库。"""

    return _provider_response(await service.create_provider(body))


@router.get("/{provider_id}", response_model=LlmProviderResponse, summary="读取上游渠道")
async def get_provider(provider_id: UUID, service: Service):
    """读取一条渠道；不返回凭据。"""

    return _provider_response(await service.get_provider(provider_id))


@router.patch("/{provider_id}", response_model=LlmProviderResponse, summary="修改上游渠道")
async def update_provider(provider_id: UUID, body: LlmProviderUpdateRequest, service: Service):
    """修改名称、接入类型、地址、启用位或凭据；凭据留空表示不改动。"""

    return _provider_response(await service.update_provider(provider_id, body))


def _provider_response(record) -> LlmProviderResponse:
    """把 ORM 行翻成公开视图：凭据字段在这里被换成一个布尔，密文与明文都不出现。"""

    return LlmProviderResponse(
        id=record.id,
        name=record.name,
        provider=record.provider,
        base_url=record.base_url,
        enabled=record.enabled,
        credential_configured=record.credential_ciphertext is not None,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _domain_error(error: LlmProviderDomainError | LlmModelDomainError) -> JSONResponse:
    """把领域错误的稳定 code 与预写 detail 翻成统一响应结构。

    读的是异常自带的 ``code`` / ``detail`` / ``status_code``，不是 ``str(error)``，所以不会
    把凭据或数据库文本带进响应。
    """

    return build_error_response(
        error.status_code,
        error.code,
        error.detail,
        retryable=False,
    )
