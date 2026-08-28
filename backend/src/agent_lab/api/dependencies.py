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

from fastapi import Request

from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.services.vector_search_service import VectorSearchService


type PipelineWriteRuntimeFactory = Callable[[], PipelineWriteRuntime]


class VectorSearchRuntimeUnavailableError(RuntimeError):
    """ASGI lifespan 尚未提供进程级只读 Search Runtime。"""


class PipelineWriteRuntimeUnavailableError(RuntimeError):
    """应用没注册写 Runtime 工厂时的内部异常（会被映射成 503）。"""


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
    if not callable(runtime_factory):
        raise PipelineWriteRuntimeUnavailableError(
            "流水线写入运行时工厂不可用。"
        )
    return runtime_factory


__all__ = [
    "PipelineWriteRuntimeFactory",
    "PipelineWriteRuntimeUnavailableError",
    "VectorSearchRuntimeUnavailableError",
    "get_pipeline_write_runtime_factory",
    "get_vector_search_service",
]
