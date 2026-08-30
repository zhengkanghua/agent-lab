"""组装进程级 Agent Runtime：编译一次图，所有请求共享。

本模块是 Agent 侧的组件组装入口（composition root），与 ``qdrant/runtime.py`` 同构：
把模型客户端、工具、中间件和 checkpointer 装成一个工具箱，交给 FastAPI lifespan 管理。

**为什么图可以进程级共享**：图本身无状态——会话历史存在 checkpointer 里、按 ``thread_id``
取；系统提示词由 ``resolve_system_prompt`` 每次从 ``AgentContext`` 读。所以「换提示词」
和「换会话」都不需要重新编译，这也是 ``dynamic_prompt`` 存在的意义。

本模块只读本地配置、构造对象和连接池，``build`` 不执行数据库或模型 I/O；连接池的实际
建连与 checkpointer 建表分别由 ``open`` 和运维步骤（见 ADR 0004）负责。
"""

import logging
from dataclasses import dataclass

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent_lab.agent.chat_model import build_chat_model
from agent_lab.agent.checkpointer import to_psycopg_conninfo
from agent_lab.agent.context import AgentContext
from agent_lab.agent.errors import AgentCheckpointerUnavailableError
from agent_lab.agent.middleware import build_agent_middleware
from agent_lab.agent.tools import build_agent_tools
from agent_lab.config.llm import LlmSettings
from agent_lab.services.vector_search_service import VectorSearchService


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentRuntime:
    """FastAPI 进程持有的 Agent 工具箱。

    生命周期与进程一致：lifespan 启动时 ``build`` 再 ``open``，退出时 ``close``。
    每次请求只用 ``graph`` 发起一次运行，不重新编译、不新建连接。

    它刻意只装只读工具（见 ADR 0003）：没有索引 Service、没有 Qdrant Point Store、
    没有 lifecycle，因此模型即使被上下文里的新闻正文诱导，也没有可写的东西可调。

    Attributes:
        graph: 已编译的 Agent 图，多请求共享。
        checkpointer: 会话历史存储；``None`` 表示未接入持久化（仅离线测试）。
        pool: checkpointer 使用的 psycopg 连接池；``None`` 表示注入了外部 checkpointer。
    """

    graph: CompiledStateGraph
    checkpointer: BaseCheckpointSaver | None
    pool: AsyncConnectionPool | None

    @classmethod
    def build(
        cls,
        *,
        llm_settings: LlmSettings,
        search_service: VectorSearchService,
        session_factory: async_sessionmaker,
        database_url: str,
        checkpointer: BaseCheckpointSaver | None = None,
        model: BaseChatModel | None = None,
    ) -> "AgentRuntime":
        """组装模型、工具、中间件和 checkpointer，编译出可共享的图。

        Args:
            llm_settings: 模型 provider、base_url、凭据、主/备模型名和连接池大小。
            search_service: 只读向量检索 Service，供 ``search_news`` 工具使用；与 HTTP
                搜索路由共用同一个进程级实例。
            session_factory: PostgreSQL Session 工厂，供 ``read_document`` 工具按次开
                Session。传工厂而不是 Session：图是进程级的，而 Session 是一次工作单元。
            database_url: SQLAlchemy 风格的数据库 URL，用于建 checkpointer 连接池。
                注入了 ``checkpointer`` 时忽略。
            checkpointer: 可选的会话历史存储；离线测试注入 ``InMemorySaver``，省略时按
                ``database_url`` 建 PostgreSQL 连接池。
            model: 可选的主模型客户端；离线测试注入 fake，省略时按配置构造。

        Returns:
            尚未建连的 Runtime；必须再 ``await open()`` 才能处理请求。

        Raises:
            LlmConfigurationError: provider 为 openai_compatible 但 API Key 为空。

        Notes:
            本方法不执行数据库、Qdrant 或模型 I/O，也不建表——checkpointer 的四张表由
            ``cli.py init-checkpointer`` 显式创建，不在启动路径隐式改数据库结构
            （见 ADR 0004）。
        """

        # 1、主模型与备用模型。备用模型只在主模型重试耗尽后才会被调用。
        primary_model = model or build_chat_model(llm_settings)
        fallback_model = model or build_chat_model(
            llm_settings,
            model=llm_settings.fallback_model,
        )

        # 2、只读工具。search 用共享 Service，read 用 Session 工厂。
        tools = build_agent_tools(
            search_service=search_service,
            session_factory=session_factory,
        )

        # 3、checkpointer。注入了就直接用（且不建池），否则按配置建 psycopg 池。
        #    两边连的是同一个库，但 SQLAlchemy 走 ORM、checkpointer 走原生 psycopg，
        #    所以是两个独立连接池，而不是共用一个（见 ADR 0004）。
        #    压缩历史用主模型：它已经构造好了，再建一个客户端只是多一个连接池。
        pool: AsyncConnectionPool | None = None
        if checkpointer is None:
            pool = AsyncConnectionPool(
                conninfo=to_psycopg_conninfo(database_url),
                max_size=llm_settings.checkpoint_pool_size,
                # 必须显式 False：默认 True 会在构造时就尝试建连，
                # 而 build 的契约是「不执行 I/O」，且此时事件循环可能还没起来。
                open=False,
                # checkpointer 自己拼 SQL 并按需 prepare，交给 psycopg 自动
                # 事务包裹会多一层往返，官方推荐关掉。
                kwargs={"autocommit": True, "prepare_threshold": 0},
            )
            checkpointer = AsyncPostgresSaver(pool)

        # 4、编译图。context_schema 让 runtime.context 拿到有类型的 AgentContext。
        #    不传 system_prompt——提示词由 resolve_system_prompt 每次动态给出，
        #    这里再传一份会造成「两个提示词来源」的歧义。
        graph = create_agent(
            primary_model,
            tools=tools,
            middleware=build_agent_middleware(
                fallback_model=fallback_model,
                summarization_model=primary_model,
            ),
            context_schema=AgentContext,
            checkpointer=checkpointer,
            name="news_agent",
        )
        return cls(graph=graph, checkpointer=checkpointer, pool=pool)

    async def open(self) -> None:
        """打开 checkpointer 连接池，让会话历史可读写。

        Raises:
            AgentCheckpointerUnavailableError: 连接池无法建立到 PostgreSQL 的连接。

        Notes:
            执行 PostgreSQL 建连 I/O，但不建表、不写业务数据。表缺失不在这里报错——
            要等到第一次读写会话历史时才会暴露，因为建表属于运维步骤（见 ADR 0004）。
            注入了外部 checkpointer 时本方法是空操作。
        """

        if self.pool is None:
            return
        try:
            await self.pool.open(wait=True)
        except Exception as exc:
            # 只记异常类型：连接串里有数据库密码，异常文本可能把它带出来。
            logger.error(
                "Agent checkpointer 连接池打开失败 error_type=%s",
                type(exc).__name__,
            )
            raise AgentCheckpointerUnavailableError from exc

    async def close(self) -> None:
        """关闭 checkpointer 连接池，不删除任何会话历史。

        Notes:
            只释放进程本地连接池，不执行业务写操作。注入了外部 checkpointer 时是空操作。
            重复关闭由 psycopg_pool 保证安全。
        """

        if self.pool is None:
            return
        await self.pool.close()


__all__ = ["AgentRuntime"]
