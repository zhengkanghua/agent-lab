"""Agent 测试共用的假模型、假工具和图装配助手。

抽到这里的原因和 ``auth_helpers.py`` 一样：多个 Agent 测试文件需要同一套「不联网的模型」
和「可数调用次数的工具」，复制两份的话改一处就会两边行为分叉。

本模块不访问网络、不连 PostgreSQL、不碰 Qdrant，也不读 ``.env``。
"""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI
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


async def open_chat_stream(
    app: FastAPI,
    *,
    path: str,
    payload: dict[str, Any],
) -> tuple[asyncio.Task, "asyncio.Queue[dict[str, Any] | None]"]:
    """用真实 ASGI 调用入口发起一次请求，并让事件**边到达边进队列**。

    **为什么不能用 ``httpx.ASGITransport``**：它在返回响应之前就把整个响应体收集完（内部是一个
    列表），所以客户端没法在服务端还在流的时候做别的事。要验证「运行中途请求停止」，那件事恰好就
    必须在服务端还在流的时候做。

    Args:
        app: 已经在 lifespan 内的应用。
        path: 请求路径。
        payload: JSON 请求体。

    Returns:
        ``(任务, 队列)``。队列里是解析后的 SSE 事件对象，发完时收到 ``None``。
        调用方最后要 ``await`` 那个任务，否则未处理的异常会被吞掉。

    Notes:
        这条连接刻意**不**报告断开：它要一直活到服务端把终态事件发完。要测断开请用
        ``disconnect_mid_stream``。
    """

    body = json.dumps(payload).encode("utf-8")
    events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    buffer = ""
    body_delivered = False

    async def send(message: dict[str, Any]) -> None:
        """把响应体分块拼起来、切帧、放进队列。"""

        nonlocal buffer
        if message["type"] != "http.response.body":
            return
        buffer += (message.get("body") or b"").decode("utf-8")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            if frame.startswith("data: "):
                events.put_nowait(json.loads(frame[len("data: ") :]))
        if not message.get("more_body", False):
            events.put_nowait(None)

    async def receive() -> dict[str, Any]:
        """先交出请求体，之后永远不报告断开。"""

        nonlocal body_delivered
        if not body_delivered:
            body_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()
        raise AssertionError("这行不可达")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": _SERVER_SPEC_VERSION},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return asyncio.create_task(app(scope, receive, send)), events


# 生产 ASGI 服务器（uvicorn）声明的协议版本。Starlette 在 ``spec_version >= 2.4`` 时走
# ``http.disconnect`` 消息那条分支，低于 2.4 时走 task group + ``cancel_scope.cancel()``。
# 声明它是为了让断连路径与生产一致：测出来的东西必须是真实跑的那一条。
_SERVER_SPEC_VERSION = "2.3"


async def disconnect_mid_stream(
    app: FastAPI,
    *,
    path: str,
    payload: dict[str, Any],
    frames_before_disconnect: int = 1,
    timeout: float = 30.0,
) -> list[str]:
    """直接以 ASGI 调用入口发起一次请求，在读到若干响应体分块之后回一个断开事件。

    **为什么不能用 ``httpx.ASGITransport`` 造断连**：它要等响应完成才报告断开，响应体也先
    全部收集再一次返回，所以「客户端在流中途断开」这个动作在那个传输上根本不存在。用假传输
    写出来的用例在「运行挂在连接上」的旧实现下也会通过（因为它根本没断），改动失败也不会变红。

    本函数不连数据库、不连向量库、不调真实模型，开销与普通单测相同。

    Args:
        app: 已经在 lifespan 内的应用。
        path: 请求路径。
        payload: JSON 请求体。
        frames_before_disconnect: 收到几个非空响应体分块后断开。
        timeout: 整个调用的上限，只用来防止用例自己挂住。

    Returns:
        断开之前收到的响应体文本，按到达顺序；调用方自己按帧分割。

    Notes:
        断开必须在**响应真的开始出帧之后**才发出：``receive`` 一上来就回 ``http.disconnect``
        的话，Starlette 会在任何事件发出去之前就取消掉响应，那测的是「还没开始就断开」而不是
        「流中途断开」。所以这里用一个事件等到第一个非空分块。
    """

    body = json.dumps(payload).encode("utf-8")
    received: list[str] = []
    body_delivered = False
    started = asyncio.Event()

    async def send(message: dict[str, Any]) -> None:
        """收集响应体分块，并在够数时放行断开。"""

        if message["type"] != "http.response.body":
            return
        chunk = message.get("body") or b""
        if not chunk:
            return
        received.append(chunk.decode("utf-8"))
        if len(received) >= frames_before_disconnect:
            started.set()

    async def receive() -> dict[str, Any]:
        """先交出请求体，之后等到响应开始出帧再报告断开。"""

        nonlocal body_delivered
        if not body_delivered:
            body_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await started.wait()
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": _SERVER_SPEC_VERSION},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout)
    return received


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


def parallel_tool_call_message(calls: Sequence[tuple[str, str, dict[str, Any]]]) -> AIMessage:
    """构造一条「模型一次发起多个工具调用」的消息。

    ``tool_call_message`` 的 id 是按工具名生成的，同一个工具调两次会撞成同一个 id，没法用来
    验证按 id 配对。这里让调用方显式给出每个 id。

    Args:
        calls: ``(tool_call_id, 工具名, 调用参数)`` 三元组序列，按模型给出的顺序。

    Returns:
        带多条 ``tool_calls`` 的 ``AIMessage``；``content`` 为空，与真实 provider 一致。
    """

    return AIMessage(
        content="",
        tool_calls=[
            {"name": tool_name, "args": arguments, "id": call_id}
            for call_id, tool_name, arguments in calls
        ],
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
            tool_names=frozenset(each.name for each in tools),
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
    "disconnect_mid_stream",
    "open_chat_stream",
    "run",
    "tool_call_message",
]
