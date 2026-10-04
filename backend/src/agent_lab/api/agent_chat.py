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
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.agent.context import AgentContext
from agent_lab.agent.limits import SSE_HEARTBEAT_INTERVAL_SECONDS
from agent_lab.agent.prompts import DEFAULT_SYSTEM_PROMPT
from agent_lab.agent.runs import AgentRun, AgentRunRegistry
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.api.dependencies import (
    get_agent_run_registry,
    get_agent_runtime,
    get_agent_thread_service,
    get_llm_model_selection_service,
    get_vector_search_service,
)
from agent_lab.api.error_contract import build_agent_chat_error_response
from agent_lab.auth.dependencies import current_active_user
from agent_lab.config.llm import LangSmithSettings, get_langsmith_settings
from agent_lab.models.user import UserRecord
from agent_lab.services.agent_thread_service import AgentThreadService
from agent_lab.services.llm_model_selection_service import LlmModelSelectionService
from agent_lab.services.vector_search_service import VectorSearchService
from agent_lab.knowledge.scope import KnowledgeBaseSelection
from agent_lab.schemas.agent_chat import (
    AgentChatErrorResponse,
    AgentChatEvent,
    AgentChatEventEnvelope,
    AgentChatRequest,
    AgentDefaultPromptResponse,
    AgentDoneEvent,
    AgentErrorEvent,
    AgentRunStopRequest,
    AgentRunStopResponse,
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


async def _stream_run(run: AgentRun) -> AsyncIterator[str]:
    """把一次运行的事件流转成 SSE 帧，并在长时间没有事件时插入心跳。

    **本生成器只是一个订阅者。** 事件不是从它里面从 LangGraph 拉出来的，而是从 ``run`` 的队列
    里取的；它被取消（浏览器断开）只意味着少了一个看的人，那次运行自己在后台照旧跑完。这是本
    次改动与旧实现最本质的区别，也是 ADR 0035 的全部要点。

    为什么需要心跳：模型「想」的时候可能十几秒不产出任何 token，而这条链路上每一跳
    （浏览器、Vite 开发代理、Nginx、Cloudflare）都有自己的空闲超时。一个字节都不发的
    连接会被中间任何一环判定为死连接掐掉，用户看到的是「刚问完就断了」。

    Args:
        run: 正在跑的那次运行。

    Yields:
        SSE 帧字符串，包括心跳注释行。

    Notes:
        本函数只做超时等待、字符串拼接和退订。模型、Qdrant、PostgreSQL 的 I/O 都在驱动的
        那个任务里，与本生成器的生命周期无关。
    """

    queue = run.subscribe()
    # 同样手工取迭代器：要「等一下、没等到就发帧心跳、再回来接着等同一个事件」。
    pending: asyncio.Task[Any] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(queue.get())
            # 用 wait 而不是 wait_for：wait 超时后不取消任务，下一轮接着等同一次取值；
            # wait_for 会把它取消掉，等于每发一次心跳就丢一个已经到达的事件。
            done, _ = await asyncio.wait(
                {pending},
                timeout=SSE_HEARTBEAT_INTERVAL_SECONDS,
            )
            if not done:
                yield _SSE_HEARTBEAT
                continue
            finished, pending = pending, None
            event = finished.result()
            # None 是驱动者发的结束标记（运行收尾但没发出终态事件的保险）。
            if event is None:
                return
            yield _encode(event)
            if isinstance(event, (AgentDoneEvent, AgentErrorEvent)):
                return
    finally:
        # 收尾：正常结束和客户端中途断开都走到这里。断开这条路上，ASGI 服务器不会「正常
        # 关闭」本生成器，而是取消它——uvicorn 声明 ASGI ``spec_version: 2.3``，低于
        # Starlette 走 ``http.disconnect`` 消息那条分支的 2.4，于是取消作用域直接 cancel，
        # 控制流从上面某个 yield 带着 CancelledError 跳过来。下次读那段注释时不要再写成
        # 「aclose 本生成器」：那时本生成器已经在等队列取值，不取消就会留一个悬空任务。
        run.unsubscribe(queue)
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pending


@router.post(
    "/chat",
    status_code=status.HTTP_200_OK,
    response_class=ServerSentEventResponse,
    summary="与知识库 Agent 对话（SSE 流式返回）",
    description=(
        "发起一次 Agent 运行。模型自行决定是否调用只读检索工具，过程以 "
        "text/event-stream 逐事件返回：token 是回答增量，tool_call/tool_result 是"
        "调用轨迹，done 或 error 是最后一个事件。带上 thread_id 即接着上一轮聊。\n\n"
        "响应体不是一个 JSON 文档，而是一串 SSE 帧，每帧形如 `data: {...}`；下面这个 "
        "schema 描述的是**单帧里那个 JSON 对象**，按 `event` 字段判别。\n\n"
        "注意：流一旦开始，HTTP 状态码就固定为 200——响应头在第一个事件发出时已经送出，"
        "之后的失败只能作为 error 事件送达，不会改变状态码。\n\n"
        "**这条连接只是本次运行的一个订阅者。** 浏览器断开（关页面、断网、切走会话）不会取消"
        "这次运行：它照旧跑完，结果落进会话历史，下次打开这个会话能读到完整答案。真的要不做"
        "这次回答了，用 `POST /agent/stop` 停它。"
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
    user: Annotated[UserRecord, Depends(current_active_user)],
    threads: Annotated[AgentThreadService, Depends(get_agent_thread_service)],
    runs: Annotated[AgentRunRegistry, Depends(get_agent_run_registry)],
    search: Annotated[VectorSearchService, Depends(get_vector_search_service)],
    models: Annotated[LlmModelSelectionService, Depends(get_llm_model_selection_service)],
) -> ServerSentEventResponse:
    """启动一次 Agent 运行并以 SSE 返回全过程。

    ``thread_id`` 带上就接着聊，但**必须是自己的会话**：checkpointer 只按 id 取历史、不校验归属，
    所以归属由 ``AgentThreadService`` 在这里挡住。缺省时由服务端生成新 id 并落一行归属记录，
    新 id 通过 ``done`` 事件返回。

    权限门在 ``main.py`` 的 ``include_router`` 上已经挂了一道，这里再声明一次 ``current_active_user``
    不是重复：那道只做「拦住没登录的人」，这里要的是**当前账号对象**本身，用来判定会话归属。

    系统提示词不再由请求体携带：它取自会话（新建时由 ``ensure_thread`` 从该账号个人偏好拍快照，
    续聊时沿用会话里那份），因此同一会话内前后一致，且用户在设置页改提示词只影响新开的会话。

    **当轮用哪个模型在开始运行之前定下**：请求里携带的那一份优先，没携带才用会话行上存的。
    两者都不存在就用目录里标着默认的那个。解析结果（含它的上下文窗口）进 ``AgentContext``，
    也冻结进提问消息，供回放与替部署接手的那一轮读回来。

    Args:
        chat_request: 提问、可选会话 id、可选会话知识库选择与可选模型 id。
        runtime: 进程级 Agent Runtime，由 lifespan 装配。
        langsmith_settings: 追踪配置，进程级缓存。
        user: 当前登录账号，用于会话归属。
        threads: 会话归属与列表 Service。
        runs: 进程级运行注册表；运行由它驱动，本接口只是它的一次订阅。
        models: 「解析当轮模型」的 Service（读目录表，与后台管理那份分开）。

    Returns:
        ``text/event-stream`` 流式响应。返回它时运行已经开始了——本接口在返回响应之前就把
        运行交给注册表，所以「同意受理」和「开始跑」是同一个动作；此刻抛出的异常还能变成
        正常的 HTTP 错误码。

    Raises:
        AgentThreadNotFoundError: ``thread_id`` 不存在或不属于当前账号；映射成 404。
            **它必须在运行开始之前抛出**，那时候还改得动 HTTP 状态码。
        AgentRunInProgressError: 这个会话已经有一次运行在跑；映射成 409 ``agent_run_in_progress``。
            同一个会话同时只允许一次运行（见 ADR 0037），占位与这个判断是同一次条件写入。
        LlmModelNotFoundError: 当轮生效的模型 id 在目录里不存在；映射成 404。
        LlmModelUnavailableError: 那一条已停用（含所属渠道停用）；映射成 409。
        NoAvailableLlmModelsError: 会话没选模型、而目录里一个可用模型都没有；映射成 409。
            三条都由应用级 handler 翻成 HTTP 响应，同样**在开始运行之前**，所以模型一次都不会
            被调用。

    Notes:
        本接口会执行模型 HTTP 调用、Qdrant 查询、PostgreSQL 读取和会话历史读写。业务数据方面
        只写 ``agent_threads``（归属与活跃时间），不写新闻和索引（见 ADR 0003）。已分类的失败
        以 ``error`` 事件送达而非 HTTP 错误码，原因见上面 description。
    """

    # 2、定下本次运行的标识。会话行上的在途运行 id、停止接口的比对、上下文，以及运行被中断时
    #    补写的消息，记的都是这一个值。由服务端生成、不由客户端提交：客户端提交的运行 id 没有
    #    地方可核验，而停止接口正是靠比对这个 id 才敢写停止标志。
    run_id = uuid4()
    # 3、确定会话并**原子地占下这次运行的位**。这一步刻意在返回运行之前完成：它内部开一个短
    #    事务、提交后立刻归还连接，所以长对话不会占着业务连接池不放（见 ADR 0010）。占位失败
    #    时（同一个会话已经有运行在跑）拿到的是 409，不是「先查后写」的竞态。
    selection = chat_request.scope
    # 会话行上存着的那份模型选择（续聊才读得到）。它是当轮的**退路**：请求里没带模型时才用它。
    saved_model_id = None
    if chat_request.thread_id is not None:
        owned = await threads.get_owned_thread(user_id=user.id, thread_id=chat_request.thread_id)
        if selection is None:
            selection = KnowledgeBaseSelection.model_validate(owned.scope)
        saved_model_id = owned.llm_model_id
    selection = selection or KnowledgeBaseSelection(mode="all")
    resolved_scope = await search.resolve_scope(selection)
    # 4、解析这一轮实际生效的模型，只校验生效的那一份：请求里给了就用请求的，没给才用会话
    #    里存的。它与上面那一步同处一条路——失败走 HTTP 状态码（不是流里的事件），而且发生在
    #    **开始运行之前**，所以那一次模型调用一次都不会发出。可用性直接读目录表，不经过客户端
    #    缓存，刚停用的模型立刻就不能被选中。
    effective_model_id = chat_request.llm_model_id or saved_model_id
    resolved_model = await models.resolve_for_run(effective_model_id)
    # 提示词取自**会话**而不是请求体：新建的会话刚用账号偏好拍下快照，续聊的用会话里那份。
    # 值仍在 ensure_thread 一处取，调用方不再自己读偏好表，免得出现第二个取值点。
    # 请求里带的那份模型**写回会话行**（与范围同一条规则：改了就是记住，没有「只对这一次」
    # 的单次覆盖）；没带就不碰它，会话行上存的是什么就继续是什么。
    thread_id, session_prompt = await threads.ensure_thread(
        user_id=user.id,
        thread_id=chat_request.thread_id,
        first_message=chat_request.message,
        run_id=run_id,
        scope=selection,
        llm_model_id=chat_request.llm_model_id,
    )
    # 2、把会话提示词装进本次运行的上下文；为 None 时中间件会用默认那份。
    #    账号与会话标识一起带上：用量采集点从当前运行的上下文读这两个值，三条查询接口都按
    #    账号过滤，读不到就谁都查不到这笔消耗。运行标识在这里显式传入，同一次运行里的多次
    #    模型调用（含中间件内部的历史摘要压缩调用）拿到的是同一个。
    context = AgentContext(
        run_id=run_id,
        system_prompt=session_prompt,
        scope=resolved_scope,
        llm_model=resolved_model,
        user_id=user.id,
        thread_id=thread_id,
    )
    # 4、只记 id 和「有没有自定义提示词」，不记提问原文——日志里不该有用户输入。
    logger.info(
        "Agent 对话开始 thread_id=%s run_id=%s custom_prompt=%s",
        thread_id,
        run_id,
        session_prompt is not None,
    )
    # 5、把运行交给注册表，然后在后台开始驱动；本接口拿到的是它的订阅入口。
    #    顺序很重要：运行先启动，HTTP 响应只是后来的订阅者，所以即使下面这行响应还没被消费
    #    （甚至浏览器根本没能连上），这次运行也会跑完并把结果写进会话。
    run = runs.begin(
        graph=runtime.graph,
        thread_id=thread_id,
        run_id=run_id,
        message=chat_request.message,
        context=context,
        langsmith_settings=langsmith_settings,
    )
    return ServerSentEventResponse(
        _stream_run(run),
        headers={
            # 反向代理和浏览器都可能为了「效率」把小块响应攒起来一起发，那会让打字机
            # 效果变成「等半天，然后整段出现」。这三个头是分别对 Nginx、HTTP 缓存和
            # 长连接说「别攒、别缓存、别关」。
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/stop",
    response_model=AgentRunStopResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": AgentChatErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AgentChatErrorResponse},
    },
    summary="停下某一次正在跑的 Agent 运行",
    description=(
        "请求服务端停下 ``run_id`` 指定的那一次运行。响应只表示**已受理**：真正停下它的是"
        "运行所在进程的驱动者，最多一个轮询周期之后才开始收尾。调用方要保持原连接等它的终态"
        "事件，不要在本地自建结局。\n\n"
        "``run_id`` 取自该会话 ``run_started`` 事件。它与当前在途运行的 id 不相等时一律幂等"
        "返回成功（视为「没有要停的运行」），因此重复调用与迟到的停止都不会误伤新一次运行。\n\n"
        "会话不存在或不属于当前账号时返回 404（与读会话历史同一形状）。"
    ),
)
async def agent_stop_run(
    stop_request: AgentRunStopRequest,
    user: Annotated[UserRecord, Depends(current_active_user)],
    threads: Annotated[AgentThreadService, Depends(get_agent_thread_service)],
) -> AgentRunStopResponse | JSONResponse:
    """受理一次停止请求。

    为什么这个接口必须存在（见 ADR 0035）：运行从浏览器连接上拆下来之后，原先那条「中止本地请求」
    的路对服务端不再有任何作用——断开只减少订阅者。没有它，停止就是假的：界面看起来停了，模型继续
    烧钱。

    Args:
        stop_request: 会话 id 与运行 id。
        user: 当前登录账号，用于归属校验。
        threads: 会话 Service；停止标志写在会话行上，所以跨进程可见。

    Returns:
        回带两个 id 的受理响应；业务库不可用时返回稳定的 503 JSON。

    Raises:
        AgentThreadNotFoundError: 会话不存在或不属于当前账号；由 handler 映射成 404。

    Notes:
        执行一次 PostgreSQL 写入并提交。**不直接取消任何运行**：请求可能落在另一个进程上。
    """

    try:
        await threads.get_owned_thread(user_id=user.id, thread_id=stop_request.thread_id)
        await threads.request_stop(
            thread_id=stop_request.thread_id,
            run_id=stop_request.run_id,
        )
    except SQLAlchemyError as error:
        # 只读异常类型，不读异常文本：它可能带连接串。
        logger.error("停止运行失败 error_type=%s", type(error).__name__)
        return build_agent_chat_error_response(error)
    # 只记两个 id，不记提问内容。
    logger.info(
        "受理停止请求 thread_id=%s run_id=%s",
        stop_request.thread_id,
        stop_request.run_id,
    )
    return AgentRunStopResponse(
        thread_id=stop_request.thread_id,
        run_id=stop_request.run_id,
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
