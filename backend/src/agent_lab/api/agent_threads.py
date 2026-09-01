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

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.agent.replay import build_replay_turns
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.api.dependencies import get_agent_runtime, get_agent_thread_service
from agent_lab.api.error_contract import build_agent_chat_error_response
from agent_lab.auth.dependencies import current_superuser
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
    user: Annotated[UserRecord, Depends(current_superuser)],
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
    user: Annotated[UserRecord, Depends(current_superuser)],
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
        await threads.get_owned_thread(user_id=user.id, thread_id=thread_id)
    except SQLAlchemyError as error:
        return _database_error(error)

    snapshot = await runtime.graph.aget_state(
        {"configurable": {"thread_id": str(thread_id)}}
    )
    messages = (snapshot.values or {}).get("messages") or []
    turns, summarized, summary = build_replay_turns(messages)
    return AgentThreadMessagesResponse(
        thread_id=thread_id,
        turns=turns,
        summarized=summarized,
        summary=summary,
    )


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
        "删除会话记录，并清掉 checkpointer 里对应的全部历史。删除后同一个 id 无法续聊。"
    ),
)
async def delete_agent_thread(
    thread_id: UUID,
    user: Annotated[UserRecord, Depends(current_superuser)],
    threads: Annotated[AgentThreadService, Depends(get_agent_thread_service)],
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> AgentThreadDeletionResponse | JSONResponse:
    """删除一个会话：先清历史，再删归属记录。

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
        **两步的顺序是有意的，不要交换。** 历史在 checkpointer（原生 psycopg 池），归属记录在业务库
        （SQLAlchemy 池），跨两个池不可能一个事务，所以必须选「中途失败留下什么」：

        - 现在这个顺序失败后留下「历史已删、归属还在」——用户看到一个点进去是空的会话，
          再点一次删除就干净了，可自愈。
        - 反过来留下「归属已删、历史还在」——那条历史查不到也删不掉，只能等
          ``prune-orphan-threads`` 来收。

        用 checkpointer 自己的 ``adelete_thread``（公开 API）而不是手写 DELETE：那四张表的结构归
        ``langgraph-checkpoint-postgres`` 管，我们不复制它的 schema 知识（见 ADR 0004 与 0009）。
    """

    try:
        await threads.get_owned_thread(user_id=user.id, thread_id=thread_id)
    except SQLAlchemyError as error:
        return _database_error(error)

    # 1、先清历史。checkpointer 为 None 只发生在注入了替身的离线场景，此时没有历史可清。
    if runtime.checkpointer is not None:
        await runtime.checkpointer.adelete_thread(str(thread_id))

    # 2、历史清干净了才删归属记录。
    try:
        await threads.delete_thread_record(user_id=user.id, thread_id=thread_id)
    except SQLAlchemyError as error:
        return _database_error(error)

    logger.info("会话已删除 thread_id=%s", thread_id)
    return AgentThreadDeletionResponse(thread_id=thread_id)


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
