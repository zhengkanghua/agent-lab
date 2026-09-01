"""管理 Agent 会话的归属校验与会话列表读写。

本模块位于 Service 层，是「某个会话属于谁」的唯一判断处。它只读写 ``agent_threads`` 一张业务表，
**不碰 LangGraph checkpointer 的四张表**，也不读写消息内容——历史归 checkpointer，删除历史由调用方
（``api/agent_threads.py``）用 checkpointer 自己的 ``adelete_thread`` 完成。

**为什么持有 session 工厂而不是 session**（改动前必读
docs/adr/0010-sse-routes-use-short-lived-db-sessions.md）：

主要调用方是 ``POST /agent/chat``，它返回 ``StreamingResponse``，一次对话可能几分钟。FastAPI 的
``Depends(get_db_session)`` 要等响应彻底结束才归还连接，而流式响应的「结束」是流关闭之后——那会让
一条业务连接被占满全程，几个并发就能把连接池占空，故障表现是**检索页**报数据库不可用，跟 Agent
看起来毫无关系。所以本 Service 的每个方法自己开一次 session、提交、立刻关，校验和写入只占几十毫秒。

工厂必须由构造参数传入，不能在方法里直接 import ``db.session.async_session_factory``：那是 import
时就绑好真实 ``DATABASE_URL`` 的模块级对象，直接引用会让离线测试没有注入点、真去连 PostgreSQL 等满
超时（``tests/app_helpers.py`` 开头记着同类的坑，曾让全套测试从 14 秒退回 20 分钟）。
"""

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent_lab.agent.errors import AgentThreadNotFoundError
from agent_lab.models.agent_thread import AgentThreadRecord


logger = logging.getLogger(__name__)

# 标题列的长度上限，与 ``AgentThreadRecord.title`` 的 String(60) 必须一致。
MAX_THREAD_TITLE_CHARS = 60

# 首条提问为空白等极端情况下的兜底标题。理论上到不了这里（``AgentChatRequest`` 已经拒绝纯空白
# 提问），但标题列 NOT NULL，留一个确定值比让数据库报约束错误好。
FALLBACK_THREAD_TITLE = "未命名会话"


def derive_thread_title(message: str) -> str:
    """把首条提问压成一行会话标题。

    Args:
        message: 用户的第一条提问原文。

    Returns:
        不超过 ``MAX_THREAD_TITLE_CHARS`` 个字符的单行标题。

    Notes:
        纯字符串处理，不执行 I/O。

        把换行和连续空白折成单个空格：标题在列表里是一行，原文里的换行会让它在某些浏览器上
        撑高行盒，把列表挤得高低不齐。

        **不加省略号**。省略号交给前端 CSS 的 text-overflow：后端加的话，宽屏明明放得下整句，
        也会带着一个多余的点。
    """

    collapsed = " ".join(message.split())
    if not collapsed:
        return FALLBACK_THREAD_TITLE
    return collapsed[:MAX_THREAD_TITLE_CHARS]


class AgentThreadService:
    """会话归属与会话列表的读写入口。

    Attributes:
        _session_factory: 产出 ``AsyncSession`` 的工厂。每个方法调用一次、用完即关。
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        """记录 session 工厂，不建连、不查库。

        Args:
            session_factory: 通常是 ``db.session.async_session_factory``；离线测试传替身。
        """

        self._session_factory = session_factory

    async def ensure_thread(
        self,
        *,
        user_id: UUID,
        thread_id: UUID | None,
        first_message: str,
    ) -> UUID:
        """确定本轮提问所属的会话 id，并保证它归当前账号所有。

        ``thread_id`` 为 ``None`` 表示新建会话：服务端生成 id、用首条提问当标题插入一行。
        非 ``None`` 表示续聊：校验归属，通过则把 ``last_active_at`` 推到当前时间。

        Args:
            user_id: 当前登录账号 id。
            thread_id: 前端要续聊的会话 id；``None`` 表示新建。
            first_message: 本轮提问原文，只在新建时用来取标题。

        Returns:
            确定可用、且已确认归属的会话 id。

        Raises:
            AgentThreadNotFoundError: ``thread_id`` 在库里没有，或者存在但属于别的账号。
            SQLAlchemyError: 业务库不可用；由错误契约映射成 503。

        Notes:
            执行 PostgreSQL 写入（insert 或 update），一个事务内完成并提交，随后立刻归还连接。
            调用方必须在**开始流式响应之前** await 它：只有这样失败才能变成正常的 HTTP 状态码，
            流一旦开始就只能发 error 事件了。

            为什么续聊也要写一次：``last_active_at`` 是会话列表的排序键，不更新的话「最近聊过的
            排在最前」就不成立。只在这里写、流结束后不再写，理由见 spec 3.4。
        """

        now = datetime.now(UTC)
        async with self._session_factory() as session:
            if thread_id is None:
                created_id = uuid4()
                session.add(
                    AgentThreadRecord(
                        thread_id=created_id,
                        user_id=user_id,
                        title=derive_thread_title(first_message),
                        created_at=now,
                        last_active_at=now,
                    )
                )
                await session.commit()
                return created_id

            # 用带 user_id 条件的 UPDATE 一次搞定「校验 + 续活」：先 SELECT 再 UPDATE 需要两次
            # 往返，而且中间存在窗口。rowcount 为 0 同时覆盖「id 不存在」和「id 属于别人」，
            # 正好对应合并成 404 的决定（见 AgentThreadNotFoundError 的 docstring）。
            result = await session.execute(
                update(AgentThreadRecord)
                .where(
                    AgentThreadRecord.thread_id == thread_id,
                    AgentThreadRecord.user_id == user_id,
                )
                .values(last_active_at=now)
            )
            if result.rowcount == 0:
                await session.rollback()
                # 只记 id 和账号，不记提问内容。id 是我们自己生成的 UUID，不是用户输入。
                logger.warning(
                    "拒绝访问不属于当前账号的会话 thread_id=%s user_id=%s",
                    thread_id,
                    user_id,
                )
                raise AgentThreadNotFoundError
            await session.commit()
            return thread_id

    async def list_threads(
        self,
        *,
        user_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[AgentThreadRecord], int]:
        """按最近活跃倒序分页读取当前账号的会话。

        Args:
            user_id: 当前登录账号 id。
            limit: 本页最多几条。
            offset: 跳过前几条。

        Returns:
            ``(本页记录, 该账号会话总数)``。总数用来让界面显示「共 N 个」和算总页数。

        Raises:
            SQLAlchemyError: 业务库不可用。

        Notes:
            执行两次 PostgreSQL 读查询（一页数据 + 一次 count），只读本表、不碰 checkpointer。

            为什么单独查一次 count 而不是用窗口函数：``COUNT(*) OVER ()`` 能省一次往返，但那样
            空结果页拿不到总数（没有行就没有窗口值），而「翻到越界的页」恰恰需要总数才能给出
            「共 N 个」的提示。两次查询在这个数据量下（一个账号几十到几百个会话）不值得优化。
        """

        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AgentThreadRecord)
                .where(AgentThreadRecord.user_id == user_id)
                # 加 thread_id 作次级排序键：同一毫秒创建的两行光按 last_active_at 排是不确定的，
                # 分页时会出现某行在两页里都不出现。次级键让顺序全序、可重复。
                .order_by(
                    AgentThreadRecord.last_active_at.desc(),
                    AgentThreadRecord.thread_id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            total = await session.scalar(
                select(func.count())
                .select_from(AgentThreadRecord)
                .where(AgentThreadRecord.user_id == user_id)
            )
            return list(rows), int(total or 0)

    async def get_owned_thread(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> AgentThreadRecord:
        """读取一个会话，并确认它属于当前账号。

        Args:
            user_id: 当前登录账号 id。
            thread_id: 目标会话 id。

        Returns:
            该会话的归属记录。

        Raises:
            AgentThreadNotFoundError: 会话不存在或不属于当前账号。
            SQLAlchemyError: 业务库不可用。

        Notes:
            只读一行，不写。回放历史和删除会话都先过这一关，所以「不是你的会话就什么都别做」
            这条规则只有一处实现。
        """

        async with self._session_factory() as session:
            record = await session.scalar(
                select(AgentThreadRecord).where(
                    AgentThreadRecord.thread_id == thread_id,
                    AgentThreadRecord.user_id == user_id,
                )
            )
        if record is None:
            logger.warning(
                "拒绝访问不属于当前账号的会话 thread_id=%s user_id=%s",
                thread_id,
                user_id,
            )
            raise AgentThreadNotFoundError
        return record

    async def delete_thread_record(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> None:
        """删除一个会话的归属记录。

        Args:
            user_id: 当前登录账号 id。
            thread_id: 目标会话 id。

        Raises:
            AgentThreadNotFoundError: 会话不存在或不属于当前账号。
            SQLAlchemyError: 业务库不可用。

        Notes:
            只删本表这一行，**不删 checkpointer 里的历史**。完整的删除动作是两步，顺序由调用方
            保证：先让 checkpointer 清历史，成功后才调本方法。反过来会留下「业务行没了、历史还在」
            的孤儿——查不到也删不掉，只能等 ``prune-orphan-threads`` 收；而按正确顺序留下的是
            「历史没了、业务行还在」，用户再点一次删除就好，可自愈。
        """

        async with self._session_factory() as session:
            result = await session.execute(
                delete(AgentThreadRecord).where(
                    AgentThreadRecord.thread_id == thread_id,
                    AgentThreadRecord.user_id == user_id,
                )
            )
            if result.rowcount == 0:
                await session.rollback()
                raise AgentThreadNotFoundError
            await session.commit()

    async def list_known_thread_ids(self) -> set[UUID]:
        """读取全部账号的会话 id，供孤儿清理命令做差集。

        Returns:
            ``agent_threads`` 里所有 thread_id。

        Raises:
            SQLAlchemyError: 业务库不可用。

        Notes:
            只读、不分账号——它服务的是运维命令 ``prune-orphan-threads``，判断依据是「checkpointer
            里有历史但这里没有归属记录」，跟哪个账号无关。请求路径不用它。
        """

        async with self._session_factory() as session:
            rows = await session.scalars(select(AgentThreadRecord.thread_id))
            return set(rows)


__all__ = [
    "FALLBACK_THREAD_TITLE",
    "MAX_THREAD_TITLE_CHARS",
    "AgentThreadService",
    "derive_thread_title",
]
