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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent_lab.agent.errors import AgentRunInProgressError, AgentThreadNotFoundError
from agent_lab.agent.limits import RUN_ZOMBIE_THRESHOLD_SECONDS
from agent_lab.models.agent_thread import AgentThreadRecord
from agent_lab.knowledge.scope import KnowledgeBaseSelection
from agent_lab.services.user_preference_service import UserPreferenceService


logger = logging.getLogger(__name__)

# 标题列的长度上限，与 ``AgentThreadRecord.title`` 的 String(60) 必须一致。
MAX_THREAD_TITLE_CHARS = 60

# 首条提问为空白等极端情况下的兜底标题。理论上到不了这里（``AgentChatRequest`` 已经拒绝纯空白
# 提问），但标题列 NOT NULL，留一个确定值比让数据库报约束错误好。
FALLBACK_THREAD_TITLE = "未命名会话"


@dataclass(frozen=True, slots=True)
class DrainedRunClaim:
    """一次成功抢到的「排空待接手」运行所需的全部上下文。

    Attributes:
        thread_id: 待接手的会话。
        run_id: 那次被排空的运行；续跑时在途运行 id 不变，前端已有的等待态与回放轮询不需要知道
            接手发生过。
        user_id: 会话归属账号，用于重建运行上下文里的用量归属。
        system_prompt: 会话级提示词快照；``None`` 表示用内置默认提示词。

    Notes:
        **不含知识库范围**：那是上一轮冻结在 checkpoint 里的值，与「会话行上的当前选择」不是
        同一件事（续聊时可以改选），接手时要回 checkpoint 里取一轮自己的那份（见 ADR 0040）。
    """

    thread_id: UUID
    run_id: UUID
    user_id: UUID
    system_prompt: str | None


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
        run_id: UUID,
        scope: KnowledgeBaseSelection | None = None,
    ) -> tuple[UUID, str | None]:
        """确定本轮提问所属的会话，保证它归当前账号所有，并**原子地占下这次运行的位**。

        ``thread_id`` 为 ``None`` 表示新建会话：服务端生成 id、用首条提问当标题插入一行，并把该账号
        当前配置的提示词**快照**进这一行。非 ``None`` 表示续聊：校验归属，通过则把 ``last_active_at``
        推到当前时间，提示词沿用会话里已存的那份。

        **占位与归属校验必须是同一次条件写入。** 先查「这个会话有没有在途运行」再写，会把这个竞态
        原样留下，而且比改动前更隐蔽：现在是两次运行的结果都能看到，那样改完是「静默丢掉一次运行」。
        所以条件写在 ``UPDATE`` 的 ``WHERE`` 里：只有 ``active_run_id`` 为空、**或那次运行已超过失活
        阈值**（进程崩了，没有任何清理动作会执行）时，这一行才被占下。

        占位时**顺势清掉上一次留下的停止请求**。只在运行收尾时清是不够的：进程可能在清之前就被杀掉，
        留下一个陈旧的停止标记，于是刚起步的新运行一开场就被它停掉——那恰好是写入端用运行 id 比对
        想防的事，只是从写端挪到了读端（见 ADR 0037）。

        **提示词在建立时定下、续聊不重读偏好表，这是有意的**：用户在设置页改了提示词只该影响新开的
        会话，否则会话内的约束会中途变化，前后回答不再可比。注意这与同表 ``scope`` 列的语义相反
        （scope 续聊时可改），不要顺手把两处「修」成一致——理由见 ADR 0029。

        Args:
            user_id: 当前登录账号 id。
            thread_id: 前端要续聊的会话 id；``None`` 表示新建。
            first_message: 本轮提问原文，只在新建时用来取标题。
            run_id: 本次运行的标识；占位写的就是它，停止接口靠比对它才敢写停止标志。
            scope: 本次提交的知识库选择；``None`` 表示沿用/默认。

        Returns:
            ``(会话 id, 该会话的系统提示词)``。提示词为 ``None`` 表示这个会话用服务端内置默认提示词。
            返回它而不是让调用方再查一次，是为了让「取用会话值」只有一处实现。

        Raises:
            AgentThreadNotFoundError: ``thread_id`` 在库里没有，或者存在但属于别的账号。
            AgentRunInProgressError: 这个会话已经有一次运行在跑，且未超过失活阈值。
            SQLAlchemyError: 业务库不可用；由错误契约映射成 503。

        Notes:
            执行 PostgreSQL 写入（insert 或 update），一个事务内完成并提交，随后立刻归还连接。
            调用方必须在**开始运行之前** await 它：只有这样失败才能变成正常的 HTTP 状态码，
            运行一旦开始就只能走事件了。

            为什么续聊也要写一次：``last_active_at`` 是会话列表的排序键，不更新的话「最近聊过的
            排在最前」就不成立；运行期间由驱动者继续续期（见 ADR 0037）。
        """

        now = datetime.now(UTC)
        async with self._session_factory() as session:
            if thread_id is None:
                created_id = uuid4()
                # 建会话时从该账号的个人偏好拍一份提示词快照。取值为 None 表示没配过，
                # 运行时会回落到内置默认提示词（那段回落逻辑在 middleware 里，本次不改）。
                system_prompt = await UserPreferenceService(session).system_prompt_for(user_id)
                session.add(
                    AgentThreadRecord(
                        thread_id=created_id,
                        user_id=user_id,
                        title=derive_thread_title(first_message),
                        scope=(scope or KnowledgeBaseSelection(mode="all")).model_dump(mode="json"),
                        system_prompt=system_prompt,
                        created_at=now,
                        last_active_at=now,
                        # 新行不存在「上一次运行」这回事，所以占位必定成功。
                        active_run_id=run_id,
                        stop_requested_at=None,
                        drained_at=None,
                    )
                )
                await session.commit()
                return created_id, system_prompt

            # 用带 user_id 条件的 UPDATE 一次搞定「校验 + 续活 + 取值 + 占位」：先 SELECT 再
            # UPDATE 需要两次往返，而且中间存在窗口。rowcount 为 0 同时覆盖「id 不存在」和
            # 「id 属于别人」，正好对应合并成 404 的决定（见 AgentThreadNotFoundError 的 docstring）。
            zombie_cutoff = now - timedelta(seconds=RUN_ZOMBIE_THRESHOLD_SECONDS)
            result = await session.execute(
                update(AgentThreadRecord)
                .where(
                    AgentThreadRecord.thread_id == thread_id,
                    AgentThreadRecord.user_id == user_id,
                    # 带「等接手」标记的会话不能被新提问顶掉，**包括失活分支**：排空之后旧进程
                    # 不再续期活跃时间，两分钟后失活分支就会命中；不排除的话用户能在标记还在的
                    # 时候开新一轮，两个进程写同一份图状态（旧的 checkpoint 停在半途、还有待跑
                    # 的节点，新提问会顺着那份状态继续跑旧节点）。见 ADR 0040。
                    AgentThreadRecord.drained_at.is_(None),
                    or_(
                        AgentThreadRecord.active_run_id.is_(None),
                        AgentThreadRecord.last_active_at < zombie_cutoff,
                    ),
                )
                .values(
                    last_active_at=now,
                    active_run_id=run_id,
                    stop_requested_at=None,
                    **({"scope": scope.model_dump(mode="json")} if scope is not None else {}),
                )
                .returning(AgentThreadRecord.system_prompt)
            )
            row = result.first()
            if row is None:
                # 条件写入没命中，原因有两种，必须分开报：会话不是这个账号的（404，与「不存在」
                # 共用一个码，不泄露存在性），或者这个会话正在跑（409，用户知道「正在生成」就行）。
                # 这一次额外的 SELECT 只在失败路径上发生，不构成「先查后写」的竞态：占位与否早已
                # 由上面那条条件写入定下。
                owned = await session.scalar(
                    select(AgentThreadRecord.thread_id).where(
                        AgentThreadRecord.thread_id == thread_id,
                        AgentThreadRecord.user_id == user_id,
                    )
                )
                await session.rollback()
                if owned is None:
                    # 只记 id 和账号，不记提问内容。id 是我们自己生成的 UUID，不是用户输入。
                    logger.warning(
                        "拒绝访问不属于当前账号的会话 thread_id=%s user_id=%s",
                        thread_id,
                        user_id,
                    )
                    raise AgentThreadNotFoundError
                logger.info("拒绝重复提交 thread_id=%s run_id=%s", thread_id, run_id)
                raise AgentRunInProgressError
            await session.commit()
            return thread_id, row[0]

    async def finish_run(self, *, thread_id: UUID, run_id: UUID) -> None:
        """释放一次运行占下的会话位，并顺手清掉停止请求。

        Args:
            thread_id: 本次运行所属会话。
            run_id: 本次运行的标识。

        Raises:
            SQLAlchemyError: 业务库不可用；由调用方记日志（收尾失败不能反过来中断清理）。

        Notes:
            条件写的是 ``active_run_id == run_id``，**不是无条件清空**：占位可能已经被失活判定判给
            了下一次运行，那时这个收尾属于一个已经被顶掉的旧运行，不能把新运行的位子抹掉。

            执行一次 PostgreSQL 写入并提交。调用方是运行驱动者；它必须在发出终态事件**之前**调用
            这里，否则用户拿到 ``done`` 后立刻追问会被自己刚刚结束的那次运行拦成 409。
        """

        async with self._session_factory() as session:
            await session.execute(
                update(AgentThreadRecord)
                .where(
                    AgentThreadRecord.thread_id == thread_id,
                    AgentThreadRecord.active_run_id == run_id,
                )
                .values(active_run_id=None, stop_requested_at=None)
            )
            await session.commit()

    async def request_stop(self, *, thread_id: UUID, run_id: UUID, now: datetime | None = None) -> None:
        """写入一个停止请求。

        **只在「在途运行的 id 与请求里的运行 id 相等」时才写。** 这个标记是会话级的、不指向某次运行，
        而停止请求可能迟到：用户先点停止 → 旧运行已经收尾 → 用户发下一次提问 → 迟到的停止到达。
        不做比对的话，那个请求会把刚开始的新运行停掉。不相等就是「没有要停的运行」，幂等成功。

        Args:
            thread_id: 目标会话。
            run_id: 前端从 ``run_started`` 事件拿到的运行 id。
            now: 写入的时刻；省略时取当前时间，测试可以钉住它。

        Raises:
            SQLAlchemyError: 业务库不可用；由调用方映射成 503。

        Notes:
            执行一次 PostgreSQL 写入并提交。**不直接取消任何东西**：请求可能落在另一个进程上（生产
            ``WORKER_COUNT`` 默认大于 1），真正停下那次运行的是它所在进程的驱动者——它按自己的节奏
            批量读到这个标记，然后自己取消那次模型调用（见 ADR 0037）。
        """

        async with self._session_factory() as session:
            await session.execute(
                update(AgentThreadRecord)
                .where(
                    AgentThreadRecord.thread_id == thread_id,
                    AgentThreadRecord.active_run_id == run_id,
                )
                .values(stop_requested_at=now or datetime.now(UTC))
            )
            await session.commit()

    async def read_run_states(
        self,
        *,
        thread_ids: list[UUID],
    ) -> dict[UUID, tuple[UUID | None, datetime | None]]:
        """批量读取一批会话的在途运行 id 与停止请求时刻。

        每个 API 进程按固定节奏调一次，把自己手上在跑的几次运行的状态读进内存标志；用批量查询
        而不是逐个运行查一次，是为了让负载与「一个进程挂了多次运行」无关。

        Args:
            thread_ids: 本进程正在驱动的那几个会话。

        Returns:
            ``thread_id`` → ``(active_run_id, stop_requested_at)``。查不到的会话不出现在结果里。

        Raises:
            SQLAlchemyError: 业务库不可用；由调用方记日志后继续下一轮。

        Notes:
            只读，不写。空列表不发查询。
        """

        if not thread_ids:
            return {}
        async with self._session_factory() as session:
            rows = await session.execute(
                select(
                    AgentThreadRecord.thread_id,
                    AgentThreadRecord.active_run_id,
                    AgentThreadRecord.stop_requested_at,
                ).where(AgentThreadRecord.thread_id.in_(thread_ids))
            )
            return {row[0]: (row[1], row[2]) for row in rows.all()}

    async def touch_run(self, *, thread_id: UUID, run_id: UUID, now: datetime) -> int:
        """把在途运行的会话活跃时间推到 ``now``，供失活判定区分「在跑」与「进程已死」。

        Args:
            thread_id: 本次运行所属会话。
            run_id: 本次运行的标识。
            now: 写入的时刻，由调用方给出（便于测试钉住时间）。

        Returns:
            实际更新的行数；``0`` 表示这次运行已经不再占着这个会话（被失活判定顶掉了）。

        Raises:
            SQLAlchemyError: 业务库不可用；由调用方记日志后继续。

        Notes:
            执行一次 PostgreSQL 写入并提交。续期的节奏由驱动者按常量决定，不由「一次模型调用跑了
            多久」决定——那条链路上有重试、降级和工具调用，任何按它推算的阈值都会算错（ADR 0037）。
        """

        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentThreadRecord)
                .where(
                    AgentThreadRecord.thread_id == thread_id,
                    AgentThreadRecord.active_run_id == run_id,
                )
                .values(last_active_at=now)
            )
            await session.commit()
            return result.rowcount or 0

    async def mark_drained(self, *, thread_id: UUID, run_id: UUID, now: datetime | None = None) -> int:
        """给一次走到可交接边界的运行写下「等接手」标记。

        条件写的是 ``active_run_id == run_id``，与释放占位同一个道理：这次运行可能已经被失活
        判定顶掉，那时不能把标记写到新运行头上（否则接手者会拿新的运行 id 去接旧 checkpoint）。

        Args:
            thread_id: 本次运行所属会话。
            run_id: 本次运行的标识。
            now: 写入的时刻；省略时取当前时间，测试可以钉住它。

        Returns:
            实际更新的行数；``0`` 表示这次运行已经不再占着这个会话，标记未写。

        Raises:
            SQLAlchemyError: 业务库不可用；由调用方记日志（收尾失败不能反过来中断退出）。

        Notes:
            执行一次 PostgreSQL 写入并提交。标记得以存在的前提是 ``active_run_id`` 还在——接手
            者靠它确认「该接着跑哪一次运行」。
        """

        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentThreadRecord)
                .where(
                    AgentThreadRecord.thread_id == thread_id,
                    AgentThreadRecord.active_run_id == run_id,
                )
                .values(drained_at=now or datetime.now(UTC))
            )
            await session.commit()
            return result.rowcount or 0

    async def list_drained_thread_ids(self) -> list[UUID]:
        """列出所有带「等接手」标记的会话。

        接手者按固定节奏调它，扫到什么就接什么，不设「一轮最多接几个」的上限——那些运行本来
        就在跑，换个进程跑不会增加负载。

        Returns:
            带标记的会话 id，顺序不保证。

        Raises:
            SQLAlchemyError: 业务库不可用；由调用方记日志后继续下一轮。

        Notes:
            只读，不写。只取 id：随后抢所有权的那条条件写入才是「算不算抢到」的判定点。
        """

        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AgentThreadRecord.thread_id).where(
                    AgentThreadRecord.drained_at.is_not(None)
                )
            )
            return list(rows)

    async def claim_drained_run(self, *, thread_id: UUID) -> DrainedRunClaim | None:
        """抢一条「等接手」运行的所有权；抢到才返回它。

        **判标记非空与置空必须放在同一条语句里**：分开做会出现两个进程都读到标记、都以为自己
        接手的窗口，同一轮运行被两处同时写入。条件更新天然解决它——``rowcount`` 为 1 才算抢到，
        第二个进程下一轮就扫不到这个标记了。

        **这次更新不碰 ``stop_requested_at``**：用户在部署窗口里按下的停止要照样生效，接手
        之后的驱动者还要能读到它并停下这次运行。

        Args:
            thread_id: 待接手的会话。

        Returns:
            抢到时的 ``DrainedRunClaim``；没抢到（标记已被别的进程消费）返回 ``None``。

        Raises:
            SQLAlchemyError: 业务库不可用；由调用方记日志后继续下一轮。

        Notes:
            执行一条 PostgreSQL 条件写入并提交。取回运行 id、账号与会话提示词，让接手方不必再
            查一次会话行；知识库范围**不在这里取**，它在 checkpoint 里。
        """

        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentThreadRecord)
                .where(
                    AgentThreadRecord.thread_id == thread_id,
                    AgentThreadRecord.drained_at.is_not(None),
                )
                .values(drained_at=None)
                .returning(
                    AgentThreadRecord.active_run_id,
                    AgentThreadRecord.user_id,
                    AgentThreadRecord.system_prompt,
                )
            )
            row = result.first()
            await session.commit()
            if row is None:
                return None
            active_run_id, user_id, system_prompt = row
            if active_run_id is None:
                # 标记与在途运行并存是写入端保证的；真出现「只有标记、没有在途运行」，把它
                # 当作一次没有可接手对象的扫描（上面的 SET 已经清掉标记，会话因此解锁）。
                logger.warning("排空标记没有对应的在途运行 thread_id=%s", thread_id)
                return None
            return DrainedRunClaim(
                thread_id=thread_id,
                run_id=active_run_id,
                user_id=user_id,
                system_prompt=system_prompt,
            )

    async def update_scope(self, *, user_id: UUID, thread_id: UUID, scope: KnowledgeBaseSelection) -> None:
        """保存经应用校验的选择；不改正在执行的运行快照，不刷新最近提问时间。"""
        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentThreadRecord)
                .where(AgentThreadRecord.thread_id == thread_id, AgentThreadRecord.user_id == user_id)
                .values(scope=scope.model_dump(mode="json"))
            )
            if result.rowcount == 0:
                raise AgentThreadNotFoundError
            await session.commit()

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

    async def list_threads_before(self, cutoff: datetime) -> list[AgentThreadRecord]:
        """读取最后活跃早于指定时间的所有会话，供旧会话清理命令使用。

        Args:
            cutoff: 时间截止点；最后活跃早于此时间的会话将被返回。

        Returns:
            符合条件的会话记录列表。

        Raises:
            SQLAlchemyError: 业务库不可用。

        Notes:
            只读、不分账号——它服务的是运维命令 ``prune-old-threads``。返回完整记录而非只返回 id，
            是为了让调用方能记录每个被删会话的标题和最后活跃时间。
        """

        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AgentThreadRecord)
                .where(AgentThreadRecord.last_active_at < cutoff)
                .order_by(AgentThreadRecord.last_active_at.asc())
            )
            return list(rows)

    async def delete_threads(self, thread_ids: list[str]) -> int:
        """批量删除指定 id 的会话归属记录，供旧会话清理命令使用。

        Args:
            thread_ids: 要删除的会话 id 列表，已转成字符串。

        Returns:
            实际删除的记录数。

        Raises:
            SQLAlchemyError: 业务库不可用。

        Notes:
            只删本表，**不删 checkpointer 里的历史**。调用方必须先删 checkpointer 历史，
            成功后再调本方法。不校验归属——它服务的是运维命令，操作的是跨账号清理。
        """

        if not thread_ids:
            return 0

        # 转回 UUID：业务表存的是 UUID 类型，传入的是字符串（与 checkpointer 一致）
        uuids = [UUID(tid) for tid in thread_ids]

        async with self._session_factory() as session:
            result = await session.execute(
                delete(AgentThreadRecord).where(AgentThreadRecord.thread_id.in_(uuids))
            )
            await session.commit()
            return result.rowcount or 0



__all__ = [
    "FALLBACK_THREAD_TITLE",
    "MAX_THREAD_TITLE_CHARS",
    "AgentThreadService",
    "DrainedRunClaim",
    "derive_thread_title",
]
