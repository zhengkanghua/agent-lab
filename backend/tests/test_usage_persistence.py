"""用量记录的持久化行为测试（内存 SQLite）。

这一层证明的是**记账逻辑与失败处理**：契约的每个字段真的落到列上、同一个调用标识重复受理不会
多出一行、用量表对业务侧迁移不可见、直写采集器把接缝收到的登记写进库并在库不可用时只留日志。
它不证明真库行为——索引、唯一约束、时区列、跨库隔离由环境变量门控的真 PostgreSQL 用例负责
（``tests/test_usage_postgres_integration.py``）。

本文件不连 PostgreSQL、不访问网络、不读用量库配置。
"""

import asyncio
import logging
from typing import Any

import httpx
from langchain_core.messages import AIMessage

from agent_lab import models  # noqa: F401  注册全部业务表，用来证明用量表不在其中
from agent_lab.db.base import Base
from agent_lab.usage.collector import NoopUsageCollector, QueuedUsageCollector
from agent_lab.usage.contracts import UsageRecord, UsageSource, UsageStatus
from agent_lab.usage.models import UsageBase, UsageRecordRow

from tests.agent_helpers import ScriptedChatModel
from tests.app_helpers import (
    FakeSearchRuntime,
    OfflineAgentRuntime,
    OfflineUsageRuntime,
    create_agent_app,
    create_offline_app,
)
from tests.usage_helpers import add_records, make_record, stored_rows, usage_database


def run(coroutine: Any) -> Any:
    """执行异步测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


# 1、契约字段真的落到列上。


def test_a_record_lands_with_every_contract_field() -> None:
    """每条记录带上账号、会话、运行三个引用与全部约定字段，值原样落库。"""

    async def scenario() -> tuple[UsageRecord, UsageRecordRow]:
        async with usage_database() as sessions:
            record = make_record()
            await add_records(sessions, [record])
            return record, (await stored_rows(sessions))[0]

    record, row = run(scenario())

    assert row.call_id == record.call_id
    assert (row.user_id, row.thread_id, row.run_id) == (record.user_id, record.thread_id, record.run_id)
    assert row.model_name == "test-model"
    assert (row.input_tokens, row.output_tokens, row.cached_tokens, row.total_tokens) == (10, 4, 6, 14)
    assert row.duration_ms == 123
    assert row.status == UsageStatus.COMPLETED.value
    assert row.source == UsageSource.UPSTREAM.value
    assert row.occurred_at.utctimetuple()[:6] == (2026, 3, 1, 12, 0, 0)


def test_a_missing_cache_count_is_stored_as_null_not_zero() -> None:
    """上游没报缓存时这一列是 NULL，与「报了 0」在库层面就是两件事。"""

    async def scenario() -> Any:
        async with usage_database() as sessions:
            await add_records(sessions, [make_record(cached_tokens=None)])
            return (await stored_rows(sessions))[0]

    assert run(scenario()).cached_tokens is None


def test_the_same_call_id_does_not_add_a_second_row() -> None:
    """同一个调用标识重复受理不会在表里多出一行，也不报错。"""

    async def scenario() -> tuple[UsageRecord, list[UsageRecordRow]]:
        async with usage_database() as sessions:
            record = make_record()
            await add_records(sessions, [record, record])
            return record, await stored_rows(sessions)

    record, stored = run(scenario())

    assert [each.call_id for each in stored] == [record.call_id]


# 2、分库：用量表对业务侧迁移不可见。


def test_the_usage_table_is_invisible_to_the_business_migrations() -> None:
    """用量表的模型只挂在自己的元数据上，业务侧迁移读到的那份里没有它。

    这条是分库的前提：一旦用量表出现在 ``Base.metadata`` 里，业务库的 ``alembic check``
    会在业务库里把它建出来，或者把它当成多余的表删掉。真库上的结构比对由集成用例负责。
    """

    assert "usage_records" in UsageBase.metadata.tables
    assert "usage_records" not in Base.metadata.tables


# 3、直写采集器：接缝收到的登记写进库，库坏了只留日志。


def test_the_queued_collector_writes_what_the_seam_hands_it() -> None:
    """采集器把收到的登记真的写进库（队列的排空交给 drain，见 test_usage_queue.py）。"""

    async def scenario() -> tuple[UsageRecord, list[UsageRecordRow]]:
        async with usage_database() as sessions:
            collector = QueuedUsageCollector(
                session_factory=sessions,
                loop=asyncio.get_running_loop(),
            )
            record = make_record()
            collector.record(record)
            await collector.drain()
            return record, await stored_rows(sessions)

    record, stored = run(scenario())

    assert [each.call_id for each in stored] == [record.call_id]


def test_the_queued_collector_swallows_a_broken_database(caplog: Any) -> None:
    """用量库不可用时采集入口不抛异常，刷写失败只留日志。"""

    class BrokenSessionFactory:
        """每次调用都抛异常的会话工厂，模拟库不可达或表还没建。"""

        def __call__(self) -> Any:
            raise RuntimeError("用量库不可达")

    async def scenario() -> None:
        collector = QueuedUsageCollector(
            session_factory=BrokenSessionFactory(),
            loop=asyncio.get_running_loop(),
        )
        with caplog.at_level(logging.WARNING):
            collector.record(make_record())
            await collector.drain()

    run(scenario())

    assert "用量库写入失败" in caplog.text


def test_the_noop_collector_accepts_and_drains_without_any_side_effect() -> None:
    """空实现的 ``record`` 与 ``drain`` 都是空操作，缺省装配因此不需要任何外部资源。"""

    collector = NoopUsageCollector()

    async def scenario() -> None:
        collector.record(make_record())
        await collector.drain()

    run(scenario())


# 4、装配：lifespan 把用量采集器交给 Agent 工厂，并在关停时释放。


def test_the_lifespan_hands_the_usage_collector_to_the_agent_factory() -> None:
    """lifespan 从用量库资源里取出采集器交给 Agent 装配，关停时释放用量库资源。"""

    usage_runtime = OfflineUsageRuntime()
    seen: dict[str, Any] = {}

    def agent_factory(_service: Any, collector: Any) -> Any:
        seen["collector"] = collector
        return OfflineAgentRuntime()

    app = create_offline_app(
        runtime_factory=FakeSearchRuntime,
        usage_runtime_factory=lambda: usage_runtime,
        agent_runtime_factory=agent_factory,
    )

    async def verify() -> None:
        async with app.router.lifespan_context(app):
            assert app.state.usage_runtime is usage_runtime
        assert usage_runtime.closed is True

    run(verify())

    assert seen["collector"] is usage_runtime.collector


# 5、请求入口填账号与会话：一次对话记下的每条登记都能归到账号。


def test_a_conversation_records_the_account_thread_and_run() -> None:
    """走真实对话入口时，登记带上当前账号、会话与本次运行的标识。

    账号与会话由 ``api/agent_chat.py`` 在构造运行上下文时填入；少了这一步，记录会因为按账号
    过滤而谁都查不到。
    """

    collector = RecordingCollector()
    model = ScriptedChatModel(responses=[AIMessage(content="央行确实降息了。")])
    app, _search = create_agent_app(model, usage_collector=collector)

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                async with client.stream("POST", "/agent/chat", json={"message": "央行降息了吗"}) as response:
                    assert response.status_code == 200
                    async for _chunk in response.aiter_text():
                        pass

    run(scenario())

    assert len(collector.records) == 1
    record = collector.records[0]
    assert record.user_id is not None
    assert record.thread_id is not None
    assert record.run_id is not None


class RecordingCollector:
    """把收到的记录攒在内存里的假采集器。"""

    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    def record(self, record: UsageRecord) -> None:
        self.records.append(record)

    async def drain(self) -> None:
        return None
