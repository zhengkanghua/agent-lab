"""``services/agent_thread_service.py`` 的离线测试。

不连 PostgreSQL：用一个只实现 ``AsyncSession`` 必要方法的替身，记录被执行的语句，然后断言编译出来
的 SQL 里带着归属条件。

**这里能证明什么、不能证明什么**（重要，别把它当成归属安全的全部保障）：

- 能证明：语句里确实带 ``user_id`` 条件、rowcount 为 0 时抛的是 ``AgentThreadNotFoundError``、
  失败路径 rollback 而不是 commit、标题按规则截断。
- 不能证明：那条 SQL 在真实 PostgreSQL 上确实只匹配到自己的行。编译文本对了不等于运行结果对。

这个缺口由 ``test_agent_thread_ownership_integration.py`` 补，它跑真库、默认跳过。归属是本次改动
的安全核心，所以两层都要有——只有语句级测试的话，一次 ORM 用法失误（比如把 ``where`` 写成两次调用
覆盖掉前一个条件）在这里照样通过。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from agent_lab.agent.errors import AgentRunInProgressError, AgentThreadNotFoundError
from agent_lab.models.agent_thread import AgentThreadRecord
from agent_lab.services.agent_thread_service import (
    FALLBACK_THREAD_TITLE,
    MAX_THREAD_TITLE_CHARS,
    AgentThreadService,
    derive_thread_title,
)
from tests.agent_helpers import run


def compiled(statement: Any) -> str:
    """把 SQLAlchemy 语句编译成 PostgreSQL 方言的 SQL 文本。

    Args:
        statement: 被 Service 执行过的语句对象。

    Returns:
        带字面量参数的 SQL 字符串，便于断言 where 条件真的在里面。
    """

    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class FakeResult:
    """带 ``rowcount`` 与 ``returning`` 行集的假执行结果。

    ``first`` 是 ``ensure_thread`` 取回会话提示词用的：它走 ``UPDATE ... RETURNING``，
    一次往返同时完成「校验 + 续活 + 取值」。这里返回的行由 ``returning_row`` 预置，
    ``None`` 表示没更新到任何行（等价于 rowcount 为 0）。
    """

    def __init__(self, rowcount: int, returning_row: tuple[Any, ...] | None = None) -> None:
        self.rowcount = rowcount
        self._returning_row = returning_row

    def first(self) -> tuple[Any, ...] | None:
        """返回预置的 RETURNING 行；没有则返回 ``None``。"""

        return self._returning_row


class FakeScalars:
    """``session.scalars`` 的返回值替身，可迭代。"""

    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def __iter__(self) -> Any:
        return iter(self._values)


class FakeSession:
    """记录语句与事务动作的假 ``AsyncSession``。

    只实现 Service 真正用到的那几个方法。刻意不做成「万能替身」：多实现一个方法，就多一处
    「Service 换了用法但测试仍然通过」的可能。

    Attributes:
        statements: 被 ``execute`` 的语句，按顺序。
        added: 被 ``add`` 的 ORM 实例。
        commits / rollbacks: 各自被调用的次数。
    """

    def __init__(
        self,
        *,
        rowcount: int = 1,
        scalar_result: Any = None,
        scalars_result: list[Any] | None = None,
        returning_row: tuple[Any, ...] | None = None,
    ) -> None:
        self._rowcount = rowcount
        self._scalar_result = scalar_result
        self._scalars_result = scalars_result or []
        self._returning_row = returning_row
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: Any) -> FakeResult:
        """记下语句并返回预置 rowcount 与 RETURNING 行。"""

        self.statements.append(statement)
        return FakeResult(self._rowcount, self._returning_row)

    async def scalar(self, statement: Any) -> Any:
        """记下语句并返回预置标量。"""

        self.statements.append(statement)
        return self._scalar_result

    async def scalars(self, statement: Any) -> FakeScalars:
        """记下语句并返回预置集合。"""

        self.statements.append(statement)
        return FakeScalars(self._scalars_result)

    def add(self, instance: Any) -> None:
        """记下待插入实例。"""

        self.added.append(instance)

    async def commit(self) -> None:
        """记一次提交。"""

        self.commits += 1

    async def rollback(self) -> None:
        """记一次回滚。"""

        self.rollbacks += 1

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None


class FakeSessionFactory:
    """每次调用返回同一个 ``FakeSession``，便于调用后检查它。"""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        return self.session


def service_with(session: FakeSession) -> AgentThreadService:
    """用假 session 工厂构造真实 Service。"""

    return AgentThreadService(FakeSessionFactory(session))  # type: ignore[arg-type]


def test_new_thread_is_inserted_with_the_calling_account_as_owner() -> None:
    """``thread_id`` 为 None 时插入新行，归属写的是传进来的账号。"""

    session = FakeSession()
    user_id = uuid4()

    created, prompt = run(
        service_with(session).ensure_thread(
            user_id=user_id,
            thread_id=None,
            run_id=uuid4(),
            first_message="央行降息了吗",
        )
    )

    assert len(session.added) == 1
    record = session.added[0]
    assert record.thread_id == created
    assert record.user_id == user_id
    assert record.title == "央行降息了吗"
    # 没配过偏好的账号：快照为空，本轮由运行时回落到内置默认提示词。
    assert prompt is None
    assert record.system_prompt is None
    # 新建路径不该跑 UPDATE：跑了说明「新建」和「续聊」两条分支缠在一起了。
    # 唯一那条 SELECT 是读该账号的个人偏好，用来给会话拍快照。
    assert len(session.statements) == 1
    assert compiled(session.statements[0]).lstrip().upper().startswith("SELECT")
    assert (session.commits, session.rollbacks) == (1, 0)


def test_new_thread_snapshots_the_account_preference_prompt() -> None:
    """建会话时把该账号配的提示词写进会话行，并原样返回给调用方。

    「快照」的含义就在这里：会话行存下建立那一刻的值，之后续聊不再回读偏好表。
    """

    session = FakeSession(scalar_result="只用一句话回答。")
    user_id = uuid4()

    created, prompt = run(
        service_with(session).ensure_thread(
            user_id=user_id, thread_id=None, run_id=uuid4(), first_message="问题"
        )
    )

    assert prompt == "只用一句话回答。"
    assert session.added[0].system_prompt == "只用一句话回答。"
    assert session.added[0].thread_id == created


def test_new_thread_id_is_generated_server_side_not_taken_from_input() -> None:
    """服务端自己生成 id：两次新建拿到的 id 不同。

    这条挡的是「把客户端传来的值当新 id 用」这类改动——那等于把 id 的控制权交回前端，
    归属校验就成了摆设。
    """

    first, _ = run(
        service_with(FakeSession()).ensure_thread(
            user_id=uuid4(), thread_id=None, run_id=uuid4(), first_message="问题"
        )
    )
    second, _ = run(
        service_with(FakeSession()).ensure_thread(
            user_id=uuid4(), thread_id=None, run_id=uuid4(), first_message="问题"
        )
    )

    assert first != second


def test_continuing_a_thread_filters_by_both_thread_id_and_user_id() -> None:
    """续聊走 UPDATE，且 where 里 **两个** 条件都在。

    这是本文件最重要的一条。少了 ``user_id`` 条件，任何人猜到 id 就能续别人的会话——
    正是本次改动要修的漏洞。
    """

    session = FakeSession(rowcount=1, returning_row=("会话里存的那份提示词。",))
    user_id = uuid4()
    thread_id = uuid4()

    returned, prompt = run(
        service_with(session).ensure_thread(
            user_id=user_id,
            thread_id=thread_id,
            run_id=uuid4(),
            first_message="继续",
        )
    )

    assert returned == thread_id
    # 续聊的提示词来自会话行，不是偏好表——UPDATE 上没有第二条 SELECT 就是证据。
    assert prompt == "会话里存的那份提示词。"
    assert len(session.statements) == 1
    sql = compiled(session.statements[0])
    assert sql.lstrip().upper().startswith("UPDATE")
    assert str(thread_id) in sql
    assert str(user_id) in sql
    assert (session.commits, session.rollbacks) == (1, 0)


def test_continuing_a_thread_never_writes_the_prompt_column() -> None:
    """续聊的 UPDATE **不改** ``system_prompt``——这是「会话级快照」在语句层的落点。

    上一条只断言「续聊只有一条 UPDATE」，挡不住「往这条 UPDATE 的 ``.values()`` 里加一个
    ``system_prompt=...``」——那种改动会让会话中途换设定，而语句条数不变、上一条照样绿。
    所以这里单独看 SET 子句：``scope`` 可以改（改选是允许的），提示词不可以。

    注意 RETURNING 里**有** ``system_prompt``，那是取回会话值用的，不是写入。所以不能整条
    SQL 搜字段名，只能看 SET 与 WHERE 之间那一段。
    """

    session = FakeSession(rowcount=1, returning_row=(None,))
    user_id, thread_id = uuid4(), uuid4()

    run(
        service_with(session).ensure_thread(
            user_id=user_id, thread_id=thread_id, run_id=uuid4(), first_message="继续"
        )
    )

    sql = compiled(session.statements[0]).upper()
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert "SYSTEM_PROMPT" not in assignments
    # 正面那一半：``last_active_at`` 是这条 UPDATE 必写的列，它不在说明 SET 子句根本没取到，
    # 那样上面的断言就是空转。
    assert "LAST_ACTIVE_AT" in assignments
    # 取回会话值用的 RETURNING 里**有**这一列，这是对的——所以上面只能看 SET 段。
    assert "RETURNING" in sql and "SYSTEM_PROMPT" in sql.split("RETURNING", 1)[1]


def test_continuing_someone_elses_thread_rolls_back_and_raises() -> None:
    """rowcount 为 0 时抛 ``AgentThreadNotFoundError``，并且回滚而不是提交。

    rowcount 为 0 同时对应「id 不存在」和「id 属于别人」，两者共用一个 404 是刻意的
    （区分开就成了枚举预言机）。这里顺带钉住「失败不提交」：提交一个空事务不会有数据后果，
    但会掩盖「本来该改一行却改了零行」这件事。
    """

    session = FakeSession(rowcount=0)

    with pytest.raises(AgentThreadNotFoundError):
        run(
            service_with(session).ensure_thread(
                user_id=uuid4(),
                thread_id=uuid4(),
                run_id=uuid4(),
                first_message="继续",
            )
        )

    assert (session.commits, session.rollbacks) == (0, 1)
    assert session.added == []


def test_claiming_a_thread_requires_no_live_run_and_clears_the_stale_stop_request() -> None:
    """占位的条件写在 UPDATE 的 WHERE 里，并在同一次写入里清掉陈旧的停止请求。

    **不能先查后写**：那样会把这个竞态原样留下，而且比改动前更隐蔽（现在是两次运行的结果都能
    看到，那样改完是「静默丢掉一次运行」）。所以这里断言的是语句文本本身：

    - WHERE 里同时有「没有被占」与「上次运行已经失活」两个出口，它们用 OR 连；
    - SET 里把 ``stop_requested_at`` 置空。只在运行收尾时清是不够的——进程可能在清之前就被
      杀掉，留下一个陈旧的停止标记，于是刚起步的新运行一开场就被它停掉。
    """

    session = FakeSession(rowcount=1, returning_row=(None,))
    run_id = uuid4()

    run(
        service_with(session).ensure_thread(
            user_id=uuid4(),
            thread_id=uuid4(),
            run_id=run_id,
            first_message="继续",
        )
    )

    sql = compiled(session.statements[0]).upper()
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    conditions = sql.split(" WHERE ", 1)[1]
    assert "ACTIVE_RUN_ID" in assignments
    assert "STOP_REQUESTED_AT=NULL" in assignments
    assert str(run_id).upper() in assignments
    assert "ACTIVE_RUN_ID IS NULL" in conditions
    assert "LAST_ACTIVE_AT <" in conditions
    assert " OR " in conditions


def test_a_live_run_on_the_thread_rejects_the_second_submission() -> None:
    """条件写入没命中、但会话确实存在时，报的是「正在生成」而不是 404。

    这两种失败必须分开：会话不是这个账号的要报 404（与「不存在」共用一个码，不泄露存在性），
    而会话在跑要报 409——用户需要知道的是「等一会」或「先按停止」。两者的区分只能靠拿到
    rowcount 之后补的那一次 SELECT，它用的是同一个归属条件，所以对别人的会话仍然报 404。
    """

    thread_id = uuid4()
    # 那一次补查（scalar）返回了行：说明会话存在且属于本账号，只是被占着。
    session = FakeSession(rowcount=0, scalar_result=thread_id)

    with pytest.raises(AgentRunInProgressError):
        run(
            service_with(session).ensure_thread(
                user_id=uuid4(),
                thread_id=thread_id,
                run_id=uuid4(),
                first_message="继续",
            )
        )

    assert (session.commits, session.rollbacks) == (0, 1)
    assert session.added == []


def test_finishing_a_run_only_releases_its_own_claim() -> None:
    """收尾只释放「占位的就是这次运行」的那个位。

    无条件清空会在被失活判定顶掉的旧运行收尾时，把新运行刚占的位抹掉——那时两个运行都能写
    同一个会话。所以条件里必须有 ``active_run_id == run_id``。
    """

    session = FakeSession()
    thread_id, run_id = uuid4(), uuid4()

    run(service_with(session).finish_run(thread_id=thread_id, run_id=run_id))

    sql = compiled(session.statements[0]).upper()
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    conditions = sql.split(" WHERE ", 1)[1]
    assert "ACTIVE_RUN_ID=NULL" in assignments
    assert "STOP_REQUESTED_AT=NULL" in assignments
    assert "ACTIVE_RUN_ID =" in conditions and str(run_id).upper() in conditions
    assert str(thread_id).upper() in conditions
    assert session.commits == 1


def test_touching_a_run_only_extends_a_claim_it_still_holds() -> None:
    """续活只写「占位的就是这次运行」的那一行，并把活跃时间推到给定时时刻。"""

    session = FakeSession(rowcount=1)
    thread_id, run_id = uuid4(), uuid4()
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

    updated = run(
        service_with(session).touch_run(thread_id=thread_id, run_id=run_id, now=now)
    )

    assert updated == 1
    sql = compiled(session.statements[0]).upper()
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    conditions = sql.split(" WHERE ", 1)[1]
    assert "LAST_ACTIVE_AT" in assignments
    assert "ACTIVE_RUN_ID =" in conditions and str(run_id).upper() in conditions


def test_requesting_a_stop_only_touches_the_run_it_names() -> None:
    """写停止标志的条件里必须有运行 id。

    停止请求可能迟到：用户先点停止 → 旧运行已经收尾 → 用户发下一次提问 → 那个请求才到达。少了
    这个条件，刚起步的新运行会被一开场就停掉——而「界面看起来停了、模型继续烧钱」正是这条链路要
    防的事。这里只能证明条件写在语句里；它在真库上真的只命中那一行由
    ``test_agent_thread_ownership_integration.py`` 的串行占位用例覆盖。
    """

    session = FakeSession()
    thread_id, run_id = uuid4(), uuid4()
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

    run(service_with(session).request_stop(thread_id=thread_id, run_id=run_id, now=now))

    sql = compiled(session.statements[0]).upper()
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    conditions = sql.split(" WHERE ", 1)[1]
    assert "STOP_REQUESTED_AT" in assignments
    assert str(run_id).upper() in conditions
    assert str(thread_id).upper() in conditions
    assert session.commits == 1


def test_reading_run_states_batches_by_the_given_threads() -> None:
    """批量读那两列，并且只读本进程手上在跑的那几个会话。

    「批量」而不是逐个查一次，是为了让负载与「一个进程挂了多次运行」无关（ADR 0037）。
    """

    thread_id = uuid4()

    class _Result:
        """只实现 Service 用到的 ``.all()`` 的行集替身。"""

        def all(self):
            return [(thread_id, None, None)]

    class _Session(FakeSession):
        """让 ``execute`` 返回可 ``.all()`` 的结果。"""

        async def execute(self, statement):  # type: ignore[override]
            self.statements.append(statement)
            return _Result()

    fake = _Session()
    states = run(service_with(fake).read_run_states(thread_ids=[thread_id]))

    assert states == {thread_id: (None, None)}
    sql = compiled(fake.statements[0]).upper()
    assert sql.lstrip().startswith("SELECT")
    assert str(thread_id).upper() in sql


def test_reading_run_states_with_no_runs_does_not_query() -> None:
    """没有在途运行时一次查询都不发（那个协程每秒都要跑一遍）。"""

    session = FakeSession()

    assert run(service_with(session).read_run_states(thread_ids=[])) == {}
    assert session.statements == []


def test_reading_one_thread_filters_by_owner() -> None:
    """``get_owned_thread`` 的 SELECT 带 ``user_id`` 条件。"""

    thread_id = uuid4()
    user_id = uuid4()
    now = datetime.now(UTC)
    expected = AgentThreadRecord(
        thread_id=thread_id,
        user_id=user_id,
        title="标题",
        created_at=now,
        last_active_at=now,
    )
    session = FakeSession(scalar_result=expected)

    record = run(
        service_with(session).get_owned_thread(user_id=user_id, thread_id=thread_id)
    )

    assert record is expected
    sql = compiled(session.statements[0])
    assert str(thread_id) in sql
    assert str(user_id) in sql


def test_reading_a_thread_that_is_not_yours_raises_not_found() -> None:
    """查不到行就抛 404 对应的异常，不返回 ``None`` 让调用方去判空。

    返回 None 的设计会把「判断归属」这件事分散到每个调用方，漏一处就是一个漏洞。
    """

    session = FakeSession(scalar_result=None)

    with pytest.raises(AgentThreadNotFoundError):
        run(
            service_with(session).get_owned_thread(
                user_id=uuid4(), thread_id=uuid4()
            )
        )


def test_deleting_a_thread_record_filters_by_owner() -> None:
    """``delete_thread_record`` 的 DELETE 带 ``user_id`` 条件。

    删除路径漏掉归属条件比读取更严重：读到别人的会话是泄露，删掉别人的会话是不可逆的数据丢失。
    """

    session = FakeSession(rowcount=1)
    user_id = uuid4()
    thread_id = uuid4()

    run(
        service_with(session).delete_thread_record(
            user_id=user_id, thread_id=thread_id
        )
    )

    sql = compiled(session.statements[0])
    assert sql.lstrip().upper().startswith("DELETE")
    assert str(thread_id) in sql
    assert str(user_id) in sql
    assert (session.commits, session.rollbacks) == (1, 0)


def test_deleting_someone_elses_thread_record_rolls_back_and_raises() -> None:
    """删不到行时回滚并抛异常。"""

    session = FakeSession(rowcount=0)

    with pytest.raises(AgentThreadNotFoundError):
        run(
            service_with(session).delete_thread_record(
                user_id=uuid4(), thread_id=uuid4()
            )
        )

    assert (session.commits, session.rollbacks) == (0, 1)


def test_listing_threads_filters_by_owner_and_sorts_by_recent_activity() -> None:
    """列表查询带归属条件，且按最近活跃倒序、带次级排序键。

    次级键 ``thread_id`` 不是装饰：只按 ``last_active_at`` 排序时，同一毫秒的两行顺序不确定，
    翻页会出现某一行两页都不露面。这条断言把它钉住。
    """

    session = FakeSession(scalar_result=7, scalars_result=[])
    user_id = uuid4()

    records, total = run(
        service_with(session).list_threads(user_id=user_id, limit=20, offset=40)
    )

    assert records == []
    assert total == 7
    page_sql = compiled(session.statements[0]).upper()
    assert str(user_id).upper() in page_sql
    assert "ORDER BY" in page_sql
    assert "LAST_ACTIVE_AT DESC" in page_sql
    assert "THREAD_ID DESC" in page_sql
    assert "LIMIT 20" in page_sql
    assert "OFFSET 40" in page_sql
    # 总数是单独一次 count 查询，不是窗口函数：空结果页也要能拿到总数。
    count_sql = compiled(session.statements[1]).upper()
    assert "COUNT" in count_sql
    assert str(user_id).upper() in count_sql


def test_listing_threads_reports_zero_when_count_comes_back_none() -> None:
    """count 查询返回 ``None`` 时总数按 0 算，不把 ``None`` 泄进响应模型。"""

    session = FakeSession(scalar_result=None, scalars_result=[])

    _records, total = run(
        service_with(session).list_threads(user_id=uuid4(), limit=20, offset=0)
    )

    assert total == 0


def test_known_thread_ids_ignores_account_boundaries() -> None:
    """``list_known_thread_ids`` 不按账号过滤——它服务的是全库孤儿清理。

    这里刻意与其余方法相反：清理命令判断的是「checkpointer 有历史、业务表没归属」，
    按账号过滤会把别人的会话误判成孤儿删掉。
    """

    ids = [uuid4(), uuid4()]
    session = FakeSession(scalars_result=ids)

    known = run(service_with(session).list_known_thread_ids())

    assert known == set(ids)
    assert "user_id" not in compiled(session.statements[0]).lower()


def test_title_keeps_a_short_question_verbatim() -> None:
    """短提问原样当标题。"""

    assert derive_thread_title("央行降息了吗") == "央行降息了吗"


def test_title_collapses_newlines_and_runs_of_whitespace() -> None:
    """换行和连续空白折成单个空格，两端去掉。

    标题在列表里是一行。原文里的换行会在某些浏览器上撑高行盒，让列表高低不齐。
    """

    assert derive_thread_title("  第一行\n\n第二行\t第三行  ") == "第一行 第二行 第三行"


def test_title_is_truncated_to_the_column_limit_without_ellipsis() -> None:
    """超长提问截到列宽上限，且**不加**省略号。

    截断长度必须与 ``AgentThreadRecord.title`` 的 ``String(60)`` 一致，否则插入时报
    ``StringDataRightTruncation``——那是个 500，而起因只是有人提了个长问题。

    不加省略号是刻意的：省略号交给前端 CSS 的 text-overflow，宽屏放得下整句时不该带个多余的点。
    """

    title = derive_thread_title("话" * 200)

    assert len(title) == MAX_THREAD_TITLE_CHARS
    assert title == "话" * MAX_THREAD_TITLE_CHARS
    assert "…" not in title
    assert not title.endswith("...")


def test_blank_question_falls_back_to_a_fixed_title() -> None:
    """纯空白提问用兜底标题，不让 NOT NULL 列收到空串。

    正常路径到不了这里（``AgentChatRequest`` 已经拒绝纯空白），但标题列非空，
    留一个确定值比让数据库报约束错误好。
    """

    assert derive_thread_title("   \n\t  ") == FALLBACK_THREAD_TITLE


def test_new_thread_title_comes_from_the_same_rule_as_the_helper() -> None:
    """插入时用的标题与 ``derive_thread_title`` 完全一致，规则只有一处。

    Service 里如果另写一遍截断逻辑，两处就会慢慢跑偏，其中一处迟早超过列宽。
    """

    session = FakeSession()
    message = "  很长的提问　" + "话" * 200

    run(
        service_with(session).ensure_thread(
            user_id=uuid4(), thread_id=None, run_id=uuid4(), first_message=message
        )
    )

    assert session.added[0].title == derive_thread_title(message)


def test_claiming_a_thread_excludes_a_drained_one_from_the_zombie_branch() -> None:
    """占位那条条件写入把带「等接手」标记的会话整个排除掉，**包括失活分支**。

    排空之后旧进程不再续期活跃时间，两分钟后失活分支就会命中。不排除的话，用户能在标记还在的
    时候开新一轮，而旧的 checkpoint 停在半途、还有待跑的节点——两个进程会写同一份图状态。
    """

    session = FakeSession(rowcount=1, returning_row=(None,))

    run(
        service_with(session).ensure_thread(
            user_id=uuid4(),
            thread_id=uuid4(),
            run_id=uuid4(),
            first_message="继续",
        )
    )

    conditions = compiled(session.statements[0]).upper().split(" WHERE ", 1)[1]
    assert "DRAINED_AT IS NULL" in conditions
    # 正面那一半：失活分支还在，说明我们不是把整段条件删了。
    assert "ACTIVE_RUN_ID IS NULL" in conditions
    assert "LAST_ACTIVE_AT <" in conditions


def test_marking_a_run_drained_only_touches_its_own_claim() -> None:
    """写标记的条件是「占位的就是这次运行」——被失活判定顶掉的旧运行不能把标记写到新运行头上。"""

    session = FakeSession(rowcount=1)
    thread_id, run_id = uuid4(), uuid4()
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

    updated = run(
        service_with(session).mark_drained(thread_id=thread_id, run_id=run_id, now=now)
    )

    assert updated == 1
    sql = compiled(session.statements[0]).upper()
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    conditions = sql.split(" WHERE ", 1)[1]
    assert "DRAINED_AT" in assignments
    assert "ACTIVE_RUN_ID =" in conditions and str(run_id).upper() in conditions
    assert str(thread_id).upper() in conditions
    assert session.commits == 1


def test_claiming_a_drained_run_consumes_the_marker_in_one_write() -> None:
    """抢所有权是「判标记非空 + 置空」的同一条语句，且不碰停止请求那一列。

    分开做会出现两个进程都读到标记、都以为自己接手的窗口。不碰停止请求是因为用户在部署窗口里
    按下的停止要照样生效——接手之后的驱动者还要能读到它并停下这次运行。
    """

    thread_id, run_id, user_id = uuid4(), uuid4(), uuid4()
    session = FakeSession(returning_row=(run_id, user_id, "会话里存的提示词。"))

    claim = run(service_with(session).claim_drained_run(thread_id=thread_id))

    assert claim is not None
    assert (claim.thread_id, claim.run_id, claim.user_id) == (thread_id, run_id, user_id)
    assert claim.system_prompt == "会话里存的提示词。"
    sql = compiled(session.statements[0]).upper()
    assignments = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    conditions = sql.split(" WHERE ", 1)[1]
    assert "DRAINED_AT=NULL" in assignments
    assert "DRAINED_AT IS NOT NULL" in conditions
    assert "STOP_REQUESTED_AT" not in assignments
    returning = sql.split("RETURNING", 1)[1]
    assert "ACTIVE_RUN_ID" in returning
    assert "USER_ID" in returning
    assert "SYSTEM_PROMPT" in returning
    assert session.commits == 1


def test_claiming_a_drained_run_returns_nothing_when_the_marker_is_gone() -> None:
    """标记已经被别的进程消费时返回 ``None``，调用方据此什么都不做。"""

    session = FakeSession(returning_row=None)

    assert run(service_with(session).claim_drained_run(thread_id=uuid4())) is None


def test_listing_drained_threads_reads_only_flagged_rows() -> None:
    """扫描只取带标记的会话，不把整个会话表拉出来。"""

    thread_id = uuid4()
    session = FakeSession(scalars_result=[thread_id])

    ids = run(service_with(session).list_drained_thread_ids())

    assert ids == [thread_id]
    sql = compiled(session.statements[0]).upper()
    assert sql.lstrip().startswith("SELECT")
    assert "DRAINED_AT IS NOT NULL" in sql
