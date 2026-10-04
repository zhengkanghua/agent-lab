"""上游渠道管理 API 的请求与响应模型。

字段的 ``description`` 会进 ``/openapi.json``，前端 ``openapi-typescript`` 拿它生成类型，
所以这里写的是「这个字段是什么」。

凭据是这一组模型里唯一敏感的东西，两边的处理刻意不对称：请求体里的明文用 ``SecretStr``
包住（``repr`` 与 Pydantic 的校验错误都不会显示它），响应里**根本没有凭据字段**，只有一个
``credential_configured`` 布尔。配合路由的 ``SanitizedValidationRoute``，校验失败也不会把原始
请求正文回给调用方。
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)

from agent_lab.config.llm import LlmProvider

LlmProviderName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]

# 契约模型的公共配置：多余字段直接拒绝（避免调用方以为某个字段生效了）、输入错误里不回显
# 原始值（请求体里有凭据明文）。
_REQUEST_CONFIG = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


def _blank_credential_is_no_credential(credential: SecretStr | None) -> SecretStr | None:
    """把纯空白的凭据整成「没有凭据」，让服务层只有一个判断。

    表单里被清空的密码框提交上来就是空串，它与「字段根本没出现」是同一个意思。真正的凭据不会
    是纯空白，所以只把纯空白整成 ``None``，不 trim 有内容的凭据——密钥可能有意义地以空格开头
    或结尾。

    **新建与编辑两条路都要过这一步。** 少了新建那一条，``credential: ""`` 会被当成一份「空的
    凭据」加密存下：列表上显示成「已配置」，而它其实没有凭据，这条渠道第一次被选中时就会以
    构造期失败的样子炸出来——正是 spec 0002 那句「接入类型要求凭据而凭据为空时，保存被拒绝」
    要挡掉的形态。
    """

    if credential is not None and not credential.get_secret_value().strip():
        return None
    return credential


class LlmProviderCreateRequest(BaseModel):
    """新增一条上游渠道；凭据只以明文出现在这一次请求里。"""

    name: LlmProviderName = Field(description="渠道展示名称，只用于后台识别，不要求唯一。")
    provider: LlmProvider = Field(
        description=(
            "接入类型，决定构造客户端走哪个分支：openai_compatible 必须配凭据，"
            "ollama 允许留空。"
        ),
    )
    base_url: AnyHttpUrl = Field(
        description="上游 HTTP API 根地址；OpenAI 兼容中转站通常需要带 /v1 后缀。",
    )
    credential: SecretStr | None = Field(
        default=None,
        description=(
            "接入凭据明文；后端加密后落库，此后任何读取接口都不再返回它。"
            "不需要凭据的接入类型可以留空。"
        ),
    )
    enabled: bool = Field(
        default=True,
        description="创建后是否启用；停用的渠道保留配置，只是不参与选择。",
    )

    model_config = _REQUEST_CONFIG

    @field_validator("credential")
    @classmethod
    def blank_credential_means_no_credential(
        cls, credential: SecretStr | None
    ) -> SecretStr | None:
        """新建时留空就是「没有凭据」，不是「有一份空凭据」。

        规整之后 ``openai_compatible`` 会被 Service 判成「要求凭据却没有」而拒绝保存，
        ``ollama`` 则照常存成没有凭据。
        """

        return _blank_credential_is_no_credential(credential)


class LlmProviderUpdateRequest(BaseModel):
    """修改一条上游渠道；未提供的字段保持不变，凭据留空表示不改。"""

    name: LlmProviderName | None = Field(default=None, description="新的展示名称；不传表示不修改。")
    provider: LlmProvider | None = Field(
        default=None,
        description=(
            "新的接入类型；不传表示不修改。改成 openai_compatible 时必须同时补上凭据，"
            "除非这条渠道已经存过凭据。"
        ),
    )
    base_url: AnyHttpUrl | None = Field(default=None, description="新的上游地址；不传表示不修改。")
    credential: SecretStr | None = Field(
        default=None,
        description=(
            "新的接入凭据明文；留空（不传或空字符串）表示**不改动**已存凭据，"
            "不是清空凭据。"
        ),
    )
    enabled: bool | None = Field(default=None, description="是否启用；不传表示不修改。")

    model_config = _REQUEST_CONFIG

    @field_validator("credential")
    @classmethod
    def blank_credential_means_unchanged(cls, credential: SecretStr | None) -> SecretStr | None:
        """编辑时留空 = 不改动已存凭据；规整方式与新建那条路共用同一个函数。"""

        return _blank_credential_is_no_credential(credential)


class LlmProviderResponse(BaseModel):
    """一条上游渠道的公开视图：没有任何凭据字段，只有「有没有存过」。"""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(description="上游渠道 id。")
    name: str = Field(description="渠道展示名称。")
    provider: LlmProvider = Field(description="接入类型：openai_compatible 或 ollama。")
    base_url: str = Field(description="上游 HTTP API 根地址。")
    enabled: bool = Field(description="是否启用；停用的渠道保留配置。")
    credential_configured: bool = Field(
        description=(
            "这条渠道是否存过凭据。它只表示「有没有存过」——返回的永远不是凭据本身，"
            "也不代表那份凭据仍然有效。"
        ),
    )
    created_at: datetime = Field(description="创建时间，UTC。")
    updated_at: datetime = Field(description="最近一次实际修改时间，UTC。")


class LlmProviderErrorResponse(BaseModel):
    """上游渠道管理 API 的稳定、脱敏错误结构。"""

    code: str = Field(description="供前端稳定识别的错误代码。")
    detail: str = Field(description="不含凭据、密钥或数据库异常文本的安全说明。")
    retryable: bool = Field(description="相同请求稍后重试是否可能成功。")


__all__ = [
    "LlmProviderCreateRequest",
    "LlmProviderErrorResponse",
    "LlmProviderName",
    "LlmProviderResponse",
    "LlmProviderUpdateRequest",
]
