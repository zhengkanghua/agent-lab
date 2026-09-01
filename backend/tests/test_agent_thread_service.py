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

from agent_lab.agent.errors import AgentThreadNotFoundError
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
    """只带 ``rowcount`` 的假执行结果。"""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


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
    ) -> None:
        self._rowcount = rowcount
        self._scalar_result = scalar_result
        self._scalars_result = scalars_result or []
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: Any) -> FakeResult:
        """记下语句并返回预置 rowcount。"""

        self.statements.append(statement)
        return FakeResult(self._rowcount)

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

    created = run(
        service_with(session).ensure_thread(
            user_id=user_id,
            thread_id=None,
            first_message="央行降息了吗",
        )
    )

    assert len(session.added) == 1
    record = session.added[0]
    assert record.thread_id == created
    assert record.user_id == user_id
    assert record.title == "央行降息了吗"
    # 新建路径不该跑 UPDATE：跑了说明「新建」和「续聊」两条分支缠在一起了。
    assert session.statements == []
    assert (session.commits, session.rollbacks) == (1, 0)


def test_new_thread_id_is_generated_server_side_not_taken_from_input() -> None:
    """服务端自己生成 id：两次新建拿到的 id 不同。

    这条挡的是「把客户端传来的值当新 id 用」这类改动——那等于把 id 的控制权交回前端，
    归属校验就成了摆设。
    """

    first = run(
        service_with(FakeSession()).ensure_thread(
            user_id=uuid4(), thread_id=None, first_message="问题"
        )
    )
    second = run(
        service_with(FakeSession()).ensure_thread(
            user_id=uuid4(), thread_id=None, first_message="问题"
        )
    )

    assert first != second


def test_continuing_a_thread_filters_by_both_thread_id_and_user_id() -> None:
    """续聊走 UPDATE，且 where 里 **两个** 条件都在。

    这是本文件最重要的一条。少了 ``user_id`` 条件，任何人猜到 id 就能续别人的会话——
    正是本次改动要修的漏洞。
    """

    session = FakeSession(rowcount=1)
    user_id = uuid4()
    thread_id = uuid4()

    returned = run(
        service_with(session).ensure_thread(
            user_id=user_id,
            thread_id=thread_id,
            first_message="继续",
        )
    )

    assert returned == thread_id
    assert len(session.statements) == 1
    sql = compiled(session.statements[0])
    assert sql.lstrip().upper().startswith("UPDATE")
    assert str(thread_id) in sql
    assert str(user_id) in sql
    assert (session.commits, session.rollbacks) == (1, 0)


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
                first_message="继续",
            )
        )

    assert (session.commits, session.rollbacks) == (0, 1)
    assert session.added == []


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
            user_id=uuid4(), thread_id=None, first_message=message
        )
    )

    assert session.added[0].title == derive_thread_title(message)
