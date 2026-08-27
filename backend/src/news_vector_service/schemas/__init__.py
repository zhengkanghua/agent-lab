"""外部 API 与应用用例使用的 Pydantic 请求/响应契约。"""

from news_vector_service.schemas.auth import AuthUserCreate, AuthUserResponse

from news_vector_service.schemas.document_search import (
    DocumentDetailResponse,
    DocumentSearchMatch,
    DocumentSearchRequest,
    DocumentSearchResult,
)

from news_vector_service.schemas.vector_search import (
    VectorSearchFilters,
    VectorSearchRequest,
    VectorSearchResult,
)

from news_vector_service.schemas.pipeline import (
    PipelineErrorResponse,
    PipelineRunOnceRequest,
    PipelineRunOnceResponse,
)
from news_vector_service.schemas.user_admin import (
    UserAdminCreateRequest,
    UserAdminErrorResponse,
    UserAdminPasswordRequest,
    UserAdminResponse,
    UserAdminUpdateRequest,
    UserSessionRevocationResponse,
)

__all__ = [
    "AuthUserCreate",
    "AuthUserResponse",
    "VectorSearchFilters",
    "VectorSearchRequest",
    "VectorSearchResult",
    "PipelineErrorResponse",
    "PipelineRunOnceRequest",
    "PipelineRunOnceResponse",
    "DocumentDetailResponse",
    "DocumentSearchMatch",
    "DocumentSearchRequest",
    "DocumentSearchResult",
    "UserAdminCreateRequest",
    "UserAdminErrorResponse",
    "UserAdminPasswordRequest",
    "UserAdminResponse",
    "UserAdminUpdateRequest",
    "UserSessionRevocationResponse",
]
