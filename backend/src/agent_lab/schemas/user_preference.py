"""定义当前登录账号读写自己个人偏好的输入输出契约。

本模块只服务「当前登录账号读/写自己的偏好」这个用例，不含任何指向他人的字段：没有账号 id、
没有权限开关。取值域由登录态决定，不由路径参数决定——从形态上排除「改别人配置」的可能。

请求体只有两个检索数量参数，不含密码，所以这里不挂 ``SanitizedValidationRoute``。
"""

from pydantic import BaseModel, ConfigDict, Field

from agent_lab.schemas.document_search import (
    DEFAULT_DOCUMENT_LIMIT,
    DEFAULT_MATCHES_PER_DOCUMENT,
    MAX_DOCUMENT_LIMIT,
    MAX_MATCHES_PER_DOCUMENT,
)


class UserPreferenceResponse(BaseModel):
    """当前登录账号的个人偏好。"""

    document_limit: int = Field(
        description="检索默认返回的文档数。",
    )
    matches_per_document: int = Field(
        description="每篇文档默认保留的片段数。",
    )

    model_config = ConfigDict(from_attributes=True)


class UserPreferenceUpdateRequest(BaseModel):
    """整体覆盖当前登录账号的个人偏好。

    整体覆盖而不是部分更新：设置页是「草稿 + 显式保存」的形态，保存动作提交的就是完整
    一份，不存在「只改其中一个字段」的调用方；整体覆盖语义更简单，也不会出现两个客户端
    各改一个字段互相覆盖的合并问题。
    """

    document_limit: int = Field(
        default=DEFAULT_DOCUMENT_LIMIT,
        ge=1,
        le=MAX_DOCUMENT_LIMIT,
        description="检索默认返回的文档数。",
    )
    matches_per_document: int = Field(
        default=DEFAULT_MATCHES_PER_DOCUMENT,
        ge=1,
        le=MAX_MATCHES_PER_DOCUMENT,
        description="每篇文档默认保留的片段数。",
    )

    model_config = ConfigDict(extra="forbid")


__all__ = ["UserPreferenceResponse", "UserPreferenceUpdateRequest"]
