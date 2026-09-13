"""SQLAlchemy 异步数据库基础设施。

Engine 和连接池属于进程级资源；AsyncSession 属于一次工作单元，通常由单个
HTTP 请求独占。业务代码不应自行重复创建 Engine。
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_lab.config.settings import get_settings


def build_database_resources(settings=None):
    """在所属进程和事件循环中建立独立的连接池；Worker 在 fork 后调用。"""
    settings = settings or get_settings()
    engine = create_async_engine(
        str(settings.database_url),
        echo=settings.database_echo,
        connect_args={
            "connect_timeout": settings.database_connect_timeout,
            "options": f"-c timezone={settings.database_timezone}",
        },
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
    )
    sessions = async_sessionmaker(bind=engine, expire_on_commit=False)
    return engine, sessions


engine, async_session_factory = build_database_resources()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """为一次 FastAPI 请求提供独立的异步数据库 Session。

    Yields:
        当前请求使用的 ``AsyncSession``。请求结束后上下文管理器会自动关闭
        Session，并把底层连接归还连接池。

    Notes:
        本依赖不自动提交事务。写操作应由 Service 在完整业务操作成功后显式
        调用 ``await session.commit()``，以便事务边界与业务边界保持一致。
    """

    async with async_session_factory() as session:
        try:
            # 1、把 Session 交还给请求处理逻辑（FastAPI 依赖注入）
            yield session
        except Exception:
            # 2、请求内部出错：回滚，确保失败的操作不留未提交修改
            await session.rollback()
            raise
