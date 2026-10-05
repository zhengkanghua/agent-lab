"""可用模型 API 的请求与响应模型。

字段的 ``description`` 会进 ``/openapi.json``，前端 ``openapi-typescript`` 拿它生成类型，
所以这里写的是「这个字段是什么」。

两边的视图刻意不同：后台管理视图是**原始字段**（展示名为空就回 ``null``，编辑表单据此知道
这一条没有展示名），用户的选择列表是**已经落过回落的展示名**（条目没填展示名时给的就是上游
模型名），选择器拿到就能直接显示，不必自己再判一次空。
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

LlmUpstreamModelName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
LlmModelDisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=255)
]

# 上下文窗口这一栏的说明会进前端表单提示，所以把「填错了两个方向都有后果」写进来：
# 填大了可能超过上游真实窗口、整轮失败；填小了会让历史压缩来得更早（模型更早只剩摘要）。
# **它现在只影响压缩时机**：工具输出已经不再按它的比例截断（老板在验收期定的）。
_CONTEXT_WINDOW_DESCRIPTION = (
    "上游模型的上下文窗口 token 数，必填。填你确认过的最小值：历史压缩什么时候触发按它的比例算。"
    "填大了请求可能超过上游的真实窗口、整轮失败；填小了会提前压缩，模型更早只剩下摘要。"
)

# 契约模型的公共配置：多余字段直接拒绝（避免调用方以为某个字段生效了）。
_REQUEST_CONFIG = ConfigDict(extra="forbid", frozen=True)


def _blank_display_name_is_no_display_name(display_name: str | None) -> str | None:
    """把纯空白的展示名整成「没有展示名」，让服务层只有一个判断。

    表单里被清空的输入框提交上来就是空串，它与「这条模型没有展示名」是同一个意思——两者都
    落到界面上回落成上游模型名。真正的展示名不会是纯空白，所以只把纯空白整成 ``None``。
    """

    if display_name is not None and not display_name.strip():
        return None
    return display_name


class LlmModelCreateRequest(BaseModel):
    """在一个上游渠道下面新增一条可用模型。"""

    provider_id: UUID = Field(description="所属上游渠道 id。")
    upstream_model_name: LlmUpstreamModelName = Field(
        description="上游那一侧真实存在的模型名，直接交给模型客户端；同一渠道内不可重复。",
    )
    display_name: LlmModelDisplayName | None = Field(
        default=None,
        description="展示名称；留空表示这条模型没有展示名，界面上回落到上游模型名。",
    )
    context_window: int = Field(gt=0, description=_CONTEXT_WINDOW_DESCRIPTION)
    is_default: bool = Field(
        default=False,
        description=(
            "是否设为默认（没选模型时用的那一条）。只能打在一个保存后就可用的模型上："
            "自身启用且所属渠道也启用。"
        ),
    )
    enabled: bool = Field(
        default=True,
        description="创建后是否启用；停用的模型保留配置，只是不再出现在可选列表里。",
    )

    model_config = _REQUEST_CONFIG

    @field_validator("display_name")
    @classmethod
    def blank_display_name_means_no_display_name(
        cls, display_name: str | None
    ) -> str | None:
        """新建时留空就是「没有展示名」，不是「有一个空白的展示名」。"""

        return _blank_display_name_is_no_display_name(display_name)


class LlmModelUpdateRequest(BaseModel):
    """修改一条可用模型；未提供的字段保持不变。"""

    provider_id: UUID | None = Field(
        default=None,
        description="新的所属上游渠道 id；不传表示不修改。目标渠道必须是启用状态。",
    )
    upstream_model_name: LlmUpstreamModelName | None = Field(
        default=None,
        description="新的上游模型名；不传表示不修改。同一渠道内不可与其它条目重复。",
    )
    display_name: LlmModelDisplayName | None = Field(
        default=None,
        description=(
            "新的展示名称；传空字符串表示改成「没有展示名」（界面回落到上游模型名），"
            "不传表示不修改。"
        ),
    )
    context_window: int | None = Field(default=None, gt=0, description=_CONTEXT_WINDOW_DESCRIPTION)
    is_default: bool | None = Field(
        default=None,
        description=(
            "是否设为默认；不传表示不修改。置真要求它保存后仍可用；置假只对本来就不是默认的"
            "条目有效——当前默认的标记不能在这里取消，要先把它换到别的条目上。"
        ),
    )
    enabled: bool | None = Field(
        default=None,
        description="是否启用；不传表示不修改。当前默认模型不能被停用。",
    )

    model_config = _REQUEST_CONFIG

    @field_validator("display_name")
    @classmethod
    def blank_display_name_means_no_display_name(
        cls, display_name: str | None
    ) -> str | None:
        """编辑时留空 = 改成没有展示名；规整方式与新建那条路共用同一个函数。"""

        return _blank_display_name_is_no_display_name(display_name)


class LlmModelResponse(BaseModel):
    """后台管理列表里的一条可用模型（含已停用的），带所属渠道的展示名与启用位。"""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(description="可用模型 id。")
    provider_id: UUID = Field(description="所属上游渠道 id。")
    provider_name: str = Field(description="所属上游渠道的展示名称。")
    provider_enabled: bool = Field(
        description=(
            "所属渠道是否启用。它不在模型这一行上，是查渠道查出来的：用户能不能选到这条模型，"
            "由它与这里的 enabled 相与决定——停用渠道时不会去逐个改模型的启用位。"
        ),
    )
    upstream_model_name: str = Field(description="上游那一侧真实存在的模型名。")
    display_name: str | None = Field(
        description="展示名称；为空表示这条模型没有展示名，界面上回落到上游模型名。",
    )
    context_window: int = Field(description="上游模型的上下文窗口 token 数。")
    is_default: bool = Field(description="是否是没选模型时使用的默认条目；全目录最多一条为真。")
    enabled: bool = Field(description="这条模型自己是否启用；停用的保留配置。")
    created_at: datetime = Field(description="创建时间，UTC。")
    updated_at: datetime = Field(description="最近一次实际修改时间，UTC。")


class AvailableLlmModelResponse(BaseModel):
    """用户选择列表里的一条：只回选择器要用的字段，且只包含当前可用的模型。"""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(description="可用模型 id；会话里存的就是它。")
    display_name: str = Field(
        description="选择器上显示的名字：条目填过展示名就是它，没填就是上游模型名。",
    )
    context_window: int = Field(description="上游模型的上下文窗口 token 数。")
    provider_name: str = Field(description="所属上游渠道的展示名称，用于在选择器里分组显示。")


class ResolvedLlmModel(BaseModel):
    """当轮生效的模型条目：身份、展示名与上下文窗口。

    它同时是**运行上下文里那一项**与**提问消息上那份冻结快照**的形状：解析一次、两边共用，
    回放与接手续跑读的就是它。

    展示名在这里已经落过回落（条目填过就用它，没填就是上游模型名），所以一定非空；快照必须
    **自足**——条目后来改名或停用不改写已经发生过的那几轮，而接手那一轮也不许回查目录。
    窗口跟着一起走是同样的理由：它自己必须是完整的，运行期拿它算压缩比例，不依赖任何回查。
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(description="可用模型 id。")
    display_name: str = Field(
        description="当轮那个时刻的名字：条目填过展示名就是它，没填就是上游模型名。",
    )
    context_window: int = Field(
        description=(
            "当轮那个时刻的上下文窗口 token 数。冻结下来是因为运行期要按它算压缩比例，"
            "而接手续跑那一轮不许回查目录。"
        ),
    )


class LlmModelErrorResponse(BaseModel):
    """可用模型管理 API 的稳定、脱敏错误结构。"""

    code: str = Field(description="供前端稳定识别的错误代码。")
    detail: str = Field(description="不含数据库异常文本的安全说明。")
    retryable: bool = Field(description="相同请求稍后重试是否可能成功。")


__all__ = [
    "AvailableLlmModelResponse",
    "LlmModelCreateRequest",
    "LlmModelDisplayName",
    "LlmModelErrorResponse",
    "LlmModelResponse",
    "LlmModelUpdateRequest",
    "LlmUpstreamModelName",
    "ResolvedLlmModel",
]
