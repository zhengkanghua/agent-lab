"""集中 LangGraph checkpointer 的表名与建表动作。

本模块位于 Agent 层，但**刻意不 import langchain 或 langgraph 的图相关模块**：它的两个
调用方一个是 CLI，一个是 `alembic/env.py`，后者在每次迁移时都会被加载，不该为了拿四个
表名而把整个 Agent 依赖树拖进来。

为什么这四张表要单独立一个模块，而不是写在 `runtime.py` 里：表名是**两处**需要的共享
事实——CLI 要建它们，Alembic 要排除它们。写在任一边，另一边就得复制一份；而复制的后果
很具体：漏改一处会让 `--autogenerate` 生成 `op.drop_table('checkpoints')`，下一次迁移
删掉全部会话历史。决策背景见 docs/adr/0004-checkpointer-tables-outside-alembic.md。

本模块只做建表这一件写操作，且只在显式命令里被调用；不读写业务表、不碰 Qdrant。
"""

from collections.abc import Iterable

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from sqlalchemy.engine import make_url


# LangGraph checkpointer 自建自管的四张表。它们不由本项目的 ORM 定义，也不进 Alembic
# 版本链：结构归 LangGraph 的 setup() 负责，Alembic 只负责别去碰它们。
CHECKPOINTER_TABLE_NAMES = frozenset(
    {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }
)

def to_psycopg_conninfo(database_url: str) -> str:
    """把 SQLAlchemy 风格的 PostgreSQL URL 转成 psycopg 能用的连接串。

    Args:
        database_url: 形如 ``postgresql+psycopg://user:pass@host:5432/db`` 的 URL，
            也接受已经是 ``postgresql://`` 形式的串。

    Returns:
        ``key=value`` 形式的 libpq 连接串（不是 URL），密码等值已由 psycopg 负责加引号。

    Notes:
        纯字符串处理，不建连。返回值含密码，禁止写进日志或异常消息。

        **不能**用「砍掉 driver 前缀、把剩下的当 URL 交给 psycopg」这种做法。psycopg 会对
        URL 里的 userinfo 做百分号解码，于是密码里一个字面的 ``%`` 就变成非法转义、连接
        直接失败（``ProgrammingError: invalid percent-encoded token``）。SQLAlchemy 不做
        这层解码，所以同一个 ``DATABASE_URL`` 会出现「业务查询正常、只有会话记忆连不上」
        的分裂表现，而且报错信息里会带出密码原文。

        改法是不让 psycopg 再解析一遍 URL：用 SQLAlchemy 自己的解析器拆出各字段，再交给
        ``make_conninfo`` 拼成 ``key=value``——那种格式没有百分号转义规则，值按字面传递。
        用 SQLAlchemy 解析而不是 ``urllib`` 也是有意的：业务连接池用的就是它，两边共用同一
        个解析器才能保证「能连业务库」和「能连会话记忆」不会因为解析差异而分家。
    """

    # 1、用 SQLAlchemy 的解析器把 URL 拆成各字段（业务连接池用的也是它）。
    url = make_url(database_url)
    # 2、把 URL 的 query 参数原样带上。里面可能有 sslmode 这类 libpq 参数，吃掉它们会让
    #    生产静默降级成非加密连接。同名参数取最后一个值：libpq 连接串里一个 key 只能有
    #    一个值。
    options: dict[str, str] = {
        key: value[-1] if isinstance(value, tuple) else value
        for key, value in url.query.items()
    }
    # 3、拼成 key=value 形式。这种格式没有百分号转义规则，值按字面传递，密码里的 % 才
    #    不会被当成转义序列。
    return make_conninfo(
        "",
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        dbname=url.database,
        **options,
    )


def include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """告诉 Alembic autogenerate 跳过 checkpointer 的那四张表。

    参数签名由 Alembic 规定（``EnvironmentContext.configure`` 的 ``include_object`` 钩子），
    本函数只用到 ``name`` 和 ``type_``，其余参数保留以满足契约。

    Args:
        object_: 被检查的 SQLAlchemy schema 对象。本函数只按名字判断，不看它。
        name: 对象名；表对象即表名。
        type_: 对象类别，如 ``"table"``、``"column"``、``"index"``。
        reflected: 是否来自数据库反射（``True``）而非 metadata（``False``）。
        compare_to: 对比侧的同名对象，可能为 ``None``。

    Returns:
        ``False`` 表示把该对象排除在比较之外；``True`` 表示照常比较。

    Notes:
        纯判断，不执行 I/O。放在这里而不是 ``alembic/env.py`` 里，是为了能被测试直接
        调用——``env.py`` 在导入时就会执行迁移，没法当模块引入。

        只按表名精确排除，不按 ``checkpoint`` 前缀匹配：前缀匹配会顺手排掉将来某张以
        checkpoint 开头的业务表，那种漏排的表现是「迁移里少了一张表」，很难被发现。
    """

    return not (type_ == "table" and name in CHECKPOINTER_TABLE_NAMES)


async def list_checkpointer_thread_ids(database_url: str) -> set[str]:
    """列出 checkpointer 里存有历史的全部会话 id。

    Args:
        database_url: SQLAlchemy 风格的数据库 URL；内部会转成 psycopg 连接串。

    Returns:
        字符串形式的 thread_id 集合。库里没有会话历史时返回空集合。

    Raises:
        Exception: 无法连接 PostgreSQL、或那四张表还不存在（没跑过 ``init-checkpointer``）时
            传播。调用方只按异常类型报告——连接串里有数据库密码。

    Notes:
        只读，不删不改。

        用 ``alist(None)`` 而不是 ``SELECT DISTINCT thread_id FROM checkpoints``：后者要求我们
        知道那张表的表名和列名，而结构的定义权在 ``langgraph-checkpoint-postgres`` 手里
        （见 ADR 0004 与 0009）。``alist`` 是它的公开 API，传 ``None`` 表示不按会话过滤、
        遍历全部 checkpoint。

        代价是它逐条返回 checkpoint 而不是逐个会话——一次两轮对话大约产生 21 行，所以行数是
        会话数的十几倍。这里只服务运维命令，不在请求路径上；真到了慢得难受的规模，
        再改成带 ``limit`` 分批。
    """

    thread_ids: set[str] = set()
    async with await AsyncConnection.connect(
        to_psycopg_conninfo(database_url),
        autocommit=True,
    ) as connection:
        saver = AsyncPostgresSaver(connection)
        async for checkpoint in saver.alist(None):
            thread_id = checkpoint.config.get("configurable", {}).get("thread_id")
            if thread_id:
                thread_ids.add(str(thread_id))
    return thread_ids


async def delete_checkpointer_threads(
    database_url: str,
    thread_ids: Iterable[str],
) -> int:
    """删除指定会话在 checkpointer 里的全部历史。

    Args:
        database_url: SQLAlchemy 风格的数据库 URL。
        thread_ids: 要清理的 thread_id，字符串形式。

    Returns:
        实际执行了删除的会话个数。

    Raises:
        Exception: 无法连接 PostgreSQL 或删除失败时传播。

    Notes:
        这是**数据写入**，且不可恢复：会删掉那些会话的全部消息历史。它不碰业务表——
        归属记录由 ``AgentThreadService`` 负责，两者的先后顺序由调用方决定。

        用 checkpointer 的 ``adelete_thread`` 而不是手写 DELETE：那四张表之间的外键和写入表
        （``checkpoint_writes`` / ``checkpoint_blobs``）都归它管，手写很容易漏掉一张，
        留下删不干净的残余。
    """

    deleted = 0
    async with await AsyncConnection.connect(
        to_psycopg_conninfo(database_url),
        autocommit=True,
    ) as connection:
        saver = AsyncPostgresSaver(connection)
        for thread_id in thread_ids:
            await saver.adelete_thread(thread_id)
            deleted += 1
    return deleted


async def setup_checkpointer_tables(database_url: str) -> None:
    """创建或升级 checkpointer 的四张表。

    Args:
        database_url: SQLAlchemy 风格的数据库 URL；内部会转成 psycopg 连接串。

    Raises:
        Exception: 无法连接 PostgreSQL、或当前角色没有建表权限时传播。调用方只按异常
            类型报告——连接串里有数据库密码，异常文本可能把它带出来。

    Notes:
        这是数据库**结构**写入：``CREATE TABLE IF NOT EXISTS`` 那四张表，并按 LangGraph
        自己的迁移记录补齐缺失版本。不写业务表、不动 Qdrant、不删除任何已有会话历史。
        重复执行安全。

        用一次性连接而不是连接池：建表是单条命令，用完即走。``autocommit=True`` 是必须
        的——``setup()`` 自己管理事务边界，外面再包一层事务会让它的 DDL 拿不到预期语义。
    """

    async with await AsyncConnection.connect(
        to_psycopg_conninfo(database_url),
        autocommit=True,
    ) as connection:
        await AsyncPostgresSaver(connection).setup()


__all__ = [
    "CHECKPOINTER_TABLE_NAMES",
    "delete_checkpointer_threads",
    "include_object",
    "list_checkpointer_thread_ids",
    "setup_checkpointer_tables",
    "to_psycopg_conninfo",
]
