"""知识库 HTTP 错误契约；业务输入输出 DTO 由内部组件提供。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseErrorResponse(BaseModel):
    """不包含数据库、请求正文或异常文本的稳定错误。"""

    code: Literal[
        "knowledge_base_not_found",
        "knowledge_base_inactive",
        "knowledge_base_key_conflict",
        "knowledge_base_storage_unavailable",
        "knowledge_base_forbidden",
        "invalid_request",
    ] = Field(description="稳定错误码。")
    detail: str = Field(description="安全的中文错误概述。")
    retryable: bool = Field(description="稍后重试是否可能恢复。")

    model_config = ConfigDict(frozen=True)
