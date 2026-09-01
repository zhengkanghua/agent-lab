"""显式启用后，用**真实 PostgreSQL** 验证会话归属过滤真的只匹配到自己的行。

默认跳过。启用方式（需要已经跑过 ``alembic upgrade head``，``agent_threads`` 表必须存在）：

```bash
RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST=1 pytest tests/test_agent_thread_ownership_integration.py
```

**为什么这个文件必须存在**，尽管已经有 ``tests/test_agent_thread_service.py``：

那份是语句级测试，断言的是「编译出来的 SQL 文本里有 ``user_id`` 条件」。它挡得住「忘了写归属条件」，
挡不住「写了但不生效」。真实存在的几种失效方式在它那里全都能通过：

- ``.where(a).where(b)`` 与 ``.where(a, b)`` 在某些 ORM 用法下语义不同，链式写法可能覆盖前一个条件；
- 参数类型不匹配（比如 UUID 列拿字符串比），编译文本照样带条件，执行时却匹配不到或全匹配；
- 迁移里 ``user_id`` 列名写错、约束没建上。

归属是本次改动的安全核心：判断错了就是跨账号读别人的对话，或者删掉别人的历史。所以两层都要有。

本文件在一个**外层事务**里跑完再整体回滚，不会给库留下测试数据。
"""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_lab.agent.errors import AgentThreadNotFoundError
from agent_lab.db.session import engine
from agent_lab.models.agent_thread import AgentThreadRecord
from agent_lab.models.user import UserRecord
from agent_lab.services.agent_thread_service import AgentThreadService


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST") != "1",
    reason=(
        "set RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST=1 to verify agent thread "
        "ownership filtering against the configured PostgreSQL database"
    ),
)


async def _create_user(factory: async_sessionmaker, email: str) -> UserRecord:
    """插入一个启用账号，供归属外键指向。

    Args:
        factory: 绑在外层事务上的 session 工厂。
        email: 账号邮箱，调用方带上随机后缀避免撞既有数据。

    Returns:
        已提交（到 savepoint）的账号记录。

    Notes:
        必须是真实存在的账号：``agent_threads.user_id`` 上有指向 ``users.id`` 的外键，
        随手编一个 UUID 会直接违反约束——那种失败看起来像「归属写坏了」，其实只是测试数据不对。
    """

    user = UserRecord(
        id=uuid4(),
        email=email,
        hashed_password="not-a-real-hash-integration-only",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        is_environment_admin=False,
    )
    async with factory() as session:
        session.add(user)
        await session.commit()
    return user


def test_ownership_filter_really_isolates_two_accounts() -> None:
    """两个真实账号、真实表：谁都碰不到对方的会话。

    这条用例把归属的四条路径在真库上跑一遍——续聊、读取、删除、列表——每条都验证「自己的能通、
    别人的不通」两个方向。只测一个方向不够：一个永远抛异常的实现能通过「别人的不通」，
    而一个永不校验的实现能通过「自己的能通」。
    """

    async def verify() -> None:
        suffix = uuid4().hex
        connection = await engine.connect()
        outer_transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            # Service 内部会 commit。用 savepoint 模式让那些提交落在外层事务里，
            # 既能被后续查询看见，最后又能一起回滚干净。
            join_transaction_mode="create_savepoint",
        )
        try:
            alice = await _create_user(factory, f"alice-{suffix}@example.com")
            bob = await _create_user(factory, f"bob-{suffix}@example.com")
            threads = AgentThreadService(factory)

            # 1、Alice 建两个会话，Bob 建一个。
            alice_first = await threads.ensure_thread(
                user_id=alice.id, thread_id=None, first_message="Alice 的第一个会话"
            )
            alice_second = await threads.ensure_thread(
                user_id=alice.id, thread_id=None, first_message="Alice 的第二个会话"
            )
            bob_only = await threads.ensure_thread(
                user_id=bob.id, thread_id=None, first_message="Bob 的会话"
            )
            assert len({alice_first, alice_second, bob_only}) == 3

            # 2、续聊：Bob 拿 Alice 的 id 续不了。**这就是语句级测试证明不了的那一步。**
            with pytest.raises(AgentThreadNotFoundError):
                await threads.ensure_thread(
                    user_id=bob.id, thread_id=alice_first, first_message="偷看"
                )
            # 自己的能续，返回同一个 id。
            assert (
                await threads.ensure_thread(
                    user_id=alice.id, thread_id=alice_first, first_message="接着聊"
                )
                == alice_first
            )

            # 3、读取：Bob 读不到 Alice 的，Alice 读得到自己的。
            with pytest.raises(AgentThreadNotFoundError):
                await threads.get_owned_thread(user_id=bob.id, thread_id=alice_first)
            owned = await threads.get_owned_thread(
                user_id=alice.id, thread_id=alice_first
            )
            assert owned.user_id == alice.id
            assert owned.title == "Alice 的第一个会话"

            # 4、列表：各自只看到自己的，total 也不含对方的。
            alice_page, alice_total = await threads.list_threads(
                user_id=alice.id, limit=20, offset=0
            )
            bob_page, bob_total = await threads.list_threads(
                user_id=bob.id, limit=20, offset=0
            )
            assert alice_total == 2
            assert bob_total == 1
            assert {record.thread_id for record in alice_page} == {
                alice_first,
                alice_second,
            }
            assert [record.thread_id for record in bob_page] == [bob_only]
            # 刚续过的 alice_first 排在最前——排序键在真库上真的生效。
            assert alice_page[0].thread_id == alice_first

            # 5、删除：Bob 删不掉 Alice 的，而且 Alice 的行还在。
            with pytest.raises(AgentThreadNotFoundError):
                await threads.delete_thread_record(
                    user_id=bob.id, thread_id=alice_first
                )
            assert (
                await threads.get_owned_thread(
                    user_id=alice.id, thread_id=alice_first
                )
            ).thread_id == alice_first
            # Alice 删自己的能成，删完就查不到了。
            await threads.delete_thread_record(
                user_id=alice.id, thread_id=alice_first
            )
            with pytest.raises(AgentThreadNotFoundError):
                await threads.get_owned_thread(
                    user_id=alice.id, thread_id=alice_first
                )

            # 6、运维用的全库视图不按账号过滤：两个账号剩下的会话都在里面。
            known = await threads.list_known_thread_ids()
            assert {alice_second, bob_only} <= known
            assert alice_first not in known
        finally:
            await outer_transaction.rollback()
            await connection.close()
            await engine.dispose()

    # Windows 默认的 ProactorEventLoop 跑不了 psycopg，必须显式换 Selector 循环。
    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)


def test_deleting_an_account_cascades_to_its_thread_rows() -> None:
    """删账号会带走它的会话归属记录。

    模型里写了 ``ondelete="CASCADE"``，但那只是声明——约束到底有没有建到库上，只有真库能回答。
    少了它，删账号会因为外键约束失败（500），或者留下一堆指向不存在账号的行。

    注意级联只清业务表这一行，**不清 checkpointer 里的历史**：那四张表不在我们的外键图里。
    删账号后残留的历史由 ``prune-orphan-threads`` 回收，这也是那个命令存在的理由之一。
    """

    async def verify() -> None:
        suffix = uuid4().hex
        connection = await engine.connect()
        outer_transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            doomed = await _create_user(factory, f"doomed-{suffix}@example.com")
            threads = AgentThreadService(factory)
            thread_id = await threads.ensure_thread(
                user_id=doomed.id, thread_id=None, first_message="随账号一起消失"
            )

            async with factory() as session:
                assert await session.get(AgentThreadRecord, thread_id) is not None
                await session.delete(await session.get(UserRecord, doomed.id))
                await session.commit()

            async with factory() as session:
                assert await session.get(AgentThreadRecord, thread_id) is None
                remaining = await session.scalar(
                    select(func.count())
                    .select_from(AgentThreadRecord)
                    .where(AgentThreadRecord.user_id == doomed.id)
                )
                assert remaining == 0
        finally:
            await outer_transaction.rollback()
            await connection.close()
            await engine.dispose()

    asyncio.run(verify(), loop_factory=asyncio.SelectorEventLoop)
