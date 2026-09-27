"""排队采集器的行为测试：不挡对话、丢了能看见、故障恢复自动续上、关停尽力排空。

这一层守的是用量库这个世界出问题时系统的表现。它不连 PostgreSQL：会话工厂是假的或内存
SQLite，因此证明的是采集器自己的行为，不是真库行为。
"""

import asyncio
import logging
import time
from typing import Any

import httpx
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_lab.usage.assembly import UsageRuntime
from agent_lab.usage.collector import NoopUsageCollector, QueuedUsageCollector

from tests.agent_helpers import ScriptedChatModel
from tests.app_helpers import create_agent_app
from tests.usage_helpers import make_record, stored_rows, usage_database


def run(coroutine: Any) -> Any:
    """执行异步测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


class HangingSessionFactory:
    """开一个会话就挂住不返回：模拟用量库卡死（不是快速报错，而是根本没有响应）。"""

    def __call__(self) -> "HangingSessionFactory":
        return self

    async def __aenter__(self) -> Any:
        await asyncio.sleep(30)
        raise AssertionError("这个会话本来就不该开成功")

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class FailingSessionFactory:
    """前若干次调用直接抛异常、之后交给真实会话工厂：模拟库先坏后恢复。"""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], failures: int) -> None:
        self._sessions = sessions
        self._remaining = failures
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("用量库不可达")
        return self._sessions()


class CountingSessionFactory:
    """记录会话工厂被要了几次，用来断言「队列为空时不做数据库访问」。"""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        return self._sessions()


def test_the_seam_never_waits_for_a_stuck_database() -> None:
    """采集入口不等待数据库：库卡死时受理一批记录仍然是毫秒级的事。

    会话都被挂住不返回的情况下，如果 ``record`` 里带任何一次数据库往返，这里就会等到测试超时。
    """

    async def scenario() -> tuple[float, list[Any]]:
        collector = QueuedUsageCollector(
            session_factory=HangingSessionFactory(),
            loop=asyncio.get_running_loop(),
            flush_interval=0.01,
        )
        records = [make_record() for _ in range(200)]
        started = time.monotonic()
        for record in records:
            collector.record(record)
        elapsed = time.monotonic() - started
        return elapsed, records

    elapsed, records = run(scenario())

    assert len(records) == 200
    assert elapsed < 0.5, f"采集入口被数据库拖住了：{elapsed:.3f}s"


def test_a_persistently_failing_flush_leaves_a_log(caplog: Any) -> None:
    """写入持续失败时留日志，能看出是用量库的问题。"""

    class Broken:
        """每次要会话都抛异常，模拟库不可达或表还没建。"""

        def __call__(self) -> Any:
            raise RuntimeError("用量库不可达")

    async def scenario() -> None:
        collector = QueuedUsageCollector(
            session_factory=Broken(),
            loop=asyncio.get_running_loop(),
            flush_interval=0.01,
        )
        with caplog.at_level(logging.WARNING):
            collector.record(make_record())
            await collector.drain()

    run(scenario())

    assert "用量库写入失败" in caplog.text
    assert "用量库" in caplog.text


def test_new_records_land_again_after_the_database_recovers() -> None:
    """用量库恢复后，新产生的记录继续落库，不需要重启进程。"""

    async def scenario() -> tuple[list[Any], int]:
        async with usage_database() as sessions:
            factory = FailingSessionFactory(sessions, failures=1)
            collector = QueuedUsageCollector(
                session_factory=factory,
                loop=asyncio.get_running_loop(),
                flush_interval=0.01,
                commit_retries=0,
                drain_timeout=5.0,
            )
            # 第一次刷写失败：这一条按规则丢弃，不重新入队。
            collector.record(make_record(model_name="before-outage"))
            await asyncio.sleep(0.05)
            # 库已经恢复，之后产生的记录照常落库。
            collector.record(make_record(model_name="after-outage"))
            await collector.drain()
            return await stored_rows(sessions), factory.calls

    stored, calls = run(scenario())

    assert calls >= 2, "第一次刷写必须真的试过并失败"
    assert [row.model_name for row in stored] == ["after-outage"]


def test_a_full_queue_drops_new_records_and_logs_how_many(caplog: Any) -> None:
    """队列满时丢弃新记录并记日志，日志里能看出这一次丢了多少条。"""

    async def scenario() -> None:
        collector = QueuedUsageCollector(
            session_factory=HangingSessionFactory(),
            loop=asyncio.get_running_loop(),
            queue_capacity=2,
            # 刷写间隔远大于用例时长：让队列保持满，专门测溢出分支。
            flush_interval=60.0,
        )
        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                collector.record(make_record())

    run(scenario())

    assert "用量队列已满" in caplog.text
    assert "累计丢弃 3 条记录" in caplog.text


def test_an_empty_queue_never_touches_the_database() -> None:
    """队列为空时刷写不做任何数据库访问。"""

    async def scenario() -> int:
        async with usage_database() as sessions:
            factory = CountingSessionFactory(sessions)
            collector = QueuedUsageCollector(
                session_factory=factory,
                loop=asyncio.get_running_loop(),
                flush_interval=0.01,
                drain_timeout=5.0,
            )
            # 先让队列真的跑起来并写掉一条，再清空队列观察。
            collector.record(make_record())
            await asyncio.sleep(0.05)
            assert factory.calls == 1
            await asyncio.sleep(0.05)
            during_idle = factory.calls - 1
            await collector.drain()
            return during_idle

    assert run(scenario()) == 0


def test_shutdown_writes_what_is_still_queued() -> None:
    """进程关停时把队列里剩下的记录尽力写完。"""

    async def scenario() -> list[Any]:
        async with usage_database() as sessions:
            collector = QueuedUsageCollector(
                session_factory=sessions,
                loop=asyncio.get_running_loop(),
                # 刷写间隔远大于用例时长：记录只能靠关停排空落库。
                flush_interval=60.0,
            )
            for _ in range(3):
                collector.record(make_record())
            await collector.drain()
            return await stored_rows(sessions)

    assert len(run(scenario())) == 3


def test_shutdown_gives_up_within_one_flush_interval(caplog: Any) -> None:
    """库卡死时关停不被无限期拖住，剩余的条数写进日志。"""

    async def scenario() -> float:
        collector = QueuedUsageCollector(
            session_factory=HangingSessionFactory(),
            loop=asyncio.get_running_loop(),
            flush_interval=60.0,
            drain_timeout=0.05,
        )
        collector.record(make_record())
        with caplog.at_level(logging.WARNING):
            started = time.monotonic()
            await collector.drain()
            return time.monotonic() - started

    elapsed = run(scenario())

    assert elapsed < 2.0, f"关停被卡死的用量库拖住了：{elapsed:.3f}s"
    assert "排空超时" in caplog.text
    assert "仍有 1 条记录未写入" in caplog.text


def test_a_conversation_still_completes_when_the_usage_database_is_down() -> None:
    """用量库连不上时，问一次 Agent 仍然拿到完整回答。"""

    class BrokenSessionFactory:
        """每次要会话都抛异常，模拟库不可达或表还没建。"""

        def __call__(self) -> Any:
            raise RuntimeError("用量库不可达")

    async def scenario() -> tuple[int, str]:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        usage_runtime = UsageRuntime(
            engine=engine,
            session_factory=BrokenSessionFactory(),
            collector=QueuedUsageCollector(
                session_factory=BrokenSessionFactory(),
                loop=asyncio.get_running_loop(),
                flush_interval=0.01,
                drain_timeout=0.05,
            ),
        )
        app, _search = create_agent_app(
            ScriptedChatModel(responses=[AIMessage(content="央行确实降息了。")]),
            usage_runtime=usage_runtime,
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                async with client.stream(
                    "POST", "/agent/chat", json={"message": "央行降息了吗"}
                ) as response:
                    body = "".join([chunk async for chunk in response.aiter_text()])
                    return response.status_code, body

    status, body = run(scenario())

    assert status == 200
    assert "央行确实降息了。" in body, "用量库故障不能影响回答本身"


def test_the_noop_collector_accepts_and_drains_without_any_side_effect() -> None:
    """空实现仍然满足采集器协议：缺省装配不需要任何外部资源。"""

    collector = NoopUsageCollector()

    async def scenario() -> None:
        collector.record(make_record())
        await collector.drain()

    run(scenario())
