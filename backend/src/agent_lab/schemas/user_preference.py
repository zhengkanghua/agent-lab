"""定义当前登录账号读写自己个人偏好的输入输出契约。

本模块只服务「当前登录账号读/写自己的偏好」这个用例，不含任何指向他人的字段：没有账号 id、
没有权限开关。取值域由登录态决定，不由路径参数决定——从形态上排除「改别人配置」的可能。

请求体不含密码，所以这里不挂 ``SanitizedValidationRoute``；但提示词长度上限必须**在本 schema
上重建**：它原先只挂在 ``AgentChatRequest.system_prompt`` 的字段上，那个字段随本次改动删除，
不在这里补一道的话服务端的边界约束就没了（前端会截断，但服务端才是真边界）。数值沿用
``agent.limits`` 的同一个常量，不新写一个。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_lab.agent.limits import MAX_SYSTEM_PROMPT_CHARS
from agent_lab.schemas.document_search import (
    DEFAULT_DOCUMENT_LIMIT,
    DEFAULT_MATCHES_PER_DOCUMENT,
    MAX_DOCUMENT_LIMIT,
    MAX_MATCHES_PER_DOCUMENT,
)


class UserPreferenceResponse(BaseModel):
    """当前登录账号的个人偏好。

    ``system_prompt`` 用 ``None`` 表达「未配置」而不是空串：「没配过」与「配了一份空的」
    不是一回事，前者回落到服务端默认提示词，后者会让模型失去角色约束。
    """

    system_prompt: str | None = Field(
        description="自定义系统提示词；为 null 表示使用服务端内置默认提示词。",
    )
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

    system_prompt: str | None = Field(
        default=None,
        max_length=MAX_SYSTEM_PROMPT_CHARS,
        repr=False,
        description=(
            "自定义系统提示词；为 null 表示恢复使用服务端内置默认提示词。"
            "作为新会话的初始提示词，已开始的会话不受影响。"
        ),
    )
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

    @field_validator("system_prompt")
    @classmethod
    def _normalize_system_prompt(cls, value: str | None) -> str | None:
        """把纯空白提示词当作「没配」，回落到默认提示词。

        为什么不报错：用户清空输入框后提交的是空串，语义是「用默认的」，不是「用一份空
        提示词」。与 ``AgentChatRequest`` 里那条校验同一个理由，这里沿用相同行为，免得
        从请求体传提示词和从偏好读提示词两种路径对同一份输入给出不同结论。

        Args:
            value: 调用方提交的提示词，可能为 ``None`` 或空白。

        Returns:
            去掉首尾空白的提示词，或 ``None`` 表示使用默认提示词。
        """

        if value is None or not value.strip():
            return None
        return value.strip()


__all__ = ["UserPreferenceResponse", "UserPreferenceUpdateRequest"]
