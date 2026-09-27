"""装配用量库资源：Engine、短会话工厂与采集器。

这是用量子包对外的组装入口，由 API 进程的 lifespan 调用一次并持有结果：

* 写入侧（采集器）每次刷写开一个短会话、写完提交、立即关闭；
* 读用量侧（HTTP 接口）从同一个会话工厂取会话。

**这个 Engine 只由 API 进程持有**：Agent 对话只在 API 进程跑，Worker 与 CLI 都不调生成式模型。
跟着这条边界的一个当前事实：Worker 侧不记账，将来若有定时任务开始调生成式模型，那条链路上的
调用不会被记录（不是记成 0，是根本没有采集器），见
``docs/adr/0032-usage-data-in-separate-database.md``。
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_lab.config.usage_database import UsageDatabaseSettings
from agent_lab.usage.collector import QueuedUsageCollector
from agent_lab.usage.contracts import UsageCollector


USAGE_DATABASE_TIMEZONE = "UTC"
"""用量库会话时区。与时刻列一起把「写入」和「按 UTC 日期边界查询」对齐到同一个瞬时。"""


@dataclass(slots=True)
class UsageRuntime:
    """API 进程持有的用量库资源。

    生命周期与进程一致：lifespan 启动时装配，退出时 ``close``。与 ``AgentRuntime`` 分成两个
    对象，因为「记账」和「对话」是两条独立的失败边界：用量库连不上只应当让用量消失，
    不应当让对话装配失败。

    Attributes:
        engine: 绑定用量库的连接池；写入与查询两个使用者都从它借连接。
        session_factory: 短会话工厂；请求级查询与每次刷写各自开一个会话。
        collector: 交给模型包装层的采集器；缺省装配是直写实现。
    """

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    collector: UsageCollector

    @classmethod
    def build(cls, settings: UsageDatabaseSettings) -> "UsageRuntime":
        """按配置构造用量库资源（只构造对象，不建连接）。

        Args:
            settings: 已完成校验的用量库配置。

        Returns:
            尚未建立任何连接的 ``UsageRuntime``。

        Raises:
            RuntimeError: 不在运行中的事件循环里被调用。采集器是同步入口，只能把待写协程排到
                装配时所在的那个循环上，所以本方法必须在 lifespan 这样的异步上下文里调用
                （``AsyncPostgresSaver`` 那条 checkpointer 装配对事件循环也有同样的要求）。

        Notes:
            ``create_async_engine`` 本身不建连；第一条 SQL 才会。会话时区用 ``options`` 钉成
            UTC，免得写入与查询两边在代码里看起来都用 UTC、实际按别的时区解释。
        """

        engine = create_async_engine(
            str(settings.database_url),
            echo=settings.echo,
            connect_args={
                "connect_timeout": settings.connect_timeout,
                "options": f"-c timezone={USAGE_DATABASE_TIMEZONE}",
            },
            pool_pre_ping=True,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
        )
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        collector = QueuedUsageCollector(
            session_factory=session_factory,
            loop=asyncio.get_running_loop(),
        )
        return cls(engine=engine, session_factory=session_factory, collector=collector)

    async def close(self) -> None:
        """先把待写记录尽力刷完，再释放连接池；不删除任何用量记录。

        Notes:
            先 ``drain`` 再 dispose：只 dispose 会把刚受理、还没落库的记录连同连接一起丢掉，
            表现是「快速重启后最后几条不见了，而且不报错」。排空有上限（一次刷写间隔），
            不让关停被一个坏掉的数据库无限期拖住。
        """

        await self.collector.drain()
        await self.engine.dispose()


__all__ = ["USAGE_DATABASE_TIMEZONE", "UsageRuntime"]
