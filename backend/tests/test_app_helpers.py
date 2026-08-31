"""守住 ``create_offline_app`` 的离线默认值真的生效。

为什么这一个文件值得单独存在：``create_app`` 的 lifespan 里那个 ``except Exception`` 会把
Agent 装配失败**咽掉**——只记一行日志，把 state 留成 ``None``，然后照常开门营业。生产上这
是对的（一个缺失的模型凭据不该让整个只读系统下线），但它对测试有个副作用：装配根本没成功
时，除 ``/agent/*`` 以外的用例全都察觉不到。

这个副作用在本仓库真的咬过一次：5 个测试文件漏传 ``agent_runtime_factory``，于是 lifespan
拿生产默认工厂去建真实 checkpointer 连接池，每次白等 30 秒 psycopg 池超时，**测试全绿**，
没人发现，直到有人去查「为什么测试要跑二十分钟」。

所以本文件断言的不是 Agent 的行为，是「离线默认值没被悄悄绕过」这件事本身。它是那个 bug
的回归测试：把 ``app_helpers`` 里的 agent 默认替身去掉，本文件会红。

不连接 PostgreSQL、Qdrant，也不访问任何大模型。
"""

import asyncio
from typing import Any

from tests.app_helpers import OfflineAgentRuntime, create_offline_app


class FakeSearchRuntime:
    """只提供 lifespan 需要的 ``service`` 与 ``close``。"""

    def __init__(self) -> None:
        self.service = object()

    async def close(self) -> None:
        """不执行外部 I/O。"""


def test_offline_app_assembles_a_fake_agent_without_being_told_to() -> None:
    """只传 ``runtime_factory``（六个 HTTP 测试文件的常见写法）也必须拿到离线替身。

    这正是原始 bug 的形状：调用方没提 Agent，于是默认值说话。默认值要是生产工厂，这里
    ``agent_runtime`` 会是 ``None``（装配失败被咽掉），而且要白等 30 秒。断言它是
    ``OfflineAgentRuntime`` 就同时钉住了两件事：默认值是替身，且它真的被 lifespan 打开了。
    """

    app = create_offline_app(runtime_factory=FakeSearchRuntime)

    async def verify() -> None:
        async with app.router.lifespan_context(app):
            runtime = app.state.agent_runtime
            assert isinstance(runtime, OfflineAgentRuntime), (
                "lifespan 没拿到离线 Agent 替身——默认值可能被改回生产工厂了，"
                "那会让这批测试重新去连真实 PostgreSQL"
            )
            assert runtime.opened is True

    asyncio.run(verify())


def test_an_explicit_agent_factory_still_wins_over_the_default() -> None:
    """显式传入的工厂必须覆盖默认替身。

    ``test_agent_chat_api.py`` 靠这条语义注入真实 ``AgentRuntime``（配 ``InMemorySaver``）
    去测真实装配，其中还有刻意让装配失败的用例。默认值要是把它盖掉，那些用例就变成在测
    替身，等于悄悄失效。
    """

    sentinel = OfflineAgentRuntime()

    def explicit_factory(_service: Any) -> OfflineAgentRuntime:
        return sentinel

    app = create_offline_app(
        runtime_factory=FakeSearchRuntime,
        agent_runtime_factory=explicit_factory,
    )

    async def verify() -> None:
        async with app.router.lifespan_context(app):
            assert app.state.agent_runtime is sentinel

    asyncio.run(verify())
