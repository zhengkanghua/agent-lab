"""把会话记录暴露为 ``GET /agent/threads``、``GET /agent/threads/{id}/messages`` 和
``DELETE /agent/threads/{id}``。

本模块位于 FastAPI 边界层，只做四件事：取依赖、校验分页参数、把归属校验交给
``AgentThreadService``、把历史翻译交给 ``agent/replay.py``。它不判断归属规则、不解析消息结构，
也不决定错误文案（那在 ``api/error_contract.py``）。

三条路由的共同前提是**归属**：每条都先确认目标会话属于当前账号，不属于就 404。这条前提只有一处
实现（``AgentThreadService.get_owned_thread``），路由不自己写 where 条件。

``AgentThreadNotFoundError`` 不在这里 catch：它是 ``AgentError`` 的子类，``main.py`` 注册在基类上
的 handler 会把它映射成 404。``SQLAlchemyError`` 不是 ``AgentError``，所以要显式 catch，做法与
``api/user_admin.py`` 一致。
"""

import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.agent.errors import AgentThreadNotFoundError
from agent_lab.agent.limits import (
    DELETE_RUNNING_THREAD_POLL_INTERVAL_SECONDS,
    DELETE_RUNNING_THREAD_WAIT_SECONDS,
)
from agent_lab.agent.replay import build_replay_turns
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.api.dependencies import get_agent_runtime, get_agent_thread_service, get_vector_search_service
from agent_lab.api.error_contract import build_agent_chat_error_response
from agent_lab.auth.dependencies import current_active_user
from agent_lab.models.user import UserRecord
from agent_lab.schemas.agent_chat import AgentChatErrorResponse
from agent_lab.schemas.agent_thread import (
    DEFAULT_THREAD_PAGE_SIZE,
    MAX_THREAD_PAGE_SIZE,
    AgentThreadDeletionResponse,
    AgentThreadListResponse,
    AgentThreadMessagesResponse,
    AgentThreadSummary,
)
from agent_lab.services.agent_thread_service import AgentThreadService
from agent_lab.services.vector_search_service import VectorSearchService
from agent_lab.knowledge.scope import KnowledgeBaseSelection


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent/threads", tags=["agent"])


@router.get(
    "",
    response_model=AgentThreadListResponse,
    status_code=status.HTTP_200_OK,
    responses={503: {"model": AgentChatErrorResponse}},
    summary="列出当前账号的 Agent 会话",
    description=(
        "按最后活跃时间倒序分页返回当前账号的会话。只返回自己的会话，"
        "`total` 是不受分页影响的总数，供界面显示总量和算页数。"
    ),
)
async def list_agent_threads(
    user: Annotated[UserRecord, Depends(current_active_user)],
    threads: Annotated[AgentThreadService, Depends(get_agent_thread_service)],
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_THREAD_PAGE_SIZE,
            description="本页最多返回几个会话。",
        ),
    ] = DEFAULT_THREAD_PAGE_SIZE,
    offset: Annotated[
        int,
        Query(ge=0, description="跳过前几个会话。"),
    ] = 0,
) -> AgentThreadListResponse | JSONResponse:
    """分页读取当前账号的会话列表。

    Args:
        user: 当前登录账号。
        threads: 会话归属与列表 Service。
        limit: 本页条数，1 到 ``MAX_THREAD_PAGE_SIZE``。
        offset: 跳过的条数。

    Returns:
        本页会话与总数；数据库故障时返回稳定的 503 JSON。

    Notes:
        只读 ``agent_threads``，不碰 checkpointer、不调模型。

        offset 分页的已知取舍：一边翻页一边新建会话时，列表整体前移会让某一条在两页里重复、
        另一条被跳过。一个账号的会话是几十到几百个，这个规模下不值得换成游标分页——那样就拿不到
        ``total``（除非再查一次 count）。
    """

    try:
        records, total = await threads.list_threads(
            user_id=user.id,
            limit=limit,
            offset=offset,
        )
    except SQLAlchemyError as error:
        return _database_error(error)
    return AgentThreadListResponse(
        items=tuple(AgentThreadSummary.model_validate(record) for record in records),
        total=total,
    )


@router.get(
    "/{thread_id}/messages",
    response_model=AgentThreadMessagesResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": AgentChatErrorResponse},
        503: {"model": AgentChatErrorResponse},
    },
    summary="读取一个会话的历史消息",
    description=(
        "回放某个会话已经存下的问答，供前端在续聊前把界面补齐。"
        "不分页：历史被压缩中间件封在有限条数内。\n\n"
        "`summarized` 为真表示早期历史已被压缩成摘要、原始消息已不存在，"
        "此时 `turns` 不是全部历史，界面必须如实说明。"
    ),
)
async def get_agent_thread_messages(
    thread_id: UUID,
    user: Annotated[UserRecord, Depends(current_active_user)],
    threads: Annotated[AgentThreadService, Depends(get_agent_thread_service)],
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> AgentThreadMessagesResponse | JSONResponse:
    """回放一个会话的历史问答。

    Args:
        thread_id: 目标会话 id。
        user: 当前登录账号。
        threads: 会话归属 Service。
        runtime: 进程级 Agent Runtime，用它的 graph 读 checkpointer 状态。

    Returns:
        按时间排列的历史轮次，以及历史是否被压缩过。

    Raises:
        AgentThreadNotFoundError: 会话不存在或不属于当前账号；由 handler 映射成 404。

    Notes:
        先查业务库确认归属，再读 checkpointer 状态；两者走不同连接池（见 ADR 0004）。
        不调模型，不写任何东西。

        历史从 checkpointer 读而不是另存一份副本：副本会因为历史压缩而与模型实际看到的上下文
        分叉，界面显示的和模型记得的对不上。用 ``aget_state`` 而不是 ``aget_state_history``——
        后者返回全部 checkpoint（实测两轮对话 21 行），这里只要最新那个状态。
    """

    try:
        owned = await threads.get_owned_thread(user_id=user.id, thread_id=thread_id)
    except SQLAlchemyError as error:
        return _database_error(error)

    snapshot = await runtime.graph.aget_state(
        {"configurable": {"thread_id": str(thread_id)}}
    )
    messages = (snapshot.values or {}).get("messages") or []
    turns, summarized, summary = build_replay_turns(messages)
    return AgentThreadMessagesResponse(
        thread_id=thread_id,
        # 在途运行的 id 从会话行上读，不从 checkpointer 推：正在跑的那一轮还没落库，
        # 从消息里根本看不出来。它是前端刷新后能区分「还没回答」与「还在生成」的唯一依据。
        active_run_id=owned.active_run_id,
        turns=turns,
        scope=KnowledgeBaseSelection.model_validate(owned.scope),
        summarized=summarized,
        summary=summary,
    )


@router.patch("/{thread_id}/scope", response_model=KnowledgeBaseSelection, summary="保存会话知识库选择，只影响后续提问")
async def update_agent_thread_scope(
    thread_id: UUID,
    selection: KnowledgeBaseSelection,
    user: Annotated[UserRecord, Depends(current_active_user)],
    threads: Annotated[AgentThreadService, Depends(get_agent_thread_service)],
    search: Annotated[VectorSearchService, Depends(get_vector_search_service)],
) -> KnowledgeBaseSelection | JSONResponse:
    """归属与知识库有效性检查通过后，在短事务中保存用户选择。"""
    try:
        await threads.get_owned_thread(user_id=user.id, thread_id=thread_id)
        await search.resolve_scope(selection)
        await threads.update_scope(user_id=user.id, thread_id=thread_id, scope=selection)
    except SQLAlchemyError as error:
        return _database_error(error)
    return selection


@router.delete(
    "/{thread_id}",
    response_model=AgentThreadDeletionResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": AgentChatErrorResponse},
        503: {"model": AgentChatErrorResponse},
    },
    summary="删除一个会话及其历史",
    description=(
        "删除会话记录，并清掉 checkpointer 里对应的全部历史。删除后同一个 id 无法续聊。\n\n"
        "如果这个会话有运行在跑，先请求停下它并等它收尾，再清历史；等不到（可能卡在一次不响应取消的"
        "调用里）就按已中断继续删，不让删除请求挂住。"
    ),
)
async def delete_agent_thread(
    thread_id: UUID,
    user: Annotated[UserRecord, Depends(current_active_user)],
    threads: Annotated[AgentThreadService, Depends(get_agent_thread_service)],
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> AgentThreadDeletionResponse | JSONResponse:
    """删除一个会话：先停在途运行，再清历史，最后删归属记录。

    Args:
        thread_id: 目标会话 id。
        user: 当前登录账号。
        threads: 会话归属 Service。
        runtime: 进程级 Agent Runtime，用它的 checkpointer 清历史。

    Returns:
        成功时回带被删除的会话 id；数据库故障时稳定的 503 JSON。

    Raises:
        AgentThreadNotFoundError: 会话不存在或不属于当前账号；由 handler 映射成 404。

    Notes:
        **步骤顺序都是有意的，不要交换：**

        1. 停在途运行（如果有）——它正在往这个会话的历史里写，不等它停就清历史，它会在我们清空之后
           继续写回来，留下一条查不到也删不掉的孤儿会话；
        2. 清 checkpointer 里的历史；
        3. 删业务库里的归属记录。

        2 与 3 的顺序也不能换。历史在 checkpointer（原生 psycopg 池），归属记录在业务库
        （SQLAlchemy 池），跨两个池不可能一个事务，所以必须选「中途失败留下什么」：

        - 现在这个顺序失败后留下「历史已删、归属还在」——用户看到一个点进去是空的会话，
          再点一次删除就干净了，可自愈。
        - 反过来留下「归属已删、历史还在」——那条历史查不到也删不掉，只能等
          ``prune-orphan-threads`` 来收。

        用 checkpointer 自己的 ``adelete_thread``（公开 API）而不是手写 DELETE：那四张表的结构归
        ``langgraph-checkpoint-postgres`` 管，我们不复制它的 schema 知识（见 ADR 0004 与 0009）。
    """

    try:
        owned = await threads.get_owned_thread(user_id=user.id, thread_id=thread_id)
    except SQLAlchemyError as error:
        return _database_error(error)

    # 1、先让在途的那次运行停下来。它正在往这个会话的历史里写，不确认它停了就去清历史，它会在我们
    #    清空之后继续写回来，留下一条查不到也删不掉的孤儿会话（术语表里的「孤儿会话」）。
    if owned.active_run_id is not None:
        await _wait_for_run_to_stop(
            threads, user_id=user.id, thread_id=thread_id, run_id=owned.active_run_id
        )

    # 2、先清历史。checkpointer 为 None 只发生在注入了替身的离线场景，此时没有历史可清。
    if runtime.checkpointer is not None:
        await runtime.checkpointer.adelete_thread(str(thread_id))

    # 3、历史清干净了才删归属记录。
    try:
        await threads.delete_thread_record(user_id=user.id, thread_id=thread_id)
    except SQLAlchemyError as error:
        return _database_error(error)

    logger.info("会话已删除 thread_id=%s", thread_id)
    return AgentThreadDeletionResponse(thread_id=thread_id)


async def _wait_for_run_to_stop(
    threads: AgentThreadService,
    *,
    user_id: UUID,
    thread_id: UUID,
    run_id: UUID,
) -> None:
    """请求停下这次运行，并等它释放占位；等不到就按已中断继续删。

    为什么必须先停：运行在跑的过程中一直在往 checkpointer 写（每个节点结束写一次），不只是收尾那一次。
    不确认它停了就去清历史，它会在我们清空之后继续写回来，留下一条有历史、没有归属记录的孤儿会话
    ——查不到也删不掉，只能靠运维命令清。

    为什么要等而不是直接把占位清掉：停止是协作式、跨进程的，收到删除请求的进程不一定跑着这次运行，
    所以只能先写停止请求、再等它自己收尾（见 ADR 0037）。

    Args:
        threads: 会话 Service；停止请求与占位状态都在 ``agent_threads`` 那一行上。
        user_id: 当前登录账号，用来在等待期间重读会话（重读要带归属条件）。
        thread_id: 目标会话。
        run_id: 删除那一刻在途运行的 id。

    Notes:
        执行 PostgreSQL 读写。**等不到就继续删**：删除请求挂住比留下一次未完成的运行更糟，代价如实
        记在 spec 的「补充说明」里（那次运行随后可能把收尾内容写回，留下一条孤儿会话，只能靠运维命令
        清）。上限与轮询间隔见 ``agent/limits.py``。
    """

    try:
        await threads.request_stop(thread_id=thread_id, run_id=run_id)
    except SQLAlchemyError as error:
        # 停止请求写不进去（库不可用）：下面的循环也读不到状态，直接按已中断继续删。
        logger.warning(
            "写入停止请求失败 thread_id=%s error_type=%s", thread_id, type(error).__name__
        )
        return

    loop = asyncio.get_running_loop()
    deadline = loop.time() + DELETE_RUNNING_THREAD_WAIT_SECONDS
    while loop.time() < deadline:
        await asyncio.sleep(DELETE_RUNNING_THREAD_POLL_INTERVAL_SECONDS)
        try:
            current = await threads.get_owned_thread(user_id=user_id, thread_id=thread_id)
        except AgentThreadNotFoundError:
            # 已经在别处被删掉了，没什么可等的。
            return
        except SQLAlchemyError as error:
            logger.warning(
                "等待在途运行收尾时读不到会话 thread_id=%s error_type=%s",
                thread_id,
                type(error).__name__,
            )
            return
        if current.active_run_id is None:
            return
    logger.warning("等待在途运行收尾超时，按已中断继续删除 thread_id=%s", thread_id)


def _database_error(error: SQLAlchemyError) -> JSONResponse:
    """把业务库故障交给共享错误表映射成稳定 503（只读异常类型）。

    Args:
        error: 请求期间捕获的 SQLAlchemy 异常。

    Returns:
        含 ``agent_thread_database_unavailable`` 的 503 JSON 响应。

    Notes:
        不读 ``str(error)``：SQLAlchemy 的异常文本可能带连接串，里面有数据库密码。
    """

    return build_agent_chat_error_response(error)


__all__ = ["router"]
