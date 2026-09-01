"""把 Agent 对话暴露为 ``POST /agent/chat``（SSE）和 ``GET /agent/default-prompt``。

本模块位于 FastAPI 边界层，只做四件事：取依赖、生成 ``thread_id``、把事件序列化成 SSE
行格式、在模型沉默期间发心跳。它不调用模型、不决定事件顺序、不分类异常——那些在
``agent/streaming.py``；也不判断错误文案，那在 ``api/error_contract.py``。

**为什么是 POST 而不是 GET**：浏览器原生的 ``EventSource`` 只能发 GET、不能带请求体，
而提问和自定义提示词都可能超过 URL 长度限制，也不该出现在访问日志的 URL 里。所以前端
改用 ``fetch`` + ``ReadableStream`` 自己解析，见 ``frontend/src/api/agent-chat.ts``。

**为什么 SSE 而不是 WebSocket**：这条链路是单向的（服务端推、客户端只在开头说一句话），
SSE 走普通 HTTP，能直接复用现有的 Cookie 认证、反向代理和错误契约；WebSocket 要另配
一套升级握手和鉴权。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from agent_lab.agent.context import AgentContext
from agent_lab.agent.limits import SSE_HEARTBEAT_INTERVAL_SECONDS
from agent_lab.agent.prompts import DEFAULT_SYSTEM_PROMPT
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.agent.streaming import stream_agent_events
from agent_lab.api.dependencies import get_agent_runtime, get_agent_thread_service
from agent_lab.auth.dependencies import current_superuser
from agent_lab.config.llm import LangSmithSettings, get_langsmith_settings
from agent_lab.models.user import UserRecord
from agent_lab.services.agent_thread_service import AgentThreadService
from agent_lab.schemas.agent_chat import (
    AgentChatEvent,
    AgentChatEventEnvelope,
    AgentChatRequest,
    AgentDefaultPromptResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])

# SSE 的行格式：每个事件是 ``data: <一行 JSON>``，以空行结束。这里刻意不使用 SSE 的
# ``event:`` 字段名——判别信息已经在 JSON 的 ``event`` 键里，写两遍会出现「两个真源」，
# 而且前端用 fetch 手工解析时读 JSON 比读 SSE 字段更直接。
_SSE_DATA_PREFIX = "data: "
_SSE_EVENT_SUFFIX = "\n\n"

# 心跳用 SSE 注释行（以冒号开头）：它是协议里合法的「什么都不做」的帧，客户端解析器会
# 忽略它，所以不必在前端为心跳写分支，也不会混进事件流。
_SSE_HEARTBEAT = ": keep-alive\n\n"


class ServerSentEventResponse(StreamingResponse):
    """媒体类型固定为 ``text/event-stream`` 的流式响应。

    为什么要有这个子类而不是每次传 ``media_type=``：FastAPI 生成 OpenAPI 时，把
    ``responses`` 里声明的模型挂到 ``response_class.media_type`` 这个键下面。直接传
    ``media_type`` 参数只影响真实响应头，不影响文档，结果是文档里事件 schema 被挂到
    ``application/json`` 上——那是错的，这个接口从不返回 JSON 响应体。
    声明成类之后，运行时响应头和 OpenAPI 的 content key 来自同一个常量。
    """

    media_type = "text/event-stream"


def _encode(event: AgentChatEvent) -> str:
    """把一个事件序列化成一帧 SSE 文本。

    Args:
        event: 已构造好的事件模型。

    Returns:
        ``data: {...}\\n\\n`` 形式的一帧。

    Notes:
        纯内存转换。用 ``AgentChatEventEnvelope`` 而不是直接 ``event.model_dump_json()``：
        走信封才能保证发出去的 JSON 与 OpenAPI 里那个可判别联合是同一套 schema，前端
        生成的 TS 类型才真的对得上。
    """

    payload = AgentChatEventEnvelope(root=event).model_dump_json()
    return f"{_SSE_DATA_PREFIX}{payload}{_SSE_EVENT_SUFFIX}"


async def _stream_with_heartbeat(
    runtime: AgentRuntime,
    *,
    message: str,
    thread_id: UUID,
    context: AgentContext,
    langsmith_settings: LangSmithSettings,
) -> AsyncIterator[str]:
    """把事件流转成 SSE 帧，并在长时间没有事件时插入心跳。

    为什么需要心跳：模型「想」的时候可能十几秒不产出任何 token，而这条链路上每一跳
    （浏览器、Vite 开发代理、Nginx、Cloudflare）都有自己的空闲超时。一个字节都不发的
    连接会被中间任何一环判定为死连接掐掉，用户看到的是「刚问完就断了」。

    Args:
        runtime: 进程级 Agent Runtime。
        message: 用户提问。
        thread_id: 本轮所属会话 id。
        context: 本次运行的上下文。
        langsmith_settings: 追踪开关与凭据。

    Yields:
        SSE 帧字符串，包括心跳注释行。

    Notes:
        执行模型、Qdrant、PostgreSQL 的读 I/O（都在 ``stream_agent_events`` 内部）。
        本函数自己只做超时等待、字符串拼接和断连清理。已分类的失败不会从这里抛出——
        ``stream_agent_events`` 已经把它们转成 ``error`` 事件了。
    """

    # 1、拿到事件流，并手工取它的迭代器。手工取是因为下面要「等一下、没等到就干点别的、
    #    再回来接着等同一个事件」，普通 async for 做不到这件事。
    events = stream_agent_events(
        runtime.graph,
        message=message,
        thread_id=thread_id,
        context=context,
        langsmith_settings=langsmith_settings,
    )
    iterator = events.__aiter__()
    pending: asyncio.Task[Any] | None = None
    try:
        while True:
            # 2、还没有在等的事件，就发起一次「取下一个事件」。
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            # 3、最多等一个心跳间隔。用 wait 而不是 wait_for：wait 超时后**不取消**任务，
            #    所以下一轮还能接着等同一次 __anext__。wait_for 会把它取消掉，等于每发
            #    一次心跳就丢一个正在生成的事件。
            done, _ = await asyncio.wait(
                {pending},
                timeout=SSE_HEARTBEAT_INTERVAL_SECONDS,
            )
            # 4、超时没等到 → 发一帧心跳占住连接，回去接着等那个还没完成的任务。
            if not done:
                yield _SSE_HEARTBEAT
                continue
            # 5、等到了 → 取结果。StopAsyncIteration 表示流正常结束。
            finished, pending = pending, None
            try:
                event = finished.result()
            except StopAsyncIteration:
                return
            yield _encode(event)
    finally:
        # 6、收尾，正常结束和客户端中途断开都会走到这里。断开时 ASGI 服务器会 aclose
        #    本生成器，控制流从上面某个 yield 直接跳过来；此时那次 __anext__ 可能还在等
        #    模型响应，不收拾就会变成一个没人接收结果的悬空任务，模型连接也不释放。
        if pending is not None:
            pending.cancel()
            # 必须等它真的结束再关：__anext__ 还在跑的时候 aclose() 会直接 RuntimeError。
            with suppress(asyncio.CancelledError, Exception):
                await pending
        with suppress(asyncio.CancelledError, Exception):
            await events.aclose()


@router.post(
    "/chat",
    status_code=status.HTTP_200_OK,
    response_class=ServerSentEventResponse,
    summary="与新闻 Agent 对话（SSE 流式返回）",
    description=(
        "发起一次 Agent 运行。模型自行决定是否调用只读检索工具，过程以 "
        "text/event-stream 逐事件返回：token 是回答增量，tool_call/tool_result 是"
        "调用轨迹，done 或 error 是最后一个事件。带上 thread_id 即接着上一轮聊。\n\n"
        "响应体不是一个 JSON 文档，而是一串 SSE 帧，每帧形如 `data: {...}`；下面这个 "
        "schema 描述的是**单帧里那个 JSON 对象**，按 `event` 字段判别。\n\n"
        "注意：流一旦开始，HTTP 状态码就固定为 200——响应头在第一个事件发出时已经送出，"
        "之后的失败只能作为 error 事件送达，不会改变状态码。"
    ),
    responses={
        status.HTTP_200_OK: {
            "model": AgentChatEventEnvelope,
            "description": "SSE 流；schema 描述单帧 `data:` 后面的那个 JSON 对象。",
        },
    },
)
async def agent_chat(
    chat_request: AgentChatRequest,
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
    langsmith_settings: Annotated[LangSmithSettings, Depends(get_langsmith_settings)],
    user: Annotated[UserRecord, Depends(current_superuser)],
    threads: Annotated[AgentThreadService, Depends(get_agent_thread_service)],
) -> ServerSentEventResponse:
    """启动一次 Agent 运行并以 SSE 返回全过程。

    ``thread_id`` 带上就接着聊，但**必须是自己的会话**：checkpointer 只按 id 取历史、不校验归属，
    所以归属由 ``AgentThreadService`` 在这里挡住。缺省时由服务端生成新 id 并落一行归属记录，
    新 id 通过 ``done`` 事件返回。

    权限门在 ``main.py`` 的 ``include_router`` 上已经挂了一道，这里再声明一次 ``current_superuser``
    不是重复：那道只做「拦住没权限的人」，这里要的是**当前账号对象**本身，用来判定会话归属。

    Args:
        chat_request: 提问、可选会话 id 和可选自定义系统提示词。
        runtime: 进程级 Agent Runtime，由 lifespan 装配。
        langsmith_settings: 追踪配置，进程级缓存。
        user: 当前登录账号，用于会话归属。
        threads: 会话归属与列表 Service。

    Returns:
        ``text/event-stream`` 流式响应。返回它时流还没开始跑——第一次迭代发生在 ASGI
        服务器读生成器的时候，所以此刻抛出的异常还能变成正常的 HTTP 错误码。

    Raises:
        AgentThreadNotFoundError: ``thread_id`` 不存在或不属于当前账号；映射成 404。
            **它必须在流开始之前抛出**，那时候还改得动 HTTP 状态码。

    Notes:
        本接口会执行模型 HTTP 调用、Qdrant 查询、PostgreSQL 读取和会话历史读写。业务数据方面
        只写 ``agent_threads``（归属与活跃时间），不写新闻和索引（见 ADR 0003）。已分类的失败
        以 ``error`` 事件送达而非 HTTP 错误码，原因见上面 description。
    """

    # 1、定会话 id 并确认归属。这一步刻意在返回流式响应**之前**完成：它内部开一个短事务、
    #    提交后立刻归还连接，所以长对话不会占着业务连接池不放（见 ADR 0010）。
    thread_id = await threads.ensure_thread(
        user_id=user.id,
        thread_id=chat_request.thread_id,
        first_message=chat_request.message,
    )
    # 2、把自定义提示词装进本次运行的上下文；为 None 时中间件会用默认那份。
    context = AgentContext(system_prompt=chat_request.system_prompt)
    # 3、只记 id 和「有没有自定义提示词」，不记提问原文——日志里不该有用户输入。
    logger.info(
        "Agent 对话开始 thread_id=%s custom_prompt=%s",
        thread_id,
        chat_request.system_prompt is not None,
    )
    # 4、交出流式响应。注意此刻流还没开始跑，第一次迭代发生在 ASGI 服务器读生成器时。
    return ServerSentEventResponse(
        _stream_with_heartbeat(
            runtime,
            message=chat_request.message,
            thread_id=thread_id,
            context=context,
            langsmith_settings=langsmith_settings,
        ),
        headers={
            # 反向代理和浏览器都可能为了「效率」把小块响应攒起来一起发，那会让打字机
            # 效果变成「等半天，然后整段出现」。这三个头是分别对 Nginx、HTTP 缓存和
            # 长连接说「别攒、别缓存、别关」。
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/default-prompt",
    response_model=AgentDefaultPromptResponse,
    status_code=status.HTTP_200_OK,
    summary="读取服务端内置的默认系统提示词",
    description=(
        "返回不传 system_prompt 时实际生效的那份提示词，供前端预填到编辑框里。"
        "它是常量，不随会话变化。"
    ),
)
async def agent_default_prompt() -> AgentDefaultPromptResponse:
    """返回默认系统提示词。

    为什么要有这个接口：自定义提示词的输入框如果一开始是空的，用户只能从零写一份，
    大概率写出比默认版更差的（漏掉引用要求、漏掉「不知道就说不知道」）。预填默认值让
    「微调」成为默认动作，「重写」成为主动选择。

    Returns:
        含默认提示词全文的响应。

    Notes:
        返回进程内常量，不执行任何 I/O，也不读取会话状态。
    """

    return AgentDefaultPromptResponse(system_prompt=DEFAULT_SYSTEM_PROMPT)


__all__ = ["router"]
