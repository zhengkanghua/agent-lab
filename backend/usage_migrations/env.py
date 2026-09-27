"""用量库独有的 Alembic 环境。

与业务库的 ``alembic/env.py`` 有三处刻意的不同：

1. **表结构来自 ``UsageBase.metadata``**，不是 ``db.base.Base``。用量表不能出现在业务侧
   metadata 里，否则业务库的 ``alembic check`` 会把它当成缺失的表而在业务库里建出来，或者
   当成多余的表删掉；
2. **连接串来自 ``get_usage_database_settings()``**，与业务库的连接串是两个独立配置；
3. **没有 ``include_object``**：这个 metadata 里只有用量表，没有需要排除的外部表。

历史迁移的签名都是普通 ``upgrade()``，所以不使用 Alembic 官方的多库模板（它的分派依赖模板
生成的函数签名，运行时会在本仓库的历史迁移上直接报错）。
"""

import asyncio
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

from agent_lab.config.usage_database import get_usage_database_settings
from agent_lab.usage.models import UsageBase

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_usage_database_settings()

# UsageBase 只注册用量表，autogenerate 的比较范围因此仅限用量库。
target_metadata = UsageBase.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不需要 DBAPI。"""

    context.configure(
        url=str(settings.database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """连接用量库并用 NullPool 跑完迁移，完成后不保留连接。"""

    connectable = create_async_engine(
        str(settings.database_url),
        poolclass=pool.NullPool,
        connect_args={
            "connect_timeout": settings.connect_timeout,
            # 会话时区与应用侧一致；迁移里写入带时区列时不受本机时区影响。
            "options": "-c timezone=UTC",
        },
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """连接用量库并执行迁移。

    psycopg 异步模式在 Windows 上需要 SelectorEventLoop，Linux 使用平台默认事件循环；
    这一点与业务库 env.py 相同。
    """

    # 验收可提供限定 search_path 的连接；普通 CLI 走下面的配置连接。
    connection = config.attributes.get("connection")
    if connection is not None:
        do_run_migrations(connection)
        return
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(run_async_migrations(), loop_factory=loop_factory)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
