"""Agent 测试共用的假模型、假工具和图装配助手。

抽到这里的原因和 ``auth_helpers.py`` 一样：多个 Agent 测试文件需要同一套「不联网的模型」
和「可数调用次数的工具」，复制两份的话改一处就会两边行为分叉。

本模块不访问网络、不连 PostgreSQL、不碰 Qdrant，也不读 ``.env``。
"""

import asyncio
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
    GenericFakeChatModel,
)
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver

from agent_lab.agent.context import AgentContext
from agent_lab.agent.middleware import build_agent_middleware
from agent_lab.config.llm import LangSmithSettings
from langchain.agents import create_agent


# 关闭追踪、无凭据。所有测试共用一份，确保没有测试会意外向 LangSmith 上报。
OFFLINE_LANGSMITH_SETTINGS = LangSmithSettings(
    tracing=False,
    api_key="",
    project="offline-test",
)


def run(coroutine: Any) -> Any:
    """执行一个测试协程，保持测试环境不依赖 pytest-asyncio。"""

    return asyncio.run(coroutine)


class ScriptedChatModel(FakeMessagesListChatModel):
    """按脚本依次返回预置消息的假模型，并记录被调用次数。

    为什么要自己写 ``bind_tools``：``FakeMessagesListChatModel`` 的默认实现直接抛
    ``NotImplementedError``，而 ``create_agent`` 一定会调它去绑定工具 schema。返回
    ``self`` 表示「我知道有哪些工具，但我的回答是脚本写死的」——这正是离线测试需要的：
    工具调用由脚本决定，不受模型能力影响。

    ``call_count`` 用来断言重试和降级真的发生了。只断言最终消息不够：ADR 0005 记录的
    那个顺序 bug 在两种顺序下最终消息完全相同，只有调用次数能区分。

    ``received_messages`` 记录每次调用收到的完整消息列表，用来断言系统提示词、历史压缩
    这类「改写请求」的中间件真的改到了模型看见的东西。
    """

    call_count: int = 0
    received_messages: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        """接受工具绑定但不改变脚本行为。"""

        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        """记下这次收到的消息，然后交给父类按脚本取下一条回复。"""

        self.call_count += 1
        self.received_messages = [*self.received_messages, list(messages)]
        return super()._generate(messages, *args, **kwargs)


class StreamingChatModel(GenericFakeChatModel):
    """逐 token 产出的假模型，用来验证流式路径。

    与 ``ScriptedChatModel`` 的区别是它走 ``_stream``，产出 ``AIMessageChunk``；
    真实 provider 也走这条路径。
    """

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        """接受工具绑定但不改变脚本行为。"""

        return self


class FailingChatModel(BaseChatModel):
    """固定抛出预置异常的假模型，用来验证错误分类和降级。

    刻意不继承 ``FakeMessagesListChatModel``：那个类需要一份 responses 脚本，而这里的
    语义是「永远失败」，给脚本反而让人以为它有时会成功。
    """

    error: BaseException
    call_count: int = 0

    @property
    def _llm_type(self) -> str:
        """LangChain 要求的模型类型标识。"""

        return "failing-fake"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BaseChatModel:
        """接受工具绑定但不改变失败行为。"""

        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        """记一次调用后抛出预置异常。"""

        self.call_count += 1
        raise self.error


def tool_call_message(tool_name: str, arguments: dict[str, Any]) -> AIMessage:
    """构造一条「模型决定调用某工具」的消息。

    Args:
        tool_name: 要调用的工具名。
        arguments: 调用参数。

    Returns:
        带 ``tool_calls`` 的 ``AIMessage``；``content`` 为空，与真实 provider 一致。
    """

    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": arguments, "id": f"call-{tool_name}"}],
    )


class CountingTool:
    """记录调用次数的假工具工厂。

    存在的意义是给「重试真的重试了吗」提供可断言的证据：``invocations`` 是唯一能区分
    「重试中间件在内层（调 3 次）」和「在外层（调 1 次）」的信号。
    """

    def __init__(
        self,
        name: str,
        *,
        result: str = "ok",
        error: BaseException | None = None,
    ) -> None:
        self.name = name
        self.result = result
        self.error = error
        self.invocations: list[dict[str, Any]] = []

    def build(self) -> BaseTool:
        """构造一个记录调用并按配置成功或失败的 LangChain 工具。"""

        counter = self

        @tool(self.name)
        async def _counting_tool(text: str) -> str:
            """假工具：记录调用，然后按配置返回结果或抛异常。

            Args:
                text: 任意输入；只用于记录，不参与逻辑。

            Returns:
                预置的成功文案。

            Raises:
                BaseException: 构造时配置了 ``error`` 就抛它。
            """

            counter.invocations.append({"text": text})
            if counter.error is not None:
                raise counter.error
            return counter.result

        return _counting_tool


def build_offline_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool] = (),
    *,
    fallback_model: BaseChatModel | None = None,
    retry_initial_delay: float = 0.0,
) -> Any:
    """用真实中间件流水线装配一个不联网的图。

    刻意走 ``build_agent_middleware`` 而不是裸 ``create_agent``：中间件顺序正是要被测
    的东西，绕过它测出来的东西没有意义。

    Args:
        model: 主模型（假的）。
        tools: 要挂上的工具。
        fallback_model: 备用模型；省略时复用主模型。
        retry_initial_delay: 重试退避秒数，默认 ``0.0``——测试不需要真的等。生产默认是
            1 秒且指数翻倍，按那个值跑，「主备模型都失败」一条用例就要白等 6 秒纯 sleep，
            而这些用例断言的是「重试了几次、顺序对不对」，跟等多久无关。要专门验证退避
            时长的话显式传一个非零值。

    Returns:
        已编译的图，会话历史存在 ``InMemorySaver`` 里。

    Notes:
        不访问网络、PostgreSQL 或 Qdrant。用 ``InMemorySaver`` 而非 PostgreSQL
        checkpointer，因此不需要建表（见 ADR 0004）。
    """

    return create_agent(
        model,
        tools=list(tools),
        middleware=build_agent_middleware(
            fallback_model=fallback_model or model,
            summarization_model=model,
            retry_initial_delay=retry_initial_delay,
        ),
        context_schema=AgentContext,
        checkpointer=InMemorySaver(),
    )


__all__ = [
    "OFFLINE_LANGSMITH_SETTINGS",
    "CountingTool",
    "FailingChatModel",
    "ScriptedChatModel",
    "StreamingChatModel",
    "build_offline_graph",
    "run",
    "tool_call_message",
]
