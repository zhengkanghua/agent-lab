"""把按新闻文档分组的只读语义搜索暴露为 ``POST /document-search``。

本模块位于 FastAPI 边界层，只负责请求依赖注入、OpenAPI 响应声明和已知上游异常
映射；它不在路由中对 Chunk 去重、不访问 PostgreSQL，也不拼装 Qdrant Filter。分组和
排序由 ``VectorSearchService`` 与 Qdrant grouped query 负责。
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from agent_lab.api.vector_search import (
    VectorSearchErrorResponse,
    VectorSearchRuntimeUnavailableError,
    build_vector_search_error_response,
    get_vector_search_service,
)
from agent_lab.pipeline.ollama_embedding_provider import OllamaEmbeddingError
from agent_lab.qdrant.search import QdrantVectorSearchError
from agent_lab.schemas.document_search import (
    DocumentSearchRequest,
    DocumentSearchResult,
)
from agent_lab.services.vector_search_service import (
    QueryVectorValidationError,
    VectorSearchService,
)


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
    """执行 query Embedding → Qdrant grouped query，并返回文档级结果。"""

    try:
        return await service.search_documents(search_request)
    except (
        OllamaEmbeddingError,
        QueryVectorValidationError,
        QdrantVectorSearchError,
        VectorSearchRuntimeUnavailableError,
    ) as exc:
        # 只记录稳定异常类型；错误映射函数不会读取第三方异常文本。
        logger.warning("文档检索上游故障：%s", type(exc).__name__)
        return build_vector_search_error_response(exc)


__all__ = ["router"]
