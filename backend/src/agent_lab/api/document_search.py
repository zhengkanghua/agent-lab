"""把按新闻文档分组的只读语义搜索暴露为 ``POST /document-search``。

本模块位于 FastAPI 边界层，只负责请求依赖注入、OpenAPI 响应声明和已知上游异常
映射；它不在路由中对 Chunk 去重、不访问 PostgreSQL，也不拼装 Qdrant Filter。分组和
排序由 ``VectorSearchService`` 与 Qdrant grouped query 负责。

它与 ``vector_search`` 是平级的特性路由：两者都依赖 ``agent_lab.api.dependencies``
提供的注入函数和 ``agent_lab.api.error_contract`` 的共享错误表，彼此不互相 import。
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from agent_lab.api.dependencies import get_vector_search_service
from agent_lab.api.error_contract import (
    SEARCH_UPSTREAM_EXCEPTIONS,
    VectorSearchErrorResponse,
    build_vector_search_error_response,
)
from agent_lab.schemas.document_search import (
    DocumentSearchRequest,
    DocumentSearchResult,
)
from agent_lab.services.vector_search_service import VectorSearchService


logger = logging.getLogger(__name__)
router = APIRouter(tags=["document-search"])


@router.post(
    "/document-search",
    response_model=list[DocumentSearchResult],
    status_code=status.HTTP_200_OK,
    summary="按新闻文档分组搜索相关片段",
    description=(
        "执行一次只读 grouped vector search。document_limit 控制不同新闻数量，"
        "matches_per_document 控制每篇新闻返回的相关 Chunk 数；完整正文需另行请求"
        "GET /documents/{document_id}。"
    ),
    responses={
        status.HTTP_502_BAD_GATEWAY: {
            "model": VectorSearchErrorResponse,
            "description": "Embedding 或 Qdrant 上游响应失败。",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": VectorSearchErrorResponse,
            "description": "搜索上游连接、目标或配置暂不可用。",
        },
        status.HTTP_504_GATEWAY_TIMEOUT: {
            "model": VectorSearchErrorResponse,
            "description": "Embedding 或 Qdrant 上游请求超时。",
        },
    },
)
async def document_search(
    search_request: DocumentSearchRequest,
    service: Annotated[VectorSearchService, Depends(get_vector_search_service)],
) -> list[DocumentSearchResult] | JSONResponse:
    """执行 query Embedding → Qdrant grouped query，并返回文档级结果。

    捕获范围与 ``POST /vector-search`` 完全一致（同一个 ``SEARCH_UPSTREAM_EXCEPTIONS``
    元组和同一张错误表）；Runtime 缺失由依赖在进入本方法前抛出，两条路由共用应用级
    handler 的同一个 503 响应。
    """

    try:
        return await service.search_documents(search_request)
    except SEARCH_UPSTREAM_EXCEPTIONS as exc:
        # 只记录稳定异常类型；错误映射函数不会读取第三方异常文本。
        logger.warning("文档检索上游故障：%s", type(exc).__name__)
        return build_vector_search_error_response(exc)


__all__ = ["router"]
