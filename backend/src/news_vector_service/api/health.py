"""提供应用与 PostgreSQL 的基础健康检查（GET /health）。

本模块只做一件事：执行带总超时的 ``SELECT 1`` 探活并返回状态。
它刻意保持「最小」——不读新闻业务表、不访问 FreshRSS/Ollama/Qdrant，也不做
readiness（就绪探测）、指标采集或自动恢复。为什么：健康检查应该只回答
「进程活着、数据库能连」这两个问题，把完整链路探活交给真正的请求，避免健康检查
本身成为新的故障来源。
"""

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from news_vector_service.config.settings import Settings, get_settings
from news_vector_service.db.session import get_db_session


logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """应用及其必要依赖均可用时返回的响应（当前只有 ok 一种取值）。"""

    status: Literal["ok"] = Field(description="应用运行状态。")
    database: Literal["ok"] = Field(description="PostgreSQL 连接状态。")


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="检查服务和 PostgreSQL 是否可用",
)
async def health(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """执行最小 SQL（SELECT 1）验证数据库连接和查询能力。

    两个 FastAPI 语法点：
    - ``Depends(get_db_session)``：告诉 FastAPI 调用 get_db_session 来获得 Session，
      请求结束还会自动帮我们关闭它；
    - ``Annotated[X, Depends(...)]``：既让 FastAPI 认识依赖，又保留 X 类型信息，
      编辑器补全和静态检查都能看到真实类型。

    为什么用 ``SELECT 1`` 而不是查业务表：它不依赖任何表存在，空数据库也能通过，
    只验证「连接建立 + 能执行 SQL」这两件事；任何业务表结构变化都不会误伤健康检查。

    Args:
        session: FastAPI 为当前请求注入的异步数据库 Session。
        settings: FastAPI 注入的应用配置，提供健康检查超时等参数。

    Returns:
        应用和 PostgreSQL 都可用时返回状态均为 ``ok`` 的响应模型。

    Raises:
        HTTPException: 数据库连接、查询失败或超过健康检查总超时时返回 503。
    """

    try:
        # 应用级总超时可以覆盖 DNS 返回多个地址、驱动逐个尝试所产生的累计等待。
        async with asyncio.timeout(settings.database_health_check_timeout):
            # SELECT 1 不访问任何业务表，因此空数据库也可以通过健康检查。
            await session.execute(text("SELECT 1"))
    except (TimeoutError, SQLAlchemyError) as exc:
        # SQLAlchemy/驱动异常可能带数据库主机或 URL，只记录稳定类型；HTTP 同样使用
        # 固定消息，避免健康检查成为连接配置泄露路径。
        logger.error(
            "PostgreSQL 健康检查失败 error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库不可用",
        ) from exc

    return HealthResponse(status="ok", database="ok")
