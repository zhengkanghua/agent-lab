"""用量记录的仓储：只做追加与只读查询，不做修改与删除。

写入用「插入冲突就什么都不做」（``ON CONFLICT (call_id) DO NOTHING``）表达幂等，而不是先查后
插：查询加插入在多个 API 进程并发时仍然会撞唯一约束，撞上就要回滚整个事务重试；让数据库自己
处理冲突是一次往返、且不需要重试。唯一约束本身是 ``usage.models`` 上那个
``uq_usage_records_call_id``。

**本模块不提交事务。** 一次刷写（或一次测试）由调用方决定事务边界，这样多条记录能共用一个
事务，失败时整体回滚而不是留下半批。

筛选条件集中在 ``UsageFilter.conditions()``：明细与汇总必须接受同一组筛选，把它们写成一份
where 子句是两个接口「口径对得上」的结构性保证，而不是靠两边各写一遍再人工核对。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.usage.contracts import UsageRecord
from agent_lab.usage.models import UsageRecordRow


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageFilter:
    """一次用量查询的筛选条件。

    账号是必填的：三个查询接口都面向当前账号，没有「查全部账号」这种用法（后台超管视角不在本期
    范围）。其余三个字段为 ``None`` 表示不按这一维筛。

    Attributes:
        user_id: 只看这个账号的记录。
        model_name: 只看这个模型的记录。
        occurred_from: 时间范围起点，**含**。
        occurred_to: 时间范围终点，**不含**。用闭开区间是为了让相邻两天的区间拼起来不留缝、
            也不重算同一条记录。
    """

    user_id: UUID
    model_name: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None

    def conditions(self) -> tuple[Any, ...]:
        """把筛选条件翻成 SQLAlchemy 的 where 子句。

        Returns:
            可直接展开传给 ``where(*...)`` 的条件元组。
        """

        clauses: list[Any] = [UsageRecordRow.user_id == self.user_id]
        if self.model_name is not None:
            clauses.append(UsageRecordRow.model_name == self.model_name)
        if self.occurred_from is not None:
            clauses.append(UsageRecordRow.occurred_at >= self.occurred_from)
        if self.occurred_to is not None:
            clauses.append(UsageRecordRow.occurred_at < self.occurred_to)
        return tuple(clauses)


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageTotals:
    """一次汇总的结果。

    Attributes:
        input_tokens: 范围内输入 token 之和；没有记录时为 0。
        output_tokens: 范围内输出 token 之和；没有记录时为 0。
        cached_tokens: 范围内缓存命中 token 之和。``None`` 表示范围内有记录、但上游一次都没报过
            缓存——与「报了 0」是两件事；范围内一条记录都没有时是 0（事实，不是未知量）。
        total_tokens: 范围内合计 token 之和；没有记录时为 0。
        call_count: 范围内的调用次数，就是明细的行数。
    """

    input_tokens: int
    output_tokens: int
    cached_tokens: int | None
    total_tokens: int
    call_count: int


class UsageRepository:
    """用量表的追加与查询入口。

    Attributes:
        _session: 本次工作单元使用的异步 Session，由调用方管理生命周期与提交。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, record: UsageRecord) -> None:
        """追加一条用量记录；``call_id`` 已存在时不新增行、也不报错。

        Args:
            record: 已经装好的一次调用事实。

        Notes:
            执行一次 ``INSERT ... ON CONFLICT DO NOTHING``，不提交。批量刷写时调用方可以连着
            调多次 ``add`` 再一起提交。
        """

        statement = self._insert_factory().values(**self._row_values(record))
        await self._session.execute(
            statement.on_conflict_do_nothing(index_elements=["call_id"])
        )

    async def list_records(
        self,
        usage_filter: UsageFilter,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[UsageRecordRow], bool]:
        """按筛选条件取一页明细。

        Args:
            usage_filter: 账号、模型与时间范围。
            limit: 本页最多返回几条。
            offset: 跳过前几条。

        Returns:
            ``(本页记录, 是否还有下一页)``。

        Notes:
            排序是「发生时刻倒序、主键倒序」。只按发生时刻排的话，同一账号的多个会话并发写入时会
            落在同一毫秒，那些行在分页边界上的先后全凭数据库心情：同一条会在两页里重复出现，
            另一条被跳过。补一个唯一且单调的主键就能给出稳定全序（一次运行内的多次调用是串行的，
            不会撞，但不同会话会）。

            用 ``limit + 1`` 探测有没有下一页，不查精确总数：账本表持续增长，精确 COUNT 是白付
            的代价，而界面不需要精确总数（需要计数时看汇总接口）。
        """

        statement: Select[Any] = (
            select(UsageRecordRow)
            .where(*usage_filter.conditions())
            .order_by(UsageRecordRow.occurred_at.desc(), UsageRecordRow.id.desc())
            .limit(limit + 1)
            .offset(offset)
        )
        rows = list((await self._session.execute(statement)).scalars().all())
        return rows[:limit], len(rows) > limit

    async def summarize(self, usage_filter: UsageFilter) -> "UsageTotals":
        """按同一组筛选条件汇总四个 token 合计与调用次数。

        Args:
            usage_filter: 账号、模型与时间范围；与 ``list_records`` 共用同一份 where 子句，
                因此「列表认的筛选维度汇总也认」是结构性的，不是两边各写一遍。

        Returns:
            汇总值；不存在的合计记 0，**没报过的缓存记 null**（见下面 Notes）。

        Notes:
            缓存合计不加 ``coalesce``：SQL 的 ``sum`` 会跳过 NULL，于是它只累加上游报过的行；
            一行都没报时整个筛选范围里没有一条报过缓存——那与「报了 0」是两件事，用 ``None``
            区分开。唯一的例外是**范围内一条记录都没有**：那时 ``cached`` 与其它三列一样是
            0，因为「零消耗必然零缓存」是事实，不是未知量。

            调用次数就是明细的行数（``count()``），不另计数也不做账目守恒。
        """

        statement: Select[Any] = select(
            func.coalesce(func.sum(UsageRecordRow.input_tokens), 0),
            func.coalesce(func.sum(UsageRecordRow.output_tokens), 0),
            func.sum(UsageRecordRow.cached_tokens),
            func.coalesce(func.sum(UsageRecordRow.total_tokens), 0),
            func.count(),
        ).where(*usage_filter.conditions())
        input_tokens, output_tokens, cached_tokens, total_tokens, call_count = (
            await self._session.execute(statement)
        ).one()
        return UsageTotals(
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cached_tokens=0 if call_count == 0 else (None if cached_tokens is None else int(cached_tokens)),
            total_tokens=int(total_tokens),
            call_count=int(call_count),
        )

    async def list_model_names(self, usage_filter: UsageFilter) -> list[str]:
        """列出筛选范围内出现过的模型名，按名称排序。

        Args:
            usage_filter: 实际上只用得到账号；调用方不应传时间或模型条件（见下）。

        Returns:
            去重、去空、已排序的模型名。

        Notes:
            给筛选栏取值用，不做其他聚合。它**不**跟时间范围走：跟着走的话，用户选了某个模型
            再改时间范围，选项里那个模型可能消失——而当前选中值仍挂在 URL 上，界面会变成
            「选着一个不存在的选项」。所以口径就是「这个账号用过哪些模型」，时间筛选只作用于
            明细与汇总。

            查询走 ``(user_id, model_name, occurred_at)`` 那条索引。
        """

        statement: Select[Any] = (
            select(UsageRecordRow.model_name)
            .where(*usage_filter.conditions(), UsageRecordRow.model_name.is_not(None))
            .distinct()
            .order_by(UsageRecordRow.model_name)
        )
        names = (await self._session.execute(statement)).scalars().all()
        return [name for name in names if name]

    def _insert_factory(self) -> Any:
        """按当前连接方言选插入构造器。

        用量库生产上只有 PostgreSQL；SQLite 只是离线测试用来跑真实 ORM 与唯一约束的替身，
        两边的 ``INSERT ... ON CONFLICT DO NOTHING`` 语法由各自的方言生成。方言不在其中时
        直接报错，而不是退化成「普通插入」——那会把唯一约束冲突变成运行时异常，
        让「重复受理不多写一行」这条性质悄悄消失。
        """

        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            return postgresql_insert(UsageRecordRow)
        if dialect == "sqlite":
            return sqlite_insert(UsageRecordRow)
        raise RuntimeError(f"用量表不支持 {dialect} 方言的幂等追加")

    @staticmethod
    def _row_values(record: UsageRecord) -> dict[str, Any]:
        """把契约映射成表的列值；枚举按值存字符串。"""

        return {
            "call_id": record.call_id,
            "user_id": record.user_id,
            "thread_id": record.thread_id,
            "run_id": record.run_id,
            "model_name": record.model_name,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cached_tokens": record.cached_tokens,
            "total_tokens": record.total_tokens,
            "duration_ms": record.duration_ms,
            "status": record.status.value,
            "source": record.source.value,
            "occurred_at": record.occurred_at,
        }


__all__ = ["UsageFilter", "UsageRepository", "UsageTotals"]
