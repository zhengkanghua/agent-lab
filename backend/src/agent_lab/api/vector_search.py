"""把只读向量搜索暴露为 FastAPI HTTP 接口（POST /vector-search）。

本模块只做三件「薄」事：
1. 依赖注入：从应用 lifespan 状态取出共享的搜索 Service 传给接口函数；
2. 定义 OpenAPI 请求/响应模型（VectorSearchRequest / VectorSearchResult）；
3. 把 Ollama/Qdrant 上游异常映射成稳定、脱敏的 HTTP 错误码。

它自己不生成 query 向量、不构造 Qdrant 过滤条件、不访问 PostgreSQL，也不创建/切换/
写入 Qdrant——真正的 I/O 在 VectorSearchService（Ollama）和 QdrantVectorSearch 里。
本接口不实现 LLM 或 RAG。
"""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agent_lab.pipeline.ollama_embedding_provider import (
    EmbeddingResponseError,
    OllamaAuthenticationError,
    OllamaConnectionError,
    OllamaEmbeddingError,
    OllamaModelNotFoundError,
    OllamaServiceError,
    OllamaTimeoutError,
)
from agent_lab.qdrant.search import (
    QdrantSearchAuthenticationError,
    QdrantSearchConfigurationError,
    QdrantSearchConnectionError,
    QdrantSearchResponseError,
    QdrantSearchServiceError,
    QdrantSearchTargetNotFoundError,
    QdrantSearchTimeoutError,
    QdrantVectorSearchError,
)
from agent_lab.schemas.vector_search import (
    VectorSearchRequest,
    VectorSearchResult,
)
from agent_lab.services.vector_search_service import (
    QueryVectorValidationError,
    VectorSearchService,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["vector-search"])

VectorSearchErrorCode = Literal[
    "search_runtime_unavailable",
    "embedding_authentication_failed",
    "embedding_unavailable",
    "embedding_timeout",
    "embedding_model_not_found",
    "embedding_response_invalid",
    "qdrant_authentication_failed",
    "qdrant_unavailable",
    "qdrant_timeout",
    "qdrant_target_missing",
    "qdrant_configuration_invalid",
    "qdrant_response_invalid",
    "qdrant_service_error",
]


class VectorSearchRuntimeUnavailableError(RuntimeError):
    """ASGI lifespan 尚未提供进程级只读 Search Runtime。"""


class VectorSearchErrorResponse(BaseModel):
    """搜索上游失败时返回给客户端的统一错误格式（固定三字段）。

    只存在于单次 HTTP 响应里，不落库、不进日志。三个字段各有分工：
    - ``code``：稳定错误码字符串，客户端据此分支处理（如 embedding_timeout）；
    - ``detail``：给人看的安全概述，绝不含密钥、query、向量或第三方响应原文；
    - ``retryable``：是否「不改请求、稍后重试就可能成功」；true 不代表会自动重试。
    """

    code: VectorSearchErrorCode = Field(
        description=(
            "由 API 异常映射产生的必需稳定错误码；不可空，用于客户端区分 Embedding、"
            "Qdrant、timeout、配置和响应契约失败。"
        ),
    )
    detail: str = Field(
        min_length=1,
        description=(
            "由 API 层生成的必需安全错误概述；不可空，不包含用户 query、密钥、Vector、"
            "新闻正文或第三方原始响应。"
        ),
    )
    retryable: bool = Field(
        description=(
            "由错误类别推导的必需重试提示；不可空，true 仅表示稍后重试可能恢复，"
            "不代表服务或客户端会自动重试。"
        ),
    )

    model_config = ConfigDict(frozen=True)


def get_vector_search_service(request: Request) -> VectorSearchService:
    """从应用 lifespan 状态取出共享的只读搜索 Service（FastAPI 依赖注入函数）。

    为什么从 request 里取：FastAPI 的 ``Depends(get_vector_search_service)`` 只负责
    调用本函数并把返回值注入接口参数；Service 本体在 lifespan 启动时创建并存进
    ``application.state``，所以必须借当前请求拿到 app 对象再取。所有并发请求共享
    同一个 Service（共享 Ollama/Qdrant 连接池），而不是每个请求新建一个。

    Args:
        request: 当前 HTTP 请求，用于访问所属应用的 ``state``。

    Returns:
        lifespan 启动时创建并由并发请求共享的 ``VectorSearchService``。

    Raises:
        VectorSearchRuntimeUnavailableError: 应用未经过 lifespan 启动或 Runtime 初始化
            缺失；全局 API handler 会把它映射为稳定的 503 错误契约。

    Notes:
        本依赖只读取进程内对象，不执行 PostgreSQL、Ollama/Embedding 或 Qdrant I/O。
    """

    runtime = getattr(request.app.state, "vector_search_runtime", None)
    service = getattr(runtime, "service", None)
    if service is None:
        raise VectorSearchRuntimeUnavailableError(
            "向量检索运行时不可用。"
        )
    return service


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

    搜索链路（完整说明见 docs/learning/04_vector_search.md）：
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
    """

    try:
        # 核心就一步：把请求交给共享 Service（内部做 query 向量化 + Qdrant 查询）
        return await service.search(search_request)
    except (
        OllamaEmbeddingError,
        QueryVectorValidationError,
        QdrantVectorSearchError,
    ) as exc:
        # 已知的上游失败：只记录稳定异常类型（不调 str(exc)，避免敏感内容进日志），
        # 然后映射成脱敏的 502/503/504 响应
        logger.warning("向量检索上游故障：%s", type(exc).__name__)
        return build_vector_search_error_response(exc)


def build_vector_search_error_response(error: Exception) -> JSONResponse:
    """把已知的搜索异常「按类型」映射成固定 HTTP 状态码和错误码。

    为什么按类型（isinstance）而不是按异常消息分类：异常文本里可能混着第三方返回的
    敏感内容，只认类型就能稳定分类，同时保证客户端拿到的永远是同一套错误契约
    （code/detail/retryable + 502/503/504）。这一大串 if/elif 本质是一张
    「异常类型 → HTTP 错误」的查表。

    Args:
        error: 已分类的 Runtime、Ollama、query Vector 或 Qdrant 边界异常。

    Returns:
        与 OpenAPI ``VectorSearchErrorResponse`` 一致的 502、503 或 504 JSON 响应。

    Notes:
        只按异常类型选择常量，不读取异常文本，不执行 PostgreSQL、Embedding 或 Qdrant
        I/O，也不写入任何外部数据。
    """

    if isinstance(error, VectorSearchRuntimeUnavailableError):
        return _response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "search_runtime_unavailable",
            "向量检索运行时不可用。",
            retryable=False,
        )

    if isinstance(error, OllamaAuthenticationError):
        return _response(
            status.HTTP_502_BAD_GATEWAY,
            "embedding_authentication_failed",
            "Embedding service authentication failed.",
            retryable=False,
        )
    if isinstance(error, OllamaTimeoutError):
        return _response(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "embedding_timeout",
            "Embedding service request timed out.",
            retryable=True,
        )
    if isinstance(error, OllamaConnectionError):
        return _response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "embedding_unavailable",
            "Embedding service is unavailable.",
            retryable=True,
        )
    if isinstance(error, OllamaModelNotFoundError):
        return _response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "embedding_model_not_found",
            "Configured embedding model is unavailable.",
            retryable=False,
        )
    if isinstance(
        error,
        (EmbeddingResponseError, QueryVectorValidationError),
    ):
        return _response(
            status.HTTP_502_BAD_GATEWAY,
            "embedding_response_invalid",
            "Embedding service returned an invalid vector.",
            retryable=False,
        )
    if isinstance(error, (OllamaServiceError, OllamaEmbeddingError)):
        return _response(
            status.HTTP_502_BAD_GATEWAY,
            "embedding_unavailable",
            "Embedding service request failed.",
            retryable=True,
        )
    if isinstance(error, QdrantSearchAuthenticationError):
        return _response(
            status.HTTP_502_BAD_GATEWAY,
            "qdrant_authentication_failed",
            "Vector database authentication failed.",
            retryable=False,
        )
    if isinstance(error, QdrantSearchTimeoutError):
        return _response(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "qdrant_timeout",
            "Vector database query timed out.",
            retryable=True,
        )
    if isinstance(error, QdrantSearchConnectionError):
        return _response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "qdrant_unavailable",
            "Vector database is unavailable.",
            retryable=True,
        )
    if isinstance(error, QdrantSearchTargetNotFoundError):
        return _response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "qdrant_target_missing",
            "Vector search Alias or Collection is unavailable.",
            retryable=False,
        )
    if isinstance(error, QdrantSearchConfigurationError):
        return _response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "qdrant_configuration_invalid",
            "Vector database configuration is incompatible.",
            retryable=False,
        )
    if isinstance(error, QdrantSearchResponseError):
        return _response(
            status.HTTP_502_BAD_GATEWAY,
            "qdrant_response_invalid",
            "Vector database returned an invalid search response.",
            retryable=False,
        )
    if isinstance(error, QdrantSearchServiceError):
        return _response(
            status.HTTP_502_BAD_GATEWAY,
            "qdrant_service_error",
            "Vector database query failed.",
            retryable=True,
        )
    # 此函数只接收上面 endpoint 已限制的异常基类；保留防御性分支可避免未来新增子类
    # 时无意返回包含原始异常的默认字符串。
    return _response(
        status.HTTP_502_BAD_GATEWAY,
        "qdrant_service_error",
        "Vector search upstream request failed.",
        retryable=False,
    )


def _response(
    status_code: int,
    code: VectorSearchErrorCode,
    detail: str,
    *,
    retryable: bool,
) -> JSONResponse:
    """构造经过 Pydantic 校验的稳定错误响应。

    统一走 Pydantic 模型构造并序列化，保证所有分支的错误字段结构完全一致，
    客户端不需要为不同失败路径写不同的解析逻辑。
    """

    content = VectorSearchErrorResponse(
        code=code,
        detail=detail,
        retryable=retryable,
    ).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=content)
