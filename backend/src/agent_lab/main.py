"""把认证、搜索和手动流水线组装成 FastAPI 应用，并管理进程级资源生命周期。

本模块是应用的「装配根」(composition root)：所有下层模块在这里被组合成应用。
它做四件事：
1. 挂载账号密码 Cookie 登录，并在服务端区分普通有效用户与超级用户；
2. 创建进程级只读搜索 Runtime（Ollama + Qdrant 客户端），供搜索请求共享；
3. 注册一个「按请求创建」的写 Runtime 工厂，只有超级用户调用 Pipeline 才真正构造；
4. 应用关闭时统一释放搜索客户端和 SQLAlchemy 连接池。

启动阶段只访问 PostgreSQL 同步环境托管管理员，不探测 FreshRSS、Ollama 或 Qdrant；
真正的新闻同步、Collection 生命周期和索引发生在手动 POST 时。本模块不实现自动调度、
后台任务、LLM 或 RAG。
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agent_lab.agent.errors import AgentError
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.api.agent_chat import router as agent_chat_router
from agent_lab.api.auth import router as auth_router
from agent_lab.api.health import router as health_router
from agent_lab.api.document_search import router as document_search_router
from agent_lab.api.documents import router as documents_router
from agent_lab.api.dependencies import VectorSearchRuntimeUnavailableError
from agent_lab.api.error_contract import (
    build_agent_chat_error_response,
    build_vector_search_error_response,
)
from agent_lab.api.pipeline import router as pipeline_router
from agent_lab.api.vector_search import router as vector_search_router
from agent_lab.api.user_admin import router as user_admin_router
from agent_lab.auth.dependencies import current_active_user, current_superuser
from agent_lab.auth.bootstrap import (
    EnvironmentAdminSyncResult,
    sync_configured_environment_admin,
)
from agent_lab.config.llm import get_llm_settings
from agent_lab.config.ollama_embedding import (
    get_ollama_embedding_settings,
)
from agent_lab.config.freshrss import get_freshrss_settings
from agent_lab.config.qdrant import get_qdrant_settings
from agent_lab.config.settings import get_settings
from agent_lab.db.session import async_session_factory, engine
from agent_lab.pipeline.write_runtime import PipelineWriteRuntime
from agent_lab.qdrant.runtime import VectorSearchRuntime
from agent_lab.services.vector_search_service import VectorSearchService


logger = logging.getLogger(__name__)

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "auth",
        "description": (
            "内部账号密码登录、退出和当前用户读取；使用 HttpOnly Cookie，不开放注册。"
        ),
    },
    {
        "name": "user-admin",
        "description": "仅超级用户可访问的内部账号、权限、密码和登录会话管理。",
    },
    {
        "name": "health",
        "description": "只检查应用与 PostgreSQL 基础连接，不访问 Ollama 或 Qdrant。",
    },
    {
        "name": "vector-search",
        "description": (
            "只读新闻 Chunk 语义搜索；执行 query Embedding 和 Qdrant current Alias 查询。"
        ),
    },
    {
        "name": "document-search",
        "description": (
            "只读新闻文档分组语义搜索；每组返回最高分 Chunk 和有限的其他相关片段，"
            "完整正文通过文档详情接口按需读取。"
        ),
    },
    {
        "name": "documents",
        "description": "只在用户明确打开阅读视图时从 PostgreSQL 读取新闻完整纯正文。",
    },
    {
        "name": "pipeline",
        "description": (
            "手动、有界且同步的写入流水线；会访问 FreshRSS，并写 PostgreSQL 与 Qdrant。"
        ),
    },
    {
        "name": "agent",
        "description": (
            "只读新闻 Agent 对话；模型自行决定是否调用检索与阅读工具，过程以 SSE 流式"
            "返回。会话历史存 PostgreSQL，但不写任何新闻业务数据。"
        ),
    },
]


def build_vector_search_runtime() -> VectorSearchRuntime:
    """从环境配置组装默认只读 Runtime（只构造对象，不连接任何服务）。

    什么是 Runtime：把 Embedding Provider、Qdrant 客户端、索引规格（模型名/维度/
    相似度度量）组装好的「工具箱」，搜索 Service 拿它干活。
    「进程级」= 应用启动时创建一次、所有请求共享，而不是每个请求都新建客户端。

    Returns:
        绑定同一 ``VectorIndexSpec`` 的进程级 Ollama/Qdrant Search Runtime。

    Raises:
        pydantic.ValidationError: 环境配置不合法。
        VectorIndexConfigurationError: 模型、维度或 Search 组件规格不一致。

    Notes:
        只读取本地配置并构造 client，不执行 PostgreSQL、Ollama/Embedding 或 Qdrant I/O。
    """

    return VectorSearchRuntime.build(
        get_qdrant_settings(),
        get_ollama_embedding_settings(),
    )


def build_agent_runtime(search_service: VectorSearchService) -> AgentRuntime:
    """从环境配置组装进程级 Agent Runtime（只构造对象，不连接任何服务）。

    为什么参数是「已建好的搜索 Service」而不是自己再建一个：Agent 的 ``search_news``
    工具做的事和 ``POST /document-search`` 完全一样，共用同一个 Service 才能保证两条
    入口的检索行为一致，也避免多出一套 Ollama/Qdrant 连接池。这也是 Agent Runtime 必须
    在搜索 Runtime 之后装配的原因。

    Args:
        search_service: lifespan 已创建的进程级只读检索 Service。

    Returns:
        尚未建连的 Agent Runtime；调用方还要 ``await open()``。

    Raises:
        pydantic.ValidationError: LLM 环境配置缺失或不合法。
        LlmConfigurationError: provider 为 openai_compatible 但 API Key 为空。

    Notes:
        只读本地配置并构造对象，不执行模型、PostgreSQL 或 Qdrant I/O，也不建表——
        checkpointer 的四张表由 ``cli.py init-checkpointer`` 显式创建（见 ADR 0004）。
    """

    return AgentRuntime.build(
        llm_settings=get_llm_settings(),
        search_service=search_service,
        session_factory=async_session_factory,
        database_url=str(get_settings().database_url),
    )


def build_pipeline_write_runtime() -> PipelineWriteRuntime:
    """从当前配置组装「一次请求独占」的写入 Runtime（只构造对象，不连接服务）。

    为什么写 Runtime 是「每次请求新建」而搜索 Runtime 是「进程级共享」：写流水线
    会读写 FreshRSS、PostgreSQL、Ollama、Qdrant 四类外部资源，请求结束就整体关闭，
    避免长连接残留和请求之间状态串扰；搜索只读且高频，共享反而省资源。

    Returns:
        绑定 FreshRSS、Session factory、Ollama 和 Qdrant 写入组件的 Runtime。

    Raises:
        pydantic.ValidationError: FreshRSS、Ollama 或 Qdrant 环境配置缺失或不合法。
        VectorIndexConfigurationError: 模型、维度或索引组件规格不一致。

    Notes:
        函数只在 ``POST /pipeline/run-once`` 请求路径被调用。它读取本地配置并构造
        client，不执行 PostgreSQL、FreshRSS、Embedding 或 Qdrant I/O；startup 不调用。
    """

    return PipelineWriteRuntime.build(
        session_factory=async_session_factory,
        freshrss_settings=get_freshrss_settings(),
        qdrant_settings=get_qdrant_settings(),
        ollama_settings=get_ollama_embedding_settings(),
    )


def create_app(
    *,
    runtime_factory: Callable[[], VectorSearchRuntime] = build_vector_search_runtime,
    pipeline_runtime_factory: Callable[[], PipelineWriteRuntime] = (
        build_pipeline_write_runtime
    ),
    agent_runtime_factory: Callable[[VectorSearchService], AgentRuntime] = (
        build_agent_runtime
    ),
    environment_admin_sync: Callable[
        [], Awaitable[EnvironmentAdminSyncResult]
    ] = sync_configured_environment_admin,
) -> FastAPI:
    """创建 Agent Lab 的 FastAPI 应用（ASGI 应用）。

    为什么要接收「工厂函数」而不是直接构造 Runtime：把「怎么建 Runtime」和「建好的
    应用」解耦。生产用默认工厂读真实环境配置；离线测试注入 fake 工厂，就能证明
    HTTP 层不碰真实 Ollama/Qdrant/PostgreSQL。这就是依赖注入。

    Args:
        runtime_factory: 同步构造只读 Runtime 的工厂；生产使用默认配置，离线测试注入
            fake Runtime 以证明 HTTP 层不访问真实 Ollama、Qdrant 或 PostgreSQL。
        pipeline_runtime_factory: 按手动请求构造写入 Runtime 的同步工厂；注册时不会
            调用，离线测试可注入 fake 以验证同步执行、参数传递和资源关闭。
        agent_runtime_factory: 用已建好的检索 Service 构造 Agent Runtime 的同步工厂；
            离线测试注入 fake 以证明 HTTP 层不访问真实大模型。
        environment_admin_sync: 启动时同步环境托管管理员的异步函数；生产使用 PostgreSQL
            实现，离线 HTTP 测试注入无 I/O fake。

    Returns:
        已挂载登录、健康检查、受保护只读搜索、Agent 对话和受超级用户保护 Pipeline 的应用。

    Notes:
        创建应用对象本身不执行外部 I/O。lifespan 先访问 PostgreSQL 同步环境管理员，
        再构造 Search Runtime（不 ``ensure_ready``）和 Agent Runtime（会建 checkpointer
        连接）；写 Runtime 首次 POST 前不调用。
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """lifespan = FastAPI 的「进程启动/退出钩子」：起来时执行一次、退出前执行一次。

        这里在启动时构造共享 Search Runtime 和 Agent Runtime 存入 application.state，
        每个请求的依赖函数再通过 request.app.state 取用；关闭时尽力释放两个 Runtime 和
        数据库连接池。finally 里多层 try 的意义：任何一个资源关闭失败，都要继续尝试释放
        其余资源，且优先把第一个失败作为根因抛出，后续失败只作为附加说明。

        Agent Runtime 装配失败是**不致命**的：只记类型日志、把 state 留成 ``None``，
        搜索和流水线照常服务，只有 ``/agent/*`` 返回 503。反过来（启动直接崩）会让一个
        缺失的模型 API Key 把整个只读系统一起拖下线，那不是合理的失败半径。

        Args:
            application: 当前 FastAPI 实例，用 ``state`` 暴露 Runtime 给依赖函数。

        Yields:
            ``None``，控制权交给 ASGI Server 处理并发 HTTP 请求。

        Raises:
            Exception: 某个 Runtime 或 SQLAlchemy Engine 关闭失败；若多者都失败，保留第一个
                异常为根因并通过 exception note 记录其余的类型。

        Notes:
            启动为环境管理员执行 PostgreSQL 认证表 I/O，并为 Agent checkpointer 建
            PostgreSQL 连接，不访问新闻表、Ollama、Qdrant 或大模型；数据库 migration 与
            ``init-checkpointer`` 必须先完成。关闭只释放 Runtime 和连接池。
        """

        runtime: VectorSearchRuntime | None = None
        agent_runtime: AgentRuntime | None = None
        shutdown_error: Exception | None = None
        try:
            # 1、migration 已由部署步骤完成；先同步唯一环境管理员，再构造只读 Runtime。
            await environment_admin_sync()
            runtime = runtime_factory()
            application.state.vector_search_runtime = runtime
            # 2、Agent 复用上面那个检索 Service，所以必须排在它之后。
            application.state.agent_runtime = None
            try:
                agent_runtime = agent_runtime_factory(runtime.service)
                await agent_runtime.open()
            except Exception as exc:
                # 只记类型：LLM 配置和数据库连接串里都有凭据，异常文本可能带出来。
                logger.error("Agent 运行时装配失败 error_type=%s", type(exc).__name__)
                agent_runtime = None
            else:
                application.state.agent_runtime = agent_runtime
            # 3、yield 之后是「运行期」：ASGI Server 在这里处理并发 HTTP 请求。
            yield
        finally:
            # 4、关闭阶段：按「依赖方先关」的顺序，Agent 依赖检索 Service，所以先关它。
            for resource in (agent_runtime, runtime):
                if resource is None:
                    continue
                try:
                    await resource.close()
                except Exception as exc:
                    if shutdown_error is None:
                        shutdown_error = exc
                    else:
                        shutdown_error.add_note(
                            f"此外关闭 {type(resource).__name__} 也失败："
                            f"{type(exc).__name__}。"
                        )
            # 5、再释放数据库连接池；所有资源都要尝试释放，且不掩盖前面的异常
            try:
                await engine.dispose()
            except Exception as engine_error:
                if shutdown_error is not None:
                    shutdown_error.add_note(
                        "此外释放 SQLAlchemy Engine 也失败："
                        f"{type(engine_error).__name__}。"
                    )
                else:
                    raise
            finally:
                application.state.vector_search_runtime = None
                application.state.agent_runtime = None
            if shutdown_error is not None:
                raise shutdown_error

    application = FastAPI(
        title="Agent Lab API",
        description=(
            "Agent Lab 后端服务。当前提供 FreshRSS 新闻增量同步、Ollama/LangChain "
            "Embedding、Qdrant 索引与只读向量搜索。流水线接口是显式手动写操作，"
            "不会在启动时自动执行。"
        ),
        version="0.1.0",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    # 保存的是无 I/O 工厂而不是写 Runtime；只有显式 POST 才构造并运行写入组件。
    application.state.pipeline_write_runtime_factory = pipeline_runtime_factory

    @application.exception_handler(RequestValidationError)
    async def sanitized_request_validation_error(
        _request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        """把请求校验失败统一转成「不含原始输入」的 422 响应（脱敏）。

        为什么必须脱敏：Pydantic 校验错误的 input/ctx 里可能带着用户提交的完整
        query（比如"央行是否加息？"）或原始值，直接回显等于把请求内容泄露给响应方。
        这里只保留字段位置、稳定错误类型和安全消息，丢弃 input 和 ctx。

        本 handler 是应用级默认兜底，不认识任何具体路由：需要更强脱敏（例如请求体带
        明文密码、必须收敛成单一 ``invalid_request``）的路由改用
        ``SanitizedValidationRoute``，在自己的 route class 里接住校验错误，所以装配根
        不再维护「哪些 URL 前缀要特殊处理」的字符串常量。

        Args:
            _request: 当前 HTTP 请求；不读取 path 或 body，因此以下划线标记未使用。
            error: FastAPI/Pydantic 产生的结构化请求校验错误。

        Returns:
            仅含脱敏 detail 列表的 422 JSON 响应。

        Notes:
            只处理进程内校验结果，不记录请求 body，不执行数据库、Embedding 或 Qdrant
            I/O。``input`` 和 ``ctx`` 可能包含完整 query 或原始值，因此统一移除。
        """

        details = [
            {
                "type": item["type"],
                "loc": list(item["loc"]),
                "msg": item["msg"],
            }
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": details},
        )

    @application.exception_handler(VectorSearchRuntimeUnavailableError)
    async def vector_search_runtime_unavailable(
        _request: Request,
        error: VectorSearchRuntimeUnavailableError,
    ) -> JSONResponse:
        """把「lifespan 未启动导致搜索 Runtime 缺失」映射成稳定的 503。

        什么时候会触发：应用没走 lifespan 启动（比如测试环境只 import 了 app），
        或 Runtime 初始化失败。此时不能现场临时构造一个 Runtime 兜底——宁可明确
        返回 503，也不在请求路径里偷偷做有副作用的事。

        Args:
            _request: 当前 HTTP 请求；不读取 body，因此以下划线标记未使用。
            error: Search Service 依赖无法取得进程级 Runtime 时产生的分类异常。

        Returns:
            包含稳定 ``code/detail/retryable`` 的 503 JSON 响应。

        Notes:
            只执行进程内异常映射，不记录 query，不执行 PostgreSQL、Embedding 或 Qdrant
            I/O，也不会在请求路径临时构造 Runtime。
        """

        return build_vector_search_error_response(error)

    @application.exception_handler(AgentError)
    async def agent_error(_request: Request, error: AgentError) -> JSONResponse:
        """把流开始之前的 Agent 分类失败映射成稳定的 JSON 错误响应。

        注册在 ``AgentError`` 基类上而不是逐个子类：``AGENT_CHAT_ERROR_RULES`` 已经覆盖了
        本层的全部分类异常，多注册几个 handler 只是把同一张表拆成几个入口。

        为什么它只管得住「流开始之前」：``StreamingResponse`` 一旦返回，响应头就发出去了；
        生成器内部之后抛出的异常已经改不了状态码，那部分由 ``stream_agent_events`` 转成
        ``error`` 事件，走的仍是这张表，所以两条路径的 ``code`` 一致。

        Args:
            _request: 当前 HTTP 请求；不读取 body，因此以下划线标记未使用。
            error: Agent 层已分类的失败，通常来自 ``get_agent_runtime``。

        Returns:
            含稳定 ``code/detail/retryable`` 的 JSON 响应。

        Notes:
            只做进程内异常类型映射，不读异常文本，不执行任何 I/O。
        """

        return build_agent_chat_error_response(error)

    application.include_router(auth_router)
    application.include_router(health_router)
    application.include_router(
        vector_search_router,
        dependencies=[Depends(current_active_user)],
    )
    application.include_router(
        document_search_router,
        dependencies=[Depends(current_active_user)],
    )
    application.include_router(
        documents_router,
        dependencies=[Depends(current_active_user)],
    )
    application.include_router(
        pipeline_router,
        dependencies=[Depends(current_superuser)],
    )
    application.include_router(
        user_admin_router,
        dependencies=[Depends(current_superuser)],
    )
    # Agent 只读，但限超级用户：每次对话都是真金白银的模型调用，而且自定义系统提示词
    # 等于让调用方直接改模型行为。v1 先按「内部工具」定级，放宽是以后的事、收紧很难。
    application.include_router(
        agent_chat_router,
        dependencies=[Depends(current_superuser)],
    )
    return application


# Uvicorn 通过 ``agent_lab.main:app`` 导入这个 ASGI 应用对象。
app = create_app()
