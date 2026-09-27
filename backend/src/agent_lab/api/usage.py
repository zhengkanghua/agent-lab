"""把用量记录暴露为面向当前账号的只读查询接口。

本模块位于 FastAPI 边界层，只做四件事：取依赖、校验分页与筛选参数、把查询交给
``usage.repository``、把结果翻成契约。它不决定筛选口径（在 ``UsageFilter`` 一处）、不决定
错误文案（在 ``api/error_contract``），也不碰写入链路。

三条接口（本期先交付明细与汇总）都面向**当前账号**：账号从登录态取，不从请求参数取，所以
「查别人用量」这种用法在契约上就写不出来。

**筛选参数只有一份定义**（``usage_query`` 依赖）。明细与汇总必须接受同一组筛选，把它写成一个
共用依赖是结构性的保证，而不是两个 endpoint 各写一遍再人工核对。

用量库不可用时的处置与写入侧相反：返回稳定的 503，**不返回空列表、不返回零汇总**。把
「查不到」渲染成 0，正是这份功能反复拒绝的事——迁移没跑、库被摘掉都会变成「这个月没花钱」。
``SQLAlchemyError`` 不是 ``UsageDatabaseUnavailableError``，所以要显式 catch，做法与
``api/agent_threads.py`` 对数据库故障的处置一致。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_lab.api.dependencies import get_usage_session_factory
from agent_lab.api.error_contract import build_usage_error_response
from agent_lab.auth.dependencies import current_active_user
from agent_lab.models.user import UserRecord
from agent_lab.schemas.usage import (
    DEFAULT_USAGE_PAGE_SIZE,
    MAX_USAGE_PAGE_SIZE,
    UsageErrorResponse,
    UsageRecordItem,
    UsageRecordPage,
    UsageSummaryResponse,
)
from agent_lab.usage.repository import UsageFilter, UsageRepository


router = APIRouter(prefix="/usage", tags=["usage"])


@dataclass(frozen=True, slots=True)
class UsageQuery:
    """明细与汇总共用的筛选参数。

    Attributes:
        model_name: 模型名；``None`` 表示不筛这一维。
        start: 时间范围起点（UTC，含）。
        end: 时间范围终点（UTC，不含）。
    """

    model_name: str | None
    start: datetime | None
    end: datetime | None

    def to_filter(self, user_id: UUID) -> UsageFilter:
        """翻成仓储层使用的筛选条件，并顺手把时刻规整成 UTC。"""

        return UsageFilter(
            user_id=user_id,
            model_name=self.model_name,
            occurred_from=_as_utc(self.start),
            occurred_to=_as_utc(self.end),
        )


def usage_query(
    model: Annotated[
        str | None,
        Query(max_length=128, description="只看某个模型的记录；省略表示不筛这一维。"),
    ] = None,
    start: Annotated[
        datetime | None,
        Query(description="时间范围起点（UTC，含）；省略表示不限。"),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(description="时间范围终点（UTC，不含）；省略表示不限。"),
    ] = None,
) -> UsageQuery:
    """解析用量查询的筛选参数（FastAPI 依赖注入函数）。

    抽成依赖而不是在每个 endpoint 上重复声明：明细与汇总必须接受同一组筛选，规格明确不允许
    「列表认这个参数、汇总悄悄忽略」的缝隙，共用一份声明是这条要求的结构性保证。
    """

    return UsageQuery(model_name=model, start=start, end=end)


@router.get(
    "/models",
    response_model=list[str],
    status_code=status.HTTP_200_OK,
    responses={503: {"model": UsageErrorResponse}},
    summary="列出当前账号用过的模型名",
    description=(
        "返回当前账号实际用过的模型名，按名称排序，供筛选栏取值。不需要用户手输，也不包含\n"
        "别人用过的模型。\n\n"
        "它不接受时间与模型筛选：跟着时间范围走的话，选了某个模型再改时间范围，选项里那个模型\n"
        "可能消失，而当前选中值仍在界面上。"
    ),
)
async def list_usage_models(
    user: Annotated[UserRecord, Depends(current_active_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession],
        Depends(get_usage_session_factory),
    ],
) -> list[str] | JSONResponse:
    """列出当前账号用过的模型名。

    Args:
        user: 当前登录账号。
        session_factory: 用量库短会话工厂。

    Returns:
        排序后的模型名列表；库里一条都没有时是空列表。用量库故障时返回稳定的 503 JSON。

    Raises:
        UsageDatabaseUnavailableError: 用量库资源缺失；由全局 handler 映射成 503。

    Notes:
        只按账号过滤，不做聚合也不分页。
    """

    try:
        async with session_factory() as session:
            return await UsageRepository(session).list_model_names(UsageFilter(user_id=user.id))
    except SQLAlchemyError as error:
        return _database_error(error)


@router.get(
    "/records",
    response_model=UsageRecordPage,
    status_code=status.HTTP_200_OK,
    responses={503: {"model": UsageErrorResponse}},
    summary="列出当前账号的用量明细",
    description=(
        "按发生时刻倒序分页返回当前账号的模型调用明细。只返回自己的记录；"
        "`has_more` 表示还有没有下一页，不返回精确总数。\n\n"
        "`start` 含、`end` 不含（闭开区间），`model` 按模型名精确匹配，三者可同时使用。"
        "时间参数按 UTC 传入，省略表示不限。\n\n"
        "刚发生的调用可能还没出现：写入先入队、按秒批量落库，查询结果有一秒左右的延迟。"
    ),
)
async def list_usage_records(
    user: Annotated[UserRecord, Depends(current_active_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession],
        Depends(get_usage_session_factory),
    ],
    query: Annotated[UsageQuery, Depends(usage_query)],
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_USAGE_PAGE_SIZE, description="本页最多返回几条。"),
    ] = DEFAULT_USAGE_PAGE_SIZE,
    offset: Annotated[
        int,
        Query(ge=0, description="跳过前几条。"),
    ] = 0,
) -> UsageRecordPage | JSONResponse:
    """分页读取当前账号的用量明细。

    Args:
        user: 当前登录账号；账号只从这里来，请求参数里没有这一维。
        session_factory: 用量库短会话工厂，由 lifespan 装配。
        query: 模型与时间范围筛选。
        limit: 本页条数，1 到 ``MAX_USAGE_PAGE_SIZE``。
        offset: 跳过的条数。

    Returns:
        本页明细与「还有没有下一页」；用量库故障时返回稳定的 503 JSON。

    Raises:
        UsageDatabaseUnavailableError: 用量库资源缺失；由全局 handler 映射成 503。

    Notes:
        只读用量库，不碰业务库、checkpointer、模型或 Qdrant。

        offset 分页的已知取舍：一边翻页一边有新记录写入时，整页会被往后顶，某一条可能在两页里
        重复、另一条被跳过。这条漂移本期不处理——账本持续增长，「不重不漏」在跨请求的并发写入下
        只能靠游标分页保证，而游标拿不到「还有没有下一页」以外的信息且要改前端契约。
    """

    try:
        async with session_factory() as session:
            records, has_more = await UsageRepository(session).list_records(
                query.to_filter(user.id),
                limit=limit,
                offset=offset,
            )
    except SQLAlchemyError as error:
        return _database_error(error)

    return UsageRecordPage(
        items=tuple(UsageRecordItem.model_validate(record) for record in records),
        has_more=has_more,
    )


@router.get(
    "/summary",
    response_model=UsageSummaryResponse,
    status_code=status.HTTP_200_OK,
    responses={503: {"model": UsageErrorResponse}},
    summary="汇总当前账号的用量",
    description=(
        "返回当前账号在筛选范围内的输入、输出、缓存、合计四个 token 各自的总和，以及调用次数。"
        "筛选参数与 `GET /usage/records` 完全一致；同一组参数下这里的四个合计逐列等于明细里"
        "全部记录的对应值之和。\n\n"
        "`cached_tokens` 为 null 表示范围内有记录、但上游一次都没报过缓存；"
        "范围内没有记录时四个合计都是 0。\n\n"
        "聚合不受明细分页影响：换页不会改变这四个数字。"
    ),
)
async def summarize_usage(
    user: Annotated[UserRecord, Depends(current_active_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession],
        Depends(get_usage_session_factory),
    ],
    query: Annotated[UsageQuery, Depends(usage_query)],
) -> UsageSummaryResponse | JSONResponse:
    """按与明细相同的筛选条件汇总当前账号的用量。

    Args:
        user: 当前登录账号。
        session_factory: 用量库短会话工厂。
        query: 模型与时间范围筛选；与明细共用同一份声明。

    Returns:
        四个 token 合计与调用次数；用量库故障时返回稳定的 503 JSON。

    Raises:
        UsageDatabaseUnavailableError: 用量库资源缺失；由全局 handler 映射成 503。

    Notes:
        一次聚合查询，不受 ``limit`` / ``offset`` 影响——汇总口径是「全部记录」，不是「当前页」。
    """

    try:
        async with session_factory() as session:
            totals = await UsageRepository(session).summarize(query.to_filter(user.id))
    except SQLAlchemyError as error:
        return _database_error(error)

    return UsageSummaryResponse(
        input_tokens=totals.input_tokens,
        output_tokens=totals.output_tokens,
        cached_tokens=totals.cached_tokens,
        total_tokens=totals.total_tokens,
        call_count=totals.call_count,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    """把查询参数里的时刻规整成带 UTC 时区的值。

    带偏移量的时刻换算成 UTC；不带时区的按 UTC 解释，而不是按服务器本地时区——否则同一句查询
    在不同部署时区下会筛出不同的窗口，而调用方无从知道自己踩上了哪一种。

    Args:
        value: 请求参数解析出的时刻，可能为空。

    Returns:
        带 UTC 时区的时刻；``None`` 原样返回。
    """

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _database_error(error: SQLAlchemyError) -> JSONResponse:
    """把用量库故障交给用量链路的错误表映射成稳定 503（只读异常类型）。"""

    return build_usage_error_response(error)


__all__ = ["router"]
