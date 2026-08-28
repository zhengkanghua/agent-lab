"""把只读向量搜索暴露为 FastAPI HTTP 接口（POST /vector-search）。

本模块只做两件「薄」事：
1. 定义 OpenAPI 请求/响应声明（请求体模型来自 schemas，错误模型来自错误契约层）；
2. 捕获已分类的 Ollama/Qdrant 上游异常并交给共享的错误契约层映射成 502/503/504。

它自己不生成 query 向量、不构造 Qdrant 过滤条件、不访问 PostgreSQL，也不创建/切换/
写入 Qdrant——真正的 I/O 在 VectorSearchService（Ollama）和 QdrantVectorSearch 里。
依赖注入在 ``agent_lab.api.dependencies``，异常映射表在 ``agent_lab.api.error_contract``；
本模块是纯特性路由，不再充当兄弟路由的基础设施来源。本接口不实现 LLM 或 RAG。
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
from agent_lab.schemas.vector_search import (
    VectorSearchRequest,
    VectorSearchResult,
)
from agent_lab.services.vector_search_service import VectorSearchService


logger = logging.getLogger(__name__)
router = APIRouter(tags=["vector-search"])


@router.post(
    "/vector-search",
    response_model=list[VectorSearchResult],
    status_code=status.HTTP_200_OK,
    summary="按语义相似度搜索新闻 Chunk",
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
async def vector_search(
    search_request: VectorSearchRequest,
    service: Annotated[VectorSearchService, Depends(get_vector_search_service)],
) -> list[VectorSearchResult] | JSONResponse:
    """执行一次只读搜索：query 向量化 → Qdrant current Alias 查最相似的新闻 Chunk。

    搜索链路：
    1. service.search() 先把 query 交给 Ollama 的 bge-m3 模型转成 1024 维向量；
    2. 再用向量查 Qdrant 的 current Alias——一个不存数据的「指针」，指向真正保存
       数据的物理 Collection（news_chunks_langchain_v1_001），部署时统一切换。

    Args:
        search_request: HTTP JSON body 解析出的 query、Top-K、可选 threshold 和 filters。
        service: lifespan 共享的应用层 Search Service，由 FastAPI dependency 注入。

    Returns:
        成功时返回保持 Qdrant score 顺序的 Chunk 结果数组；已分类的上游失败返回稳定
        ``VectorSearchErrorResponse`` JSON，不回显请求文本或第三方异常内容。

    Raises:
        Exception: 未知编程错误或未分类异常原样传播，由 FastAPI 作为 500 处理；接口
            不用空结果掩盖异常。

    Notes:
        本方法不执行 PostgreSQL I/O。Service 会执行一次 Ollama query Embedding 和一次
        Qdrant current Alias 只读查询；不执行 upsert/delete/lifecycle/状态写入或自动重试。
        Runtime 缺失由依赖在进入本方法前抛出，两条搜索路由共用应用级 handler 的 503。
    """

    try:
        # 核心就一步：把请求交给共享 Service（内部做 query 向量化 + Qdrant 查询）
        return await service.search(search_request)
    except SEARCH_UPSTREAM_EXCEPTIONS as exc:
        # 已知的上游失败：只记录稳定异常类型（不调 str(exc)，避免敏感内容进日志），
        # 然后交给共享错误表映射成脱敏的 502/503/504 响应
        logger.warning("向量检索上游故障：%s", type(exc).__name__)
        return build_vector_search_error_response(exc)


# VectorSearchErrorResponse 既用于上面的 OpenAPI responses 声明，也继续从本模块导出，
# 供重构前按 ``agent_lab.api.vector_search`` 引用它的调用方使用；权威定义在 error_contract。
__all__ = ["VectorSearchErrorResponse", "router"]
