"""定义 Agent 对话的请求契约和 SSE 事件契约。

本模块位于 HTTP/应用边界的 Pydantic 层，只描述「一次提问长什么样」和「流式返回的每个
事件长什么样」；它不调用模型、不执行检索，也不决定事件何时发出（那是
``agent/streaming.py`` 的职责）。

**为什么事件要做成带 ``event`` 判别字段的联合类型**：SSE 是一条条 JSON，前端必须先知道
这条是 token 还是 tool_call 才能决定怎么渲染。把判别字段写进 Pydantic 的
``Discriminator``，OpenAPI 里就会生成一个可判别联合，前端 ``openapi-typescript`` 生成的
TS 类型能靠 ``switch (event.event)`` 自动收窄，不需要手写一份平行的类型定义——手写的那份
迟早和后端分叉。

事件命名用现在完成/进行时的名词（``token``/``tool_call``/``tool_result``/``done``/
``error``）而不是动词，因为它们描述的是「已经发生的事实」，前端只做渲染。
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Discriminator, Field, RootModel, field_validator

from agent_lab.agent.limits import MAX_SYSTEM_PROMPT_CHARS, MAX_USER_MESSAGE_CHARS
from agent_lab.schemas._query_validators import require_non_whitespace_query


class AgentChatRequest(BaseModel):
    """一次 Agent 对话提问。

    ``thread_id`` 是「会话」的标识：带上同一个值就接着上次聊，历史由 checkpointer 按它
    存取；省略则由服务端新建一个并在 ``done`` 事件里告知，这样前端不必自己生成 UUID，
    也不会因为伪造一个 id 而读到别人的会话。
    """

    message: str = Field(
        max_length=MAX_USER_MESSAGE_CHARS,
        repr=False,
        description=(
            "用户这一轮的提问，不可为空或纯空白；原文可能敏感，只进入模型上下文，"
            "不写入 Qdrant Payload。"
        ),
    )
    thread_id: UUID | None = Field(
        default=None,
        description=(
            "要接着聊的会话 id；省略表示新建会话，新 id 通过 done 事件返回。"
        ),
    )
    system_prompt: str | None = Field(
        default=None,
        max_length=MAX_SYSTEM_PROMPT_CHARS,
        repr=False,
        description=(
            "覆盖本次运行的系统提示词；省略则使用服务端内置的默认提示词。"
            "只影响本次请求，不会被持久化。"
        ),
    )

    model_config = ConfigDict(frozen=True)

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        """拒绝纯空白提问，避免为一次空调用付出模型开销。

        Args:
            value: 调用方提交的原始提问。

        Returns:
            保留原始有效空白的提问文本。

        Raises:
            ValueError: 提问只包含空白字符。
        """

        return require_non_whitespace_query(value)

    @field_validator("system_prompt")
    @classmethod
    def _validate_system_prompt(cls, value: str | None) -> str | None:
        """把纯空白的自定义提示词当作「没给」，回落到默认提示词。

        为什么不报错：前端清空输入框后提交的是空串，那语义就是「用默认的」，而不是
        「用一份空提示词」——真用空提示词会让模型完全失去角色约束和引用要求。

        Args:
            value: 调用方提交的自定义系统提示词，可能为 ``None`` 或空白。

        Returns:
            去掉首尾空白后的提示词，或 ``None`` 表示使用默认提示词。
        """

        if value is None or not value.strip():
            return None
        return value.strip()


class AgentTokenEvent(BaseModel):
    """模型输出的一小段文本增量。

    一次运行会有很多条，前端按到达顺序追加即可。它只承载「最终回答」的增量：工具调用
    的参数不走这里，避免用户看到半截 JSON。
    """

    event: Literal["token"] = "token"
    text: str = Field(
        repr=False,
        description="要追加到当前回答末尾的文本增量，可能只有一个字符。",
    )

    model_config = ConfigDict(frozen=True)


class AgentToolCallEvent(BaseModel):
    """模型决定调用某个工具。

    发这个事件是为了让「模型正在查资料」这件事可见——否则工具执行期间前端只有一段
    静默，用户无法区分是在检索还是卡住了。
    """

    event: Literal["tool_call"] = "tool_call"
    tool: str = Field(description="被调用的工具名，如 search_news、read_document。")
    arguments: dict[str, object] = Field(
        default_factory=dict,
        repr=False,
        description=(
            "模型给出的调用参数；可能包含它自己改写的检索词，属于展示给用户看的"
            "调用轨迹，不含服务端凭据。"
        ),
    )

    model_config = ConfigDict(frozen=True)


class AgentToolResultEvent(BaseModel):
    """一次工具调用的结果，成功或失败。

    ``failed`` 为真时 ``content`` 是查表得到的安全文案，不是异常文本——异常细节只进日志，
    见 ``agent/middleware.py`` 的 ``sanitize_tool_error``。
    """

    event: Literal["tool_result"] = "tool_result"
    tool: str = Field(description="返回结果的工具名。")
    content: str = Field(
        repr=False,
        description="工具返回给模型的文本；失败时是安全中文说明，不含异常细节。",
    )
    failed: bool = Field(
        default=False,
        description="工具是否失败；失败后模型仍会继续，可能换个检索词重试。",
    )

    model_config = ConfigDict(frozen=True)


class AgentDoneEvent(BaseModel):
    """一次运行正常结束，流即将关闭。

    它同时承担「告知会话 id」的职责：新建会话时前端要拿这个值发起下一轮。
    """

    event: Literal["done"] = "done"
    thread_id: UUID = Field(description="本次运行所属会话的 id，下一轮带上它即可续聊。")

    model_config = ConfigDict(frozen=True)


class AgentErrorEvent(BaseModel):
    """一次运行因已分类的失败而中断。

    字段与项目其他接口的错误响应保持同一形状（``code``/``detail``/``retryable``），前端
    可以复用同一套错误文案映射，不必为流式接口单开一套。

    为什么错误走事件而不是 HTTP 状态码：响应头在第一个 token 发出时就已经发送，之后
    没法再改状态码。所以流一旦开始，所有失败都只能作为事件送达。
    """

    event: Literal["error"] = "error"
    code: str = Field(description="稳定机器错误码，前端据此选择提示文案。")
    detail: str = Field(description="安全中文概述，不含异常文本或上游细节。")
    retryable: bool = Field(
        description="是否「不改提问、稍后重试可能成功」；认证与配置类错误为 False。",
    )

    model_config = ConfigDict(frozen=True)


# 所有 SSE 事件的可判别联合。放进 endpoint 的 ``responses`` 后，OpenAPI 会生成
# 带 discriminator 的 schema，前端生成的 TS 类型即可按 ``event`` 字段自动收窄。
AgentChatEvent = Annotated[
    AgentTokenEvent
    | AgentToolCallEvent
    | AgentToolResultEvent
    | AgentDoneEvent
    | AgentErrorEvent,
    Discriminator("event"),
]


class AgentChatEventEnvelope(RootModel[AgentChatEvent]):
    """SSE ``data:`` 行里那一个 JSON 对象的类型。

    它存在的唯一理由是给 OpenAPI 一个「具名的联合类型」：FastAPI 的 ``responses`` 需要
    一个模型类，而裸的 ``Annotated`` 联合不是类。响应体本身不是这个信封的 JSON——真正
    发出去的是 ``text/event-stream``，每行 ``data:`` 后面跟着联合成员之一。
    """

    root: AgentChatEvent


class AgentDefaultPromptResponse(BaseModel):
    """``GET /agent/default-prompt`` 的响应。

    包成对象而不是直接返回一个 JSON 字符串：裸字符串的响应体没有加字段的余地，以后想带上
    「这份提示词的版本号」或「可用工具清单」就得改破坏性接口。
    """

    system_prompt: str = Field(
        description="不传 system_prompt 时实际生效的默认系统提示词全文。",
    )

    model_config = ConfigDict(frozen=True)


__all__ = [
    "AgentChatEvent",
    "AgentChatEventEnvelope",
    "AgentChatRequest",
    "AgentDefaultPromptResponse",
    "AgentDoneEvent",
    "AgentErrorEvent",
    "AgentTokenEvent",
    "AgentToolCallEvent",
    "AgentToolResultEvent",
]
