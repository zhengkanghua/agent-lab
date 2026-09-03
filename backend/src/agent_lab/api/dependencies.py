"""集中提供 API 层共享的 FastAPI 依赖注入函数与「组件不可用」分类异常。

本模块位于 FastAPI 边界层最底部，是各特性路由共同依赖的基础设施，而不是某个接口的
一部分：它只从 ``application.state`` 取出 composition root（``main.py``）在 lifespan
或创建应用时放进去的进程级/请求级组件。把这些依赖放在独立模块里，是为了让
``vector_search`` 与 ``document_search`` 这类平级路由都依赖公共模块，而不是互相
import——否则任何一个特性路由都会同时扮演「接口」和「基础设施」两种角色。

本模块只读取进程内对象，既不构造 Runtime，也不执行 PostgreSQL、FreshRSS、
Ollama/Embedding 或 Qdrant I/O；取不到组件时抛出分类异常，由错误契约层
(``agent_lab.api.error_contract``) 映射成稳定的 503 响应。
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import Request

from agent_lab.agent.errors import AgentRuntimeUnavailableError
from agent_lab.db.session import async_session_factory
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.services.agent_thread_service import AgentThreadService
from agent_lab.services.vector_search_service import VectorSearchService


if TYPE_CHECKING:
    # 只在类型检查时导入：运行时导入会成环。``agent.runtime`` 会拉进
    # ``agent.middleware``，后者为了错误映射要 import ``api.error_contract``，而
    # ``error_contract`` 又要 import 本模块的两个 *UnavailableError。
    # 放进 TYPE_CHECKING 是因为这里真的只需要类型：本模块从 ``app.state`` 取现成对象，
    # 从不构造 Runtime，也不 isinstance 它。
    # ``AgentRuntimeUnavailableError`` 可以照常运行时导入——``agent.errors`` 是叶子模块，
    # 一行 import 都没有。
    from agent_lab.agent.runtime import AgentRuntime

    from agent_lab.services.scheduler_runner import ScheduledJobRunner


type PipelineWriteRuntimeFactory = Callable[[], PipelineWriteRuntime]


class VectorSearchRuntimeUnavailableError(RuntimeError):
    """ASGI lifespan 尚未提供进程级只读 Search Runtime。"""


class PipelineWriteRuntimeUnavailableError(RuntimeError):
    """应用没注册写 Runtime 工厂时的内部异常（会被映射成 503）。"""


class SchedulerRuntimeUnavailableError(RuntimeError):
    """应用状态缺少调度器实例时的内部异常（会被映射成 503）。"""


def get_vector_search_service(request: Request) -> VectorSearchService:
    """从应用 lifespan 状态取出共享的只读搜索 Service（FastAPI 依赖注入函数）。

    为什么从 request 里取：FastAPI 的 ``Depends(get_vector_search_service)`` 只负责
    调用本函数并把返回值注入接口参数；Service 本体在 lifespan 启动时创建并存进
    ``application.state``，所以必须借当前请求拿到 app 对象再取。所有并发请求共享
    同一个 Service（共享 Ollama/Qdrant 连接池），而不是每个请求新建一个。

    Args:
        request: 当前 HTTP 请求，用于访问所属应用的 ``state``。

    Returns:
        lifespan 启动时创建并由并发请求共享的 ``VectorSearchService``。

    Raises:
        VectorSearchRuntimeUnavailableError: 应用未经过 lifespan 启动或 Runtime 初始化
            缺失；全局 API handler 会把它映射为稳定的 503 错误契约。

    Notes:
        本依赖只读取进程内对象，不执行 PostgreSQL、Ollama/Embedding 或 Qdrant I/O。
    """

    # 两级 getattr 各挡一种情况：没跑 lifespan 时 state 上根本没有这个属性，跑了但装配
    # 失败时属性在、service 是 None。都归到同一个 503，调用方不需要区分。
    runtime = getattr(request.app.state, "vector_search_runtime", None)
    service = getattr(runtime, "service", None)
    if service is None:
        raise VectorSearchRuntimeUnavailableError(
            "向量检索运行时不可用。"
        )
    return service


def get_pipeline_write_runtime_factory(
    request: Request,
) -> PipelineWriteRuntimeFactory:
    """从应用 state 取出「写 Runtime 工厂」（只取不建）。

    ``main.py`` 的 ``create_app`` 把工厂函数存进了
    ``application.state.pipeline_write_runtime_factory``，这里通过 ``request.app.state``
    取回。取到的是「能造 Runtime 的函数」，不是 Runtime 本身——真正构造发生在调用方，
    这样每次请求都能拿到一个全新的写 Runtime，请求结束即整体关闭。

    Args:
        request: 当前 FastAPI 请求。

    Returns:
        composition root 在创建应用时保存的同步工厂。

    Raises:
        PipelineWriteRuntimeUnavailableError: 应用状态缺少可调用工厂。

    Notes:
        读取进程内对象，不构造 Runtime，也不执行任何外部 I/O。
    """

    runtime_factory = getattr(
        request.app.state,
        "pipeline_write_runtime_factory",
        None,
    )
    # 用 callable 而不是 ``is not None``：state 是个可以随便塞东西的命名空间，塞进来的
    # 要是个非函数值，等调用方 ``factory()`` 时才炸就说不清是谁的问题了。
    if not callable(runtime_factory):
        raise PipelineWriteRuntimeUnavailableError(
            "流水线写入运行时工厂不可用。"
        )
    return runtime_factory


def get_agent_runtime(request: Request) -> "AgentRuntime":
    """从应用 lifespan 状态取出共享的 Agent Runtime（FastAPI 依赖注入函数）。

    与 ``get_vector_search_service`` 的区别是这里返回 Runtime 整体、不下钻到某个字段：
    Agent 接口既要用 ``runtime.graph`` 发起运行，也可能要用同一个 Runtime 上的其他组件，
    而 Runtime 本身已经是不可变的组合对象，交出去不会被改坏。

    为什么 Agent 是进程级而不是每请求新建：编译 LangGraph 图、建模型客户端和
    checkpointer 连接池都有固定开销，每请求重建会把它乘上并发数。请求间的差异（自定义
    提示词）走 ``AgentContext``，不需要重新编译，见 ``agent.context``。

    Args:
        request: 当前 HTTP 请求，用于访问所属应用的 ``state``。

    Returns:
        lifespan 启动时装配、由并发请求共享的 ``AgentRuntime``。

    Raises:
        AgentRuntimeUnavailableError: 应用未经 lifespan 启动，或 Agent 装配失败（例如
            缺少模型凭据）；错误契约层会把它映射成稳定的 503。

    Notes:
        只读取进程内对象，不构造 Runtime，也不执行模型、Qdrant 或 PostgreSQL I/O。
    """

    runtime = getattr(request.app.state, "agent_runtime", None)
    if runtime is None:
        raise AgentRuntimeUnavailableError("Agent 运行时不可用。")
    return runtime


def get_agent_thread_service() -> AgentThreadService:
    """构造会话归属 Service（FastAPI 依赖注入函数）。

    与本模块其他依赖不同，它不从 ``app.state`` 取现成对象，而是每次现造一个：Service 自己无状态，
    真正贵的是数据库连接，而连接归它内部按需开关。

    **为什么传的是 session 工厂而不是 session**：主要调用方 ``POST /agent/chat`` 返回流式响应，
    ``Depends(get_db_session)`` 要等流关闭才归还连接，一次对话几分钟就是几分钟——几个并发能把业务
    连接池占空，而故障表现是检索页报数据库不可用，看起来跟 Agent 无关。详见
    docs/adr/0010-sse-routes-use-short-lived-db-sessions.md。

    Returns:
        持有进程级 session 工厂的 ``AgentThreadService``。

    Notes:
        只构造对象，不建连、不查库。离线测试整体覆盖本依赖（照 ``get_user_admin_service`` 的做法），
        因此不会真去连 PostgreSQL。
    """

    return AgentThreadService(async_session_factory)


def get_scheduler_runner(request: Request) -> "ScheduledJobRunner":
    """从应用 state 取出进程级定时任务调度器（FastAPI 依赖注入函数）。

    调度器在 ``create_app`` 时构造并存进 ``application.state.scheduler_runner``；无论
    ``SCHEDULER_ENABLED`` 是否开启，实例都存在——开关只决定 cron 循环是否启动，管理 API
    和手动触发在关闭状态下依然可用。

    Args:
        request: 当前 FastAPI 请求，用于访问所属应用的 ``state``。

    Returns:
        进程级 ``ScheduledJobRunner``。

    Raises:
        SchedulerRuntimeUnavailableError: 应用未经过 ``create_app`` 正常装配（比如测试
            直接 new 了裸应用）；错误契约层会把它映射成稳定的 503。

    Notes:
        只读取进程内对象，不启动调度器，也不执行任何 I/O。
    """

    runner = getattr(request.app.state, "scheduler_runner", None)
    if runner is None:
        raise SchedulerRuntimeUnavailableError("定时任务调度器不可用。")
    return runner


__all__ = [
    "PipelineWriteRuntimeFactory",
    "PipelineWriteRuntimeUnavailableError",
    "SchedulerRuntimeUnavailableError",
    "VectorSearchRuntimeUnavailableError",
    "get_agent_runtime",
    "get_agent_thread_service",
    "get_pipeline_write_runtime_factory",
    "get_scheduler_runner",
    "get_vector_search_service",
]
