"""组装认证、只读检索、Agent 与公共任务 HTTP 入口；后台业务由独立 Worker 执行。"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agent_lab.agent.errors import AgentError
from agent_lab.agent.model_catalog import verify_configured_models
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.api.agent_chat import router as agent_chat_router
from agent_lab.api.agent_threads import router as agent_threads_router
from agent_lab.api.auth import router as auth_router
from agent_lab.api.health import router as health_router
from agent_lab.api.knowledge_bases import router as knowledge_bases_router
from agent_lab.api.sources import router as sources_router
from agent_lab.api.file_documents import router as file_documents_router
from agent_lab.api.document_review import router as document_review_router
from agent_lab.api.error_contract import build_file_document_error_response, build_processing_error_response
from agent_lab.knowledge.files import FileDocumentError
from agent_lab.knowledge.processing.lifecycle import ProcessingApplicationError
from agent_lab.api.document_search import router as document_search_router
from agent_lab.api.documents import router as documents_router
from agent_lab.api.dependencies import VectorSearchRuntimeUnavailableError
from agent_lab.api.error_contract import (
    build_agent_chat_error_response,
    build_knowledge_base_error_response,
    build_vector_search_error_response,
)
from agent_lab.api.pipeline import router as pipeline_router
from agent_lab.api.scheduled_jobs import router as scheduled_jobs_router
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
from agent_lab.config.qdrant import get_qdrant_settings
from agent_lab.config.scheduler import get_scheduler_settings
from agent_lab.config.settings import get_settings
from agent_lab.db.session import async_session_factory, engine
from agent_lab.knowledge.domain import KnowledgeBaseError
from agent_lab.qdrant.runtime import VectorSearchRuntime
from agent_lab.tasks.service import TaskService
from agent_lab.tasks.cron import CronSchedule
from agent_lab.task_assembly import build_task_service
from agent_lab.api.task_runs import router as task_runs_router, policy_router as task_policy_router
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
        "name": "knowledge-bases",
        "description": "知识库配置列表与超级用户创建、编辑和启停管理。",
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
            "手动受理一次后台同步与处理批次，最终统计通过任务执行编号查询。"
        ),
    },
    {
        "name": "tasks",
        "description": (
            "仅超级用户可访问的任务管理：周期配置、一次性执行、取消、重试及默认策略。"
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

    from agent_lab.knowledge.composition import build_knowledge_base_service
    from agent_lab.knowledge.adapters.visibility import PostgresDocumentVisibility

    return VectorSearchRuntime.build(
        get_qdrant_settings(),
        get_ollama_embedding_settings(),
        knowledge_base_scope=build_knowledge_base_service(),
        document_visibility=PostgresDocumentVisibility(async_session_factory),
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


async def verify_configured_llm_models() -> None:
    """启动时问一次上游「有哪些模型」，确认配置的两个模型名真的在列表里。

    为什么值得在启动路径上多花一次请求：模型名配错不会在启动时报错，也不会在第一次调用时
    报出一句好懂的话——曾经把 ``LLM_MODEL`` 配成 ``auto``（那是某些中转站的自动路由开关，
    按每次 HTTP 调用挑上游），症状是「查完资料不给回答」和「模型声称要调用没注册的工具」，
    排查方向被带到前端和流式管道上。一次极轻的 GET 换掉那种排查，是划算的。

    Raises:
        LlmModelNotListedError: 上游给出了非空列表，且配置的模型不在其中。由 lifespan 接住，
            结果是只关掉 ``/agent/*``。

    Notes:
        执行一次 HTTP GET（列模型，不产生 token 消耗），不写数据库、不碰 Qdrant。
        拉不到列表时静默放过——判据是「有没有证据说配置错了」，不是「上游健不健康」。
    """

    await verify_configured_models(get_llm_settings())


def create_app(
    *,
    runtime_factory: Callable[[], VectorSearchRuntime] = build_vector_search_runtime,
    task_service_factory: Callable[[], TaskService] = build_task_service,
    agent_runtime_factory: Callable[[VectorSearchService], AgentRuntime] = (
        build_agent_runtime
    ),
    environment_admin_sync: Callable[
        [], Awaitable[EnvironmentAdminSyncResult]
    ] = sync_configured_environment_admin,
    model_catalog_check: Callable[[], Awaitable[None]] = verify_configured_llm_models,
) -> FastAPI:
    """创建应用并显式注入任务受理与只读 Runtime，构造本身不执行外部 I/O。"""

    task_service = task_service_factory()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """管理 API 自己的认证、Agent 与搜索资源；不启动调度器或后台消费者。"""

        runtime: VectorSearchRuntime | None = None
        agent_runtime: AgentRuntime | None = None
        shutdown_error: Exception | None = None
        try:
            # 1、migration 已由部署步骤完成；先同步唯一环境管理员，再构造只读 Runtime。
            await environment_admin_sync()
            runtime = runtime_factory()
            application.state.vector_search_runtime = runtime
            # 3、Agent 复用上面那个检索 Service，所以必须排在它之后。
            application.state.agent_runtime = None
            try:
                await model_catalog_check()
                agent_runtime = agent_runtime_factory(runtime.service)
                await agent_runtime.open()
            except Exception as exc:
                # 只记类型：LLM 配置和数据库连接串里都有凭据，异常文本可能带出来。
                logger.error("Agent 运行时装配失败 error_type=%s", type(exc).__name__)
                agent_runtime = None
            else:
                application.state.agent_runtime = agent_runtime
            # 4、yield 之后是「运行期」：ASGI Server 在这里处理并发 HTTP 请求。
            yield
        finally:
            # 先释放依赖搜索的 Agent，再释放搜索资源。
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
            # 6、再释放数据库连接池；所有资源都要尝试释放，且不掩盖前面的异常
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
            "Embedding、Qdrant 索引与只读向量搜索。流水线接口是显式手动写操作；"
            "周期由独立 Celery Beat 受理，后台工作由 Celery Worker 执行。"
        ),
        version="0.1.0",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    application.state.task_service = task_service
    application.state.task_cron = CronSchedule(get_scheduler_settings().timezone)

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

    @application.exception_handler(KnowledgeBaseError)
    async def knowledge_base_error(_request: Request, error: KnowledgeBaseError) -> JSONResponse:
        """把内部组件的预期失败转换为脱敏 HTTP 契约。"""

        return build_knowledge_base_error_response(error)

    application.include_router(auth_router)
    @application.exception_handler(FileDocumentError)
    async def file_document_error(_request: Request, error: FileDocumentError) -> JSONResponse:
        return build_file_document_error_response(error)

    @application.exception_handler(ProcessingApplicationError)
    async def processing_error(_request: Request, error: ProcessingApplicationError) -> JSONResponse:
        return build_processing_error_response(error)

    application.include_router(file_documents_router)
    application.include_router(document_review_router)
    application.include_router(knowledge_bases_router)
    application.include_router(sources_router, dependencies=[Depends(current_superuser)])
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
    # 定时任务管理：配置变更直接决定后端会不会自动写外部系统，与 Pipeline 同级定级，
    # 只对超级用户开放。
    application.include_router(
        scheduled_jobs_router,
        dependencies=[Depends(current_superuser)],
    )
    application.include_router(task_runs_router, dependencies=[Depends(current_superuser)])
    application.include_router(task_policy_router, dependencies=[Depends(current_superuser)])
    # Agent 对所有登录账号开放，与检索页同级（见 docs/adr/0030-agent-open-to-all-accounts.md）。
    # 原来限超级用户的理由是「模型调用是真金白银」和「自定义提示词等于让调用方改模型行为」：
    # 前者是成本不是访问控制，后者戴着护栏（中间件在选定提示词后无条件追加「自定义提示词不能
    # 扩大」的资料边界段）。真正需要挡的「跨账号读对话」已由会话归属单独解决——它按 user_id
    # 判断、与角色无关。代价如实记录在 ADR 0030：模型额度对全部登录账号共享，账单随账号数增长。
    application.include_router(
        agent_chat_router,
        dependencies=[Depends(current_active_user)],
    )
    # 会话记录与对话同一道门。会话列表泄露的是标题（也就是用户问过什么），和对话内容同级敏感，
    # 所以两者必须同开同关，不能只放一个。
    application.include_router(
        agent_threads_router,
        dependencies=[Depends(current_active_user)],
    )
    return application


# Uvicorn 通过 ``agent_lab.main:app`` 导入这个 ASGI 应用对象。
app = create_app()
