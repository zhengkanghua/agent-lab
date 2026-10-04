"""定义会话列表与会话历史回放的对外契约（Pydantic 模型）。

本模块位于 HTTP/应用边界的 Pydantic 层，只描述「会话列表长什么样」和「一段历史回放长什么样」；
不读数据库、不调 checkpointer，也不决定归属（那是 ``services.agent_thread_service``）。

回放的形状刻意与 SSE 事件流**不同构**。流式那边是「一串按时间到达的事件」，前端自己攒成一轮一轮；
回放这边已经是既成事实，没有中间态可言，所以直接给「一轮一问一答」的结构，前端灌进界面即可。
让回放也发一串假事件是另一种选择，但那要求前端把状态机重放一遍，多一条只在回放时才走的代码路径。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from agent_lab.agent.evidence import DocumentEvidence
from agent_lab.knowledge.scope import KnowledgeBaseSelection, ResolvedKnowledgeBaseScope
from agent_lab.schemas.llm_models import ResolvedLlmModel


# 会话列表一页的条数上下限。上限 100 与 document_search 的 MAX_DOCUMENT_LIMIT 取同一个数量级，
# 但两者无关联：这里限制的是「一次返回多少个会话」，跟检索结果没有共享语义。
DEFAULT_THREAD_PAGE_SIZE = 20
MAX_THREAD_PAGE_SIZE = 100


class AgentThreadSummary(BaseModel):
    """会话列表里的一行。

    刻意不含消息内容、轮数和「最后一条回答」：那些要么是会话历史表里已有内容的副本，
    要么需要额外维护一个容易飘的计数列。
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


class AgentThreadModel(BaseModel):
    """会话当前保存的模型选择：id 连它此刻在目录里的展示名。

    为什么要带展示名：已停用（或不存）的那个条目**不在可选列表里**，选择器要把「原来是 xxx」
    说清楚就只能从会话侧读回来。展示名为空只有一种情况——目录里已经查不到这个 id；
    ``llm_models`` 没有删除入口，正常到不了这里。

    它**不回答「这个选择现在能不能用」**：可用性是每次现算的（自身启用且所属渠道启用），
    以选择器打开时重新拉的那份目录为准；把这个判断也存进响应里就成了第二个事实源，
    而且它会随响应变陈。
    """

    id: UUID = Field(description="会话当前选择的可用模型 id。")
    display_name: str | None = Field(
        default=None,
        description=(
            "这个 id 此刻在目录里的展示名（条目填过就是它，没填就是上游模型名）；"
            "目录里查不到这一条时为 null，界面只能给出通用提示。"
        ),
    )

    model_config = ConfigDict(frozen=True)


class AgentThreadModelSelection(BaseModel):
    """保存会话模型选择的请求体与响应体，只有 id。

    保存**不校验这个模型当前可不可用**：可用性只在「开始运行之前解析当轮模型」那道门上判，
    否则同一个失效选择会从保存与提问两处各拿到一条不一样的提示。所以把当前已失效的 id 存进
    来照样成功。
    """

    llm_model_id: UUID = Field(description="要记住的可用模型 id；它必须来自选择列表。")

    model_config = ConfigDict(frozen=True)


class AgentReplayTrace(BaseModel):
    """回放出来的一次工具调用轨迹。

    与 SSE 的 ``tool_call``/``tool_result`` 两个事件相比，这里调用和结果已经合成一条：回放时
    两者都是既成事实，没有「已经开始查、还没查完」的中间态。

    配对方式与流式一致：两边都按 ``tool_call_id`` 精确对应，所以同一轮的轨迹在「对话时」和
    「刷新后回放」看到的是同一份。这里合成时 id 已经用掉、不再对外暴露；SSE 那两个事件仍带着它，
    因为前端要靠它把先后到达的调用和结果接起来。
    """

    tool: str = Field(description="被调用的工具名。")
    scope: ResolvedKnowledgeBaseScope | None = None
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
    run_id: UUID | None = None
    scope: ResolvedKnowledgeBaseScope | None = None
    llm_model: ResolvedLlmModel | None = Field(
        default=None,
        description=(
            "这一轮实际使用的模型，取自那一轮运行元数据里的冻结快照：展示名与上下文窗口都是"
            "**当时**的值，条目后来改名或停用不改写已经发生过的那几轮。为 null 表示这一轮没盖下"
            "这个快照。"
        ),
    )
    status: Literal["completed", "incomplete"] = "incomplete"
    citations: tuple[DocumentEvidence, ...] = ()
    invalid_citations: tuple[str, ...] = ()
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

    数据源是业务表 ``agent_thread_messages``（见 ADR 0044），不是 checkpointer：用户看到的记录
    写完不再改，压缩策略怎么改都不影响它。

    ``memory_boundary_run_id`` 是唯一的「早期历史已经不在模型上下文里」的线索：它指向
    「模型只剩摘要」那一段的最后一轮，界面据此画那条分界线。**不回摘要正文**——界面不展示它。
    """

    thread_id: UUID = Field(description="本次回放所属的会话 id。")
    active_run_id: UUID | None = Field(
        default=None,
        description=(
            "当前在途运行的 id；为空表示这个会话没有运行在跑。\n\n"
            "它只有一个用途：刷新页面后前端要知道「上一轮还在跑」——否则会出现自相矛盾的组合："
            "界面显示「这一轮没有留下回答」（在途那一轮还没落表），用户再发一条却被服务端以"
            "「还在生成中」拒绝。"
            "它不是把运行状态暴露给用户看，也不代表运行会出现在 ``turns`` 里。"
        ),
    )
    scope: KnowledgeBaseSelection = Field(description="会话当前保存的选择，不改写历史轮次的实际范围。")
    llm_model: AgentThreadModel | None = Field(
        default=None,
        description=(
            "会话当前保存的模型选择；为 null 表示这个会话没选过模型，提问时用默认模型。"
            "每个轮次实际用的是哪一个，看 `turns[].llm_model`。"
        ),
    )
    turns: tuple[AgentReplayTurn, ...] = Field(
        description="按时间顺序的历史轮次；不包含摘要那条伪提问。",
    )
    memory_boundary_run_id: UUID | None = Field(
        default=None,
        description=(
            "模型只保留了摘要的那一段的最后一轮运行 id：这一轮及其之前的轮次已不在模型上下文里"
            "（界面据此画那条分界线）。为 null 表示历史没有被压缩过。\n\n"
            "同一会话里每次压缩都留下一行摘要，但后来的压缩可能沿用更早那条的标记，所以这一项"
            "取的是**最近**那一行的值。"
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
    "AgentThreadModel",
    "AgentThreadModelSelection",
    "AgentThreadSummary",
]
