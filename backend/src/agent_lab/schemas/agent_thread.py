"""定义会话列表与会话历史回放的对外契约（Pydantic 模型）。

本模块位于 HTTP/应用边界的 Pydantic 层，只描述「会话列表长什么样」和「一段历史回放长什么样」；
不读数据库、不调 checkpointer，也不决定归属（那是 ``services.agent_thread_service``）。

回放的形状刻意与 SSE 事件流**不同构**。流式那边是「一串按时间到达的事件」，前端自己攒成一轮一轮；
回放这边已经是既成事实，没有中间态可言，所以直接给「一轮一问一答」的结构，前端灌进界面即可。
让回放也发一串假事件是另一种选择，但那要求前端把状态机重放一遍，多一条只在回放时才走的代码路径。
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# 会话列表一页的条数上下限。上限 100 与 document_search 的 MAX_DOCUMENT_LIMIT 取同一个数量级，
# 但两者无关联：这里限制的是「一次返回多少个会话」，跟检索结果没有共享语义。
DEFAULT_THREAD_PAGE_SIZE = 20
MAX_THREAD_PAGE_SIZE = 100


class AgentThreadSummary(BaseModel):
    """会话列表里的一行。

    刻意不含消息内容、轮数和「最后一条回答」：那些要么是 checkpointer 里已有内容的副本
    （会因历史压缩而与真实上下文不一致），要么需要额外维护一个容易飘的计数列。
    列表只承担导航，认出「是哪个会话」够用。
    """

    thread_id: UUID = Field(description="会话 id；带上它请求历史或续聊。")
    title: str = Field(
        description="会话标题，由首条提问截断而来，最长 60 字符；不含省略号，截断标记由前端呈现。",
    )
    created_at: datetime = Field(description="会话创建时间（带时区）。")
    last_active_at: datetime = Field(
        description="最后一次在本会话提问的时间（带时区）；列表按它倒序。",
    )

    model_config = ConfigDict(frozen=True, from_attributes=True)


class AgentThreadListResponse(BaseModel):
    """``GET /agent/threads`` 的响应。

    带 ``total`` 是有意的：offset 分页下前端要显示「共 N 个」和算总页数，而这两件事光有当前页
    的条数算不出来。
    """

    items: tuple[AgentThreadSummary, ...] = Field(
        description="本页会话，按最后活跃时间倒序。",
    )
    total: int = Field(description="当前账号的会话总数，与分页参数无关。")

    model_config = ConfigDict(frozen=True)


class AgentThreadDeletionResponse(BaseModel):
    """``DELETE /agent/threads/{thread_id}`` 的响应。

    为什么删除有响应体而不是 204：这条路由的失败分支（404 归属校验失败、503 数据库不可用）都要带
    ``code``/``detail``/``retryable``，而 FastAPI 不允许给 204 声明任何响应体——真用 204 就只能把
    错误契约从 OpenAPI 里删掉，前端生成的类型里也就看不到这两种失败。项目里 ``DELETE
    /admin/users/{user_id}/sessions`` 出于同样的原因返回 200 加一个小对象。

    回带 ``thread_id`` 而不是空对象 ``{}``：前端可以核对「删掉的确实是我点的那个」，
    这在列表刚刷新过、行序变了的情况下有用。
    """

    thread_id: UUID = Field(description="已删除的会话 id。")

    model_config = ConfigDict(frozen=True)


class AgentReplayTrace(BaseModel):
    """回放出来的一次工具调用轨迹。

    与 SSE 的 ``tool_call``/``tool_result`` 两个事件相比，这里调用和结果已经合成一条：回放时
    两者都是既成事实，没有「已经开始查、还没查完」的中间态。

    配对精度也比流式那边高：checkpointer 里的 ``ToolMessage`` 带 ``tool_call_id``，所以调用与结果
    是精确对应的；而 SSE 的 ``tool_result`` 事件不带 id，前端只能按「同名且还没结果的最早那条」
    近似配对（见 ``frontend/src/features/agent-chat/model/conversation.ts``）。
    """

    tool: str = Field(description="被调用的工具名。")
    arguments: dict[str, object] = Field(
        default_factory=dict,
        repr=False,
        description="模型给出的调用参数；属于展示给用户的调用轨迹，不含服务端凭据。",
    )
    content: str | None = Field(
        default=None,
        repr=False,
        description=(
            "工具返回给模型的文本。为 null 表示历史里只有调用、没有对应结果"
            "（那一轮在工具返回前就中断了）。"
        ),
    )
    failed: bool = Field(
        default=False,
        description="该次调用是否失败；失败时 content 是安全文案，不含异常细节。",
    )

    model_config = ConfigDict(frozen=True)


class AgentReplayTurn(BaseModel):
    """回放出来的一轮问答。

    ``answer`` 可能是空串：首轮运行失败（模型没来得及作答）时，checkpointer 里只有用户那条消息。
    这种情况不伪造一个错误——当时的失败原因没有存下来，编一个出来会误导排查方向。前端显示一句
    中性说明即可。
    """

    question: str = Field(repr=False, description="用户这一轮的提问原文。")
    answer: str = Field(
        repr=False,
        description="模型这一轮的最终回答；空串表示当时没有产出回答。",
    )
    traces: tuple[AgentReplayTrace, ...] = Field(
        default=(),
        description="这一轮里的工具调用轨迹，按发生顺序。",
    )

    model_config = ConfigDict(frozen=True)


class AgentThreadMessagesResponse(BaseModel):
    """``GET /agent/threads/{thread_id}/messages`` 的响应。

    ``summarized`` 与 ``summary`` 一起表达「早期历史已经不在了」这件事，前端必须如实显示，
    不能把回放当成完整历史：``SummarizationMiddleware`` 的压缩是破坏性的，被压掉的原始消息
    真的不在 checkpointer 里了，模型看到的也只是那段摘要。
    """

    thread_id: UUID = Field(description="本次回放所属的会话 id。")
    turns: tuple[AgentReplayTurn, ...] = Field(
        description="按时间顺序的历史轮次；不包含摘要那条伪提问。",
    )
    summarized: bool = Field(
        description="早期历史是否已被压缩成摘要；为真表示 turns 不是全部历史。",
    )
    summary: str | None = Field(
        default=None,
        repr=False,
        description=(
            "压缩后的摘要正文，仅在 summarized 为真时存在。"
            "原样透传，可能带有上游库加的英文前缀。"
        ),
    )

    model_config = ConfigDict(frozen=True)


__all__ = [
    "DEFAULT_THREAD_PAGE_SIZE",
    "MAX_THREAD_PAGE_SIZE",
    "AgentReplayTrace",
    "AgentReplayTurn",
    "AgentThreadDeletionResponse",
    "AgentThreadListResponse",
    "AgentThreadMessagesResponse",
    "AgentThreadSummary",
]
