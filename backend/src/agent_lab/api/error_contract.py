"""集中定义「异常类型 → 稳定脱敏 HTTP 错误」的声明式映射表和响应构造器。

本模块位于 FastAPI 边界层，是搜索、文档搜索、手动流水线和账号管理四类路由共用的
错误契约层。之前同一份映射在 ``vector_search``、``pipeline`` 和 ``user_admin`` 里各写
了一遍（if/elif 链和内联 dict），任何一处改动都会让对外契约悄悄分叉；这里把它收成
有序的 ``ErrorContractRule`` 表，各路由只负责「catch 什么异常」和「记什么日志」。

三条必须长期保住的设计约束：

1. 只读异常的「类型」。全模块不出现 ``str(error)``、``error.args`` 或任何异常文本拼接，
   因此数据库 URL、API Key、用户 query、新闻正文、Vector 和第三方原始响应都不可能
   进入 HTTP 响应。查表天然满足这一点，新增规则时不要破坏它。
2. 表的顺序有语义：刻意从「具体子类」排到「基础异常」，``resolve_error_contract``
   返回第一条命中的规则。若把基类规则提前，它会吞掉后面的子类规则。
3. ``code`` 是对外稳定契约，只能新增不能改值；``detail`` 只是给人看的中文概述，
   同一个 ``code`` 在所有表里必须对应同一句 ``detail``（由测试守护）。

本模块不执行 PostgreSQL、FreshRSS、Ollama/Embedding 或 Qdrant I/O，也不写日志——
记录异常类型是捕获现场的职责。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, cast

import httpx
from fastapi import Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from langchain_core.exceptions import OutputParserException
from ollama import RequestError as OllamaRequestError
from ollama import ResponseError as OllamaResponseError
from openai import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from psycopg import OperationalError as PsycopgOperationalError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from agent_lab.agent.errors import (
    AgentCheckpointerUnavailableError,
    AgentRuntimeUnavailableError,
    AgentThreadNotFoundError,
    ModelResponseInvalidError,
)
from agent_lab.api.dependencies import (
    PipelineWriteRuntimeUnavailableError,
    VectorSearchRuntimeUnavailableError,
)
from agent_lab.ingestion.freshrss_client import (
    FreshRSSAuthenticationError,
    FreshRSSConnectionError,
    FreshRSSError,
    FreshRSSProtocolError,
    FreshRSSServiceError,
    FreshRSSTimeoutError,
)
from agent_lab.ingestion.freshrss_mapper import FreshRSSMappingError
from agent_lab.pipeline.ollama_embedding_provider import (
    EmbeddingResponseError,
    OllamaAuthenticationError,
    OllamaConnectionError,
    OllamaEmbeddingError,
    OllamaModelNotFoundError,
    OllamaServiceError,
    OllamaTimeoutError,
)
from agent_lab.qdrant.index_spec import VectorIndexConfigurationError
from agent_lab.qdrant.lifecycle import (
    QdrantAliasConflictError,
    QdrantLifecycleError,
)
from agent_lab.qdrant.search import (
    QdrantSearchAuthenticationError,
    QdrantSearchConfigurationError,
    QdrantSearchConnectionError,
    QdrantSearchResponseError,
    QdrantSearchServiceError,
    QdrantSearchTargetNotFoundError,
    QdrantSearchTimeoutError,
    QdrantVectorSearchError,
)
from agent_lab.qdrant.store import QdrantPointStoreError
from agent_lab.schemas.pipeline import PipelineErrorResponse
from agent_lab.services.vector_search_service import QueryVectorValidationError


VectorSearchErrorCode = Literal[
    "search_runtime_unavailable",
    "embedding_authentication_failed",
    "embedding_unavailable",
    "embedding_timeout",
    "embedding_model_not_found",
    "embedding_response_invalid",
    "qdrant_authentication_failed",
    "qdrant_unavailable",
    "qdrant_timeout",
    "qdrant_target_missing",
    "qdrant_configuration_invalid",
    "qdrant_response_invalid",
    "qdrant_service_error",
]


class VectorSearchErrorResponse(BaseModel):
    """搜索上游失败时返回给客户端的统一错误格式（固定三字段）。

    只存在于单次 HTTP 响应里，不落库、不进日志。三个字段各有分工：
    - ``code``：稳定错误码字符串，客户端据此分支处理（如 embedding_timeout）；
    - ``detail``：给人看的安全中文概述，绝不含密钥、query、向量或第三方响应原文；
    - ``retryable``：是否「不改请求、稍后重试就可能成功」；true 不代表会自动重试。
    """

    code: VectorSearchErrorCode = Field(
        description=(
            "由 API 异常映射产生的必需稳定错误码；不可空，用于客户端区分 Embedding、"
            "Qdrant、timeout、配置和响应契约失败。"
        ),
    )
    detail: str = Field(
        min_length=1,
        description=(
            "由 API 层生成的必需安全中文错误概述；不可空，不包含用户 query、密钥、"
            "Vector、新闻正文或第三方原始响应。"
        ),
    )
    retryable: bool = Field(
        description=(
            "由错误类别推导的必需重试提示；不可空，true 仅表示稍后重试可能恢复，"
            "不代表服务或客户端会自动重试。"
        ),
    )

    model_config = ConfigDict(frozen=True)


@dataclass(frozen=True, slots=True)
class ErrorContractRule:
    """一条「异常类型组 → HTTP 错误契约」映射，即声明式错误表的一行。

    实例都是模块级常量，进程生命周期内不变、可安全共享；它不持有异常对象，因此
    永远不可能把异常文本带进响应。

    Attributes:
        exceptions: 命中本行的异常类型组；用 ``isinstance`` 匹配，因此子类同样命中，
            这也是「具体规则必须排在基类规则之前」的原因。
        status_code: 对外 HTTP 状态码（502 上游响应不可接受 / 503 暂不可用 /
            504 超时 / 500 未分类）。
        code: 稳定机器错误码，客户端据此分支；属于对外契约，只能新增不能改值。
        detail: 安全中文概述；同一个 ``code`` 必须始终对应同一句 detail。
        retryable: 是否「不改请求、稍后重试可能成功」，认证与配置类错误为 False。
    """

    exceptions: tuple[type[BaseException], ...]
    status_code: int
    code: str
    detail: str
    retryable: bool


# 同一个 code 的 detail 必须唯一，所以跨表复用的文案提成常量，避免两张表各写一遍后
# 慢慢分叉（这正是重构前 13 条英文 + 2 条中文混杂的成因）。
_EMBEDDING_UNAVAILABLE_DETAIL = "Embedding 服务当前不可用。"
_QDRANT_UNAVAILABLE_DETAIL = "向量数据库当前不可用。"
_QDRANT_CONFIGURATION_INVALID_DETAIL = "向量数据库索引配置无效。"

# 未分类异常的唯一兜底。全项目只保留这一条：各构造器不再各留一个防御分支，否则
# 「未知异常长什么样」又会分叉。code 沿用重构前流水线的值以保持对外契约稳定。
UNCLASSIFIED_ERROR_RULE = ErrorContractRule(
    exceptions=(BaseException,),
    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    code="pipeline_internal_error",
    detail="服务内部执行失败。",
    retryable=False,
)

# 请求体/路径参数校验失败的稳定契约。Pydantic 的 input/ctx 可能带着完整用户 query 或
# 明文密码，所以需要脱敏的路由统一返回这一条，不回显任何原始输入。
INVALID_REQUEST_RULE = ErrorContractRule(
    exceptions=(RequestValidationError,),
    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    code="invalid_request",
    detail="请求参数无效。",
    retryable=False,
)

# Ollama Embedding 的四个具体失败：读写两条链路的状态码、错误码和重试语义完全一致，
# 因此只在这里写一遍，由两张表共享。四者互为兄弟类，彼此顺序无关。
_OLLAMA_UPSTREAM_RULES: tuple[ErrorContractRule, ...] = (
    ErrorContractRule(
        exceptions=(OllamaAuthenticationError,),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="embedding_authentication_failed",
        detail="Embedding 服务认证失败。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(OllamaTimeoutError,),
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        code="embedding_timeout",
        detail="Embedding 服务请求超时。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(OllamaConnectionError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="embedding_unavailable",
        detail=_EMBEDDING_UNAVAILABLE_DETAIL,
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(OllamaModelNotFoundError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="embedding_model_not_found",
        detail="配置的 Embedding 模型不可用。",
        retryable=False,
    ),
)

# 只读搜索链路（POST /vector-search、POST /document-search）的完整错误表。
# 顺序要点：Runtime 缺失在最前（与上游无关）；四条 Ollama 具体失败先于
# EmbeddingResponseError 与 OllamaEmbeddingError 基类；Qdrant 具体失败先于
# QdrantVectorSearchError 基类。表覆盖了两个 endpoint 声明捕获的全部异常基类，
# 因此 UNCLASSIFIED_ERROR_RULE 不会出现在搜索响应里（它的 500 code 也不在
# VectorSearchErrorCode 里）。
VECTOR_SEARCH_ERROR_RULES: tuple[ErrorContractRule, ...] = (
    ErrorContractRule(
        exceptions=(VectorSearchRuntimeUnavailableError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="search_runtime_unavailable",
        detail="向量检索运行时不可用。",
        retryable=False,
    ),
    *_OLLAMA_UPSTREAM_RULES,
    ErrorContractRule(
        exceptions=(EmbeddingResponseError, QueryVectorValidationError),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="embedding_response_invalid",
        detail="Embedding 服务返回的向量无效。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(OllamaServiceError, OllamaEmbeddingError),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="embedding_unavailable",
        detail=_EMBEDDING_UNAVAILABLE_DETAIL,
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(QdrantSearchAuthenticationError,),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="qdrant_authentication_failed",
        detail="向量数据库认证失败。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(QdrantSearchTimeoutError,),
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        code="qdrant_timeout",
        detail="向量数据库查询超时。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(QdrantSearchConnectionError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="qdrant_unavailable",
        detail=_QDRANT_UNAVAILABLE_DETAIL,
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(QdrantSearchTargetNotFoundError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="qdrant_target_missing",
        detail="向量检索的 Alias 或 Collection 不可用。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(QdrantSearchConfigurationError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="qdrant_configuration_invalid",
        detail=_QDRANT_CONFIGURATION_INVALID_DETAIL,
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(QdrantSearchResponseError,),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="qdrant_response_invalid",
        detail="向量数据库返回的检索响应无效。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(QdrantSearchServiceError, QdrantVectorSearchError),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="qdrant_service_error",
        detail="向量数据库查询失败。",
        retryable=True,
    ),
)

# 手动写入链路（POST /pipeline/run-once）的完整错误表。
# 顺序要点：FreshRSS 具体失败先于 FreshRSSError 基类；共享的 Ollama 具体失败先于
# embedding_failed 分组；QdrantAliasConflictError 先于 QdrantLifecycleError 基类；
# 未分类异常落到 UNCLASSIFIED_ERROR_RULE 的 500。
# 读链路把 EmbeddingResponseError 归为 embedding_response_invalid，写链路归为
# embedding_failed——两个 code 都是既有对外契约，只能保持分叉，不能合并改值。
PIPELINE_ERROR_RULES: tuple[ErrorContractRule, ...] = (
    ErrorContractRule(
        exceptions=(FreshRSSAuthenticationError,),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="freshrss_authentication_failed",
        detail="FreshRSS 认证失败。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(FreshRSSConnectionError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="freshrss_unavailable",
        detail="FreshRSS 当前不可用。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(FreshRSSTimeoutError,),
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        code="freshrss_timeout",
        detail="FreshRSS 请求超时。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(FreshRSSProtocolError,),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="freshrss_response_invalid",
        detail="FreshRSS 返回的响应无效。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(FreshRSSServiceError, FreshRSSError, FreshRSSMappingError),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="freshrss_sync_failed",
        detail="FreshRSS 同步失败。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(SQLAlchemyError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="postgresql_unavailable",
        detail="PostgreSQL 操作失败。",
        retryable=True,
    ),
    *_OLLAMA_UPSTREAM_RULES,
    ErrorContractRule(
        exceptions=(
            EmbeddingResponseError,
            OllamaServiceError,
            OllamaEmbeddingError,
        ),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="embedding_failed",
        detail="Embedding 操作失败。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(QdrantAliasConflictError, VectorIndexConfigurationError),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="qdrant_configuration_invalid",
        detail=_QDRANT_CONFIGURATION_INVALID_DETAIL,
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(QdrantLifecycleError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="qdrant_unavailable",
        detail=_QDRANT_UNAVAILABLE_DETAIL,
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(QdrantPointStoreError,),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="qdrant_write_failed",
        detail="Qdrant 写入 Point 失败。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(ValidationError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="pipeline_configuration_invalid",
        detail="流水线配置无效。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(TimeoutError,),
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        code="pipeline_timeout",
        detail="流水线操作超时。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(PipelineWriteRuntimeUnavailableError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="pipeline_runtime_unavailable",
        detail="流水线写入运行时不可用。",
        retryable=False,
    ),
)

# 账号管理链路（/admin/users）的错误表。它只有数据库一类「按类型分类」的失败：
# 领域错误自带稳定 code 与安全中文 detail，走 build_error_response 直接构造。
# code 保留 user_admin_database_unavailable（与流水线的 postgresql_unavailable 是两个
# 既有契约值，不能互相替换）。
USER_ADMIN_ERROR_RULES: tuple[ErrorContractRule, ...] = (
    ErrorContractRule(
        exceptions=(SQLAlchemyError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="user_admin_database_unavailable",
        detail="账号管理服务暂时不可用。",
        retryable=True,
    ),
)

# 定时任务管理链路（/scheduled-jobs）的错误表。与账号管理同构：基础设施失败只有数据库
# 一类；领域错误（任务不存在、cron 无效、正在运行中冲突等）自带稳定 code 与安全中文
# detail，由路由的 _domain_error 映射成 404/409/422，不进本表。
# code 刻意与 user_admin / pipeline 的数据库不可用都不相同：三张表管的是不同的功能面，
# 日志和前端文案要能一眼区分是哪条链路的数据库故障。
SCHEDULED_JOB_ERROR_RULES: tuple[ErrorContractRule, ...] = (
    ErrorContractRule(
        exceptions=(SQLAlchemyError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="scheduled_job_database_unavailable",
        detail="定时任务存储当前不可用。",
        retryable=True,
    ),
)


AgentChatErrorCode = Literal[
    "agent_runtime_unavailable",
    "agent_checkpointer_unavailable",
    "agent_checkpointer_connection_lost",
    "agent_thread_not_found",
    "agent_thread_database_unavailable",
    "agent_internal_error",
    "llm_authentication_failed",
    "llm_request_blocked",
    "llm_timeout",
    "llm_rate_limited",
    "llm_unavailable",
    "llm_model_not_found",
    "llm_request_rejected",
    "llm_response_invalid",
    "llm_service_error",
]


# Agent 对话链路的错误表。
#
# 这张表和其他几张有一个结构性差异：它直接映射 ``openai``、``httpx`` 和 ``ollama`` 的
# 原始异常类型，没有先包成本项目自己的异常。其他链路（Embedding、Qdrant、FreshRSS）都是
# 在边界处 ``_raise_mapped_error`` 包一层再映射，那样更好——但这里做不到：模型调用发生在
# LangGraph 内部，我们不持有那个调用点，异常是从 ``graph.astream`` 冒出来的，没有可插入
# 包装的位置。所以「翻译」只能在这张表里做一次。
#
# 顺序要点（都是 isinstance 匹配，子类必须在基类之前）：
# - APITimeoutError ⊂ APIConnectionError，超时必须排在连接失败之前，否则超时会被当成
#   连接失败、retryable 语义还对但 code 会错；
# - AuthenticationError / PermissionDeniedError / RateLimitError / NotFoundError /
#   BadRequestError / InternalServerError 都 ⊂ APIStatusError，全部排在它之前；
#   AuthenticationError 与 PermissionDeniedError 互为兄弟类（401 与 403），彼此顺序无关；
# - APIStatusError 与 APIConnectionError 都 ⊂ APIError，APIError 作为兜底排最后。
#
# 已知的精度损失（有意接受）：``ollama.ResponseError`` 只能整体归到 llm_service_error。
# 区分它的 401/404 需要读 ``exc.status_code``，而本模块的第一条约束是只读异常类型；
# Embedding 链路能区分是因为它在自己的边界里读了 status_code 再包成不同类型，我们没有
# 那个边界。代价是 Ollama provider 下认证失败和模型不存在都报同一个 code。
AGENT_CHAT_ERROR_RULES: tuple[ErrorContractRule, ...] = (
    ErrorContractRule(
        exceptions=(AgentRuntimeUnavailableError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="agent_runtime_unavailable",
        detail="Agent 运行时不可用。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(AgentCheckpointerUnavailableError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="agent_checkpointer_unavailable",
        detail="会话记忆存储当前不可用。",
        retryable=True,
    ),
    # 会话记忆的连接在运行中途断了。和上一条的区别是「什么时候发现的」：上一条来自启动时
    # 连接池打不开（AgentRuntime.open），是配置或数据库不在的信号，重启前重试没意义；这一条
    # 是进程已经跑起来之后，池里某条连接被服务端掐掉（空闲回收、PG 重启、中间代理超时），
    # 重发同一个问题就能成功，所以 retryable=True。
    #
    # 为什么只挂 OperationalError 而不是 psycopg.Error：ProgrammingError 也是 psycopg.Error
    # 的子类，而「漏跑 init-checkpointer 导致表不存在」正是它——那个要留在兜底里报
    # agent_internal_error，不能被说成「连接中断、稍后重试」，否则运维会一直重试一个永远
    # 好不了的东西（见 docs/vps_deployment.md 第 4 节）。
    #
    # 注意这是**原生 psycopg** 的异常，不是 sqlalchemy.exc.OperationalError：checkpointer
    # 按 ADR 0004 走独立的 psycopg 池，不经过 SQLAlchemy，所以 SQLAlchemyError 那几条规则
    # 对它无效。它也不是内置 ConnectionError 的子类，指望 llm_unavailable 那条捞住它同样
    # 不成立——两条路都试过才落到这里单开一条。
    ErrorContractRule(
        exceptions=(PsycopgOperationalError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="agent_checkpointer_connection_lost",
        detail="会话记忆存储的连接已中断。",
        retryable=True,
    ),
    # 会话归属校验失败：id 不存在，或存在但属于别的账号。两种情况共用一个 404，理由见
    # ``AgentThreadNotFoundError`` 的 docstring（区分开会泄露 id 是否存在）。
    #
    # 它排在这张表里而不是单开一张：``/agent/chat`` 在流开始前就要校验归属，所以这个 code
    # 会从对话链路上冒出来，不是只有会话增删查路由才用得到。
    ErrorContractRule(
        exceptions=(AgentThreadNotFoundError,),
        status_code=status.HTTP_404_NOT_FOUND,
        code="agent_thread_not_found",
        detail="会话不存在或已被删除。",
        retryable=False,
    ),
    # 业务库（SQLAlchemy 这一侧）不可用。必须有这条：``/agent/chat`` 从「会话归属」这个功能开始
    # 会读写 ``agent_threads``，而本表原有的数据库规则挂的是 ``PsycopgOperationalError``——那是
    # checkpointer 走的独立 psycopg 池（见 ADR 0004），管不到 SQLAlchemy 抛出的异常。少了这条，
    # 业务库故障会落进 ``agent_internal_error`` 兜底，前端只能显示「未分类的服务错误」，
    # 而这其实是一个明确可重试的故障。
    #
    # detail 与 ``user_admin_database_unavailable`` 刻意不同句：两个 code 说同一句话会让
    # 「同 code 同 detail」那条测试约束失去区分意义，也让日志里分不清是哪条链路。
    ErrorContractRule(
        exceptions=(SQLAlchemyError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="agent_thread_database_unavailable",
        detail="会话记录存储当前不可用。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(AuthenticationError,),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="llm_authentication_failed",
        detail="大模型服务认证失败。",
        retryable=False,
    ),
    # 403 单独一条而不是和 401 合并：合并过的版本把「中转站按 User-Agent 拦掉了这个客户端」
    # 报成「认证失败」，排查因此从换 Key 开始，而 Key 一直是好的。两者都不可重试、都是 502，
    # 但要查的地方完全不同，所以分开给码和文案。
    ErrorContractRule(
        exceptions=(PermissionDeniedError,),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="llm_request_blocked",
        detail="大模型服务拒绝了本次请求（凭据可能有效，但请求被判定为不允许）。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(APITimeoutError, httpx.TimeoutException),
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        code="llm_timeout",
        detail="大模型服务请求超时。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(RateLimitError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="llm_rate_limited",
        detail="大模型服务达到调用频率上限。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(NotFoundError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="llm_model_not_found",
        detail="配置的大模型不可用。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(BadRequestError, UnprocessableEntityError),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="llm_request_rejected",
        detail="大模型服务拒绝了本次请求。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(
            APIConnectionError,
            httpx.ConnectError,
            httpx.NetworkError,
            ConnectionError,
        ),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="llm_unavailable",
        detail="大模型服务当前不可用。",
        retryable=True,
    ),
    ErrorContractRule(
        exceptions=(
            ModelResponseInvalidError,
            APIResponseValidationError,
            OutputParserException,
        ),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="llm_response_invalid",
        detail="大模型返回的内容无法使用。",
        retryable=False,
    ),
    ErrorContractRule(
        exceptions=(
            InternalServerError,
            APIStatusError,
            APIError,
            OpenAIError,
            OllamaResponseError,
            OllamaRequestError,
        ),
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="llm_service_error",
        detail="大模型服务调用失败。",
        retryable=True,
    ),
    # 本表自带的兜底行。这是对「全项目只有 UNCLASSIFIED_ERROR_RULE 一条兜底」的一处
    # 有意例外，理由是这条链路的异常集合是开放的：搜索链路能不写兜底，是因为 endpoint
    # 显式声明了 catch 哪几个基类、表覆盖了它们；而模型调用发生在 LangGraph 内部，中间
    # 还夹着工具、checkpointer 和用户自定义提示词，能冒出什么异常我们无法穷举。
    # 不写这一行的话未知异常会落到 pipeline_internal_error——那是「手动流水线」的契约
    # code，出现在 Agent 的 SSE 流里会让前端按错误的语义去查文案表。
    ErrorContractRule(
        exceptions=(Exception,),
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="agent_internal_error",
        detail="Agent 执行失败。",
        retryable=False,
    ),
)


# Agent 工具链路的错误表。它和上面四张表有一处本质区别：命中结果不会变成 HTTP 响应，
# 而是被 ToolErrorMiddleware 写成一句 ToolMessage 交回模型，所以这里只用到 detail
# （安全中文）和 code（写日志）两个字段，status_code 不参与对外语义，取与同类失败
# 一致的值只为让「同一个 code 对应同一句 detail」这条约束继续成立。
# 第一版工具全是只读的（见 docs/adr/0003-agent-v1-is-read-only.md），失败模式与只读
# 搜索链路同构，因此直接复用 VECTOR_SEARCH_ERROR_RULES，只补一条数据库失败——
# read_document 走 PostgreSQL，不经过 Embedding 和 Qdrant。
# retryable 在这条链路上同样不直接用：要不要换个检索词重试由模型自己决定，我们只把
# 「失败了、原因是这一类」如实告诉它。
AGENT_TOOL_ERROR_RULES: tuple[ErrorContractRule, ...] = (
    *VECTOR_SEARCH_ERROR_RULES,
    ErrorContractRule(
        exceptions=(SQLAlchemyError,),
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="agent_tool_database_unavailable",
        detail="新闻数据库当前不可用。",
        retryable=True,
    ),
    # 兜底行，理由同 AGENT_CHAT_ERROR_RULES：工具体内可能抛出我们没预料到的异常，
    # 而这句文案是要交给模型看的，落到 pipeline_internal_error 的「服务内部执行失败」
    # 会让模型以为是它自己的调用方式有问题。
    ErrorContractRule(
        exceptions=(Exception,),
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="agent_tool_failed",
        detail="工具执行时发生未预期的错误。",
        retryable=False,
    ),
)


# 两条搜索路由共用的「已分类上游失败」捕获元组。放在错误表旁边是为了让「catch 什么」
# 和「映射成什么」一起演进：这三个基类都在 VECTOR_SEARCH_ERROR_RULES 里有对应规则，
# 所以捕获到的异常一定能查到表，不会掉进 500 兜底。
# 不含 VectorSearchRuntimeUnavailableError：它由依赖注入在进入 endpoint 前抛出，
# endpoint 内的 try 永远接不到，统一由应用级 handler 映射成同一个 503。
SEARCH_UPSTREAM_EXCEPTIONS: tuple[type[BaseException], ...] = (
    OllamaEmbeddingError,
    QueryVectorValidationError,
    QdrantVectorSearchError,
)


def resolve_error_contract(
    error: BaseException,
    rules: tuple[ErrorContractRule, ...],
) -> ErrorContractRule:
    """在有序错误表里查出第一条匹配当前异常「类型」的规则。

    只用 ``isinstance`` 判断，绝不读取 ``str(error)``、``error.args`` 或异常属性，因此
    上游细节不可能顺着返回值泄漏。「第一条命中」意味着表的顺序就是优先级：具体子类
    必须排在基础异常之前，否则基类规则会提前吞掉子类。

    Args:
        error: 已捕获的异常对象；本函数只读取它的类型。
        rules: 该链路的有序错误表，如 ``VECTOR_SEARCH_ERROR_RULES``。

    Returns:
        命中的 ``ErrorContractRule``；表内无匹配时返回全项目唯一的
        ``UNCLASSIFIED_ERROR_RULE``（500）。

    Notes:
        纯内存查表，不执行任何 I/O，也不写日志。
    """

    for rule in rules:
        if isinstance(error, rule.exceptions):
            return rule
    return UNCLASSIFIED_ERROR_RULE


def build_error_response(
    status_code: int,
    code: str,
    detail: str,
    *,
    retryable: bool,
) -> JSONResponse:
    """按统一的 ``code/detail/retryable`` 三字段结构构造脱敏错误响应。

    所有路由的错误响应都从这里出去，客户端不需要为不同失败路径写不同解析逻辑。
    调用方传入的 ``detail`` 必须是预先写好的安全中文常量或领域层给出的安全文案，
    不能是异常文本。

    Args:
        status_code: 对外 HTTP 状态码。
        code: 稳定机器错误码。
        detail: 安全中文概述。
        retryable: 是否「不改请求、稍后重试可能成功」。

    Returns:
        序列化后的 ``JSONResponse``。
    """

    return JSONResponse(
        status_code=status_code,
        content={"code": code, "detail": detail, "retryable": retryable},
    )


def build_vector_search_error_response(error: BaseException) -> JSONResponse:
    """把已知的搜索异常按类型映射成稳定的 502/503/504 搜索错误响应。

    Args:
        error: 已分类的 Runtime、Ollama、query Vector 或 Qdrant 边界异常；只读其类型。

    Returns:
        与 OpenAPI ``VectorSearchErrorResponse`` 一致的 JSON 响应。

    Notes:
        走 Pydantic 模型构造，顺带校验 ``code`` 确实属于 ``VectorSearchErrorCode``。
        ``VECTOR_SEARCH_ERROR_RULES`` 覆盖了搜索 endpoint 声明捕获的全部异常基类，
        因此这里的 ``cast`` 成立、也不会落到 500 兜底。不读异常文本，不执行任何 I/O。
    """

    rule = resolve_error_contract(error, VECTOR_SEARCH_ERROR_RULES)
    payload = VectorSearchErrorResponse(
        code=cast(VectorSearchErrorCode, rule.code),
        detail=rule.detail,
        retryable=rule.retryable,
    )
    return JSONResponse(
        status_code=rule.status_code,
        content=payload.model_dump(mode="json"),
    )


def build_agent_chat_error_response(error: BaseException) -> JSONResponse:
    """把「流还没开始就失败」的 Agent 异常映射成稳定的 JSON 错误响应。

    只服务于流开始之前的那一小段：取依赖、拿 Runtime。一旦第一帧 SSE 发出，响应头就已经
    走了，状态码再也改不了，之后的失败只能作为 ``error`` 事件送达（见
    ``schemas.agent_chat.AgentErrorEvent`` 的说明）。两条路径共用
    ``AGENT_CHAT_ERROR_RULES``，所以同一种失败在两处拿到的是同一个 ``code``。

    Args:
        error: 已分类的 Agent 层异常；只读其类型。

    Returns:
        含稳定 ``code/detail/retryable`` 的 JSON 响应。

    Notes:
        纯内存查表，不读异常文本，不执行任何 I/O。
    """

    rule = resolve_error_contract(error, AGENT_CHAT_ERROR_RULES)
    return build_error_response(
        rule.status_code,
        rule.code,
        rule.detail,
        retryable=rule.retryable,
    )


def build_pipeline_error_response(error: BaseException) -> JSONResponse:
    """把批次级异常按类型映射成稳定、脱敏的流水线错误响应。

    比搜索响应多一个 ``error_type`` 字段：它只放异常的 Python 类名，方便运维定位是
    哪类上游失败，而不像 ``str(error)`` 那样可能带出数据库 URL、密钥或正文。

    Args:
        error: Runtime 构造、执行或关闭阶段捕获的根异常；只读其类型。

    Returns:
        包含固定 ``code/detail/error_type/retryable`` 的 JSONResponse；未分类异常为 500。

    Notes:
        不调用 ``str(error)``，不执行任何 I/O。
    """

    rule = resolve_error_contract(error, PIPELINE_ERROR_RULES)
    payload = PipelineErrorResponse(
        code=rule.code,
        detail=rule.detail,
        error_type=type(error).__name__,
        retryable=rule.retryable,
    )
    return JSONResponse(
        status_code=rule.status_code,
        content=payload.model_dump(mode="json"),
    )


def build_user_admin_error_response(error: BaseException) -> JSONResponse:
    """把账号管理的基础设施异常按类型映射成稳定 503。

    Args:
        error: 账号管理接口捕获的数据库异常；只读其类型。

    Returns:
        含稳定 ``code/detail/retryable`` 的 JSONResponse。

    Notes:
        不读异常文本，因此连接串、SQL 和账号数据不会进入响应；不执行任何 I/O。
    """

    rule = resolve_error_contract(error, USER_ADMIN_ERROR_RULES)
    return build_error_response(
        rule.status_code,
        rule.code,
        rule.detail,
        retryable=rule.retryable,
    )


def build_scheduled_job_error_response(error: BaseException) -> JSONResponse:
    """把定时任务管理的基础设施异常按类型映射成稳定 503。

    Args:
        error: 定时任务接口捕获的数据库异常；只读其类型。

    Returns:
        含稳定 ``code/detail/retryable`` 的 JSONResponse。

    Notes:
        不读异常文本，因此连接串、SQL 和任务配置不会进入响应；不执行任何 I/O。
    """

    rule = resolve_error_contract(error, SCHEDULED_JOB_ERROR_RULES)
    return build_error_response(
        rule.status_code,
        rule.code,
        rule.detail,
        retryable=rule.retryable,
    )


class SanitizedValidationRoute(APIRoute):
    """让路由自己把请求校验失败转成稳定 ``invalid_request`` 响应的 APIRoute。

    为什么做成 route class 而不是在 composition root 里判断 URL 前缀：脱敏是路由自身
    的属性（它的请求体里有明文密码或用户 query），不是 ``main.py`` 需要维护的一串路径
    常量。挂上 ``APIRouter(route_class=SanitizedValidationRoute)`` 即生效，新增需要脱敏
    的路由不必回头改装配根。

    FastAPI 在 route handler 内部完成请求体与路径参数校验并抛出
    ``RequestValidationError``，所以这里包一层就能在它冒泡到应用级 handler 之前接住；
    未挂本 route class 的路由仍走应用级的脱敏 422。

    生命周期与普通 ``APIRoute`` 一致：随应用创建、进程内不变。
    """

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        """包装原始 handler，把请求校验失败替换成固定错误契约。

        Returns:
            与原 handler 等价、但只在 ``RequestValidationError`` 时改写响应的协程函数。

        Notes:
            只丢弃校验错误对象（其 ``input``/``ctx`` 可能含明文密码或完整 query），
            不读取、不记录请求 body，也不执行任何 I/O。
        """

        original_route_handler = super().get_route_handler()

        async def sanitized_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            # 故意不写 ``as error``：拿不到那个对象，就没人会顺手把它 log 出来或塞进
            # 响应。它的 ``input`` 字段装的是原始请求体，明文密码就在里面。
            except RequestValidationError:
                return build_error_response(
                    INVALID_REQUEST_RULE.status_code,
                    INVALID_REQUEST_RULE.code,
                    INVALID_REQUEST_RULE.detail,
                    retryable=INVALID_REQUEST_RULE.retryable,
                )

        return sanitized_route_handler


__all__ = [
    "AGENT_CHAT_ERROR_RULES",
    "AGENT_TOOL_ERROR_RULES",
    "INVALID_REQUEST_RULE",
    "PIPELINE_ERROR_RULES",
    "SCHEDULED_JOB_ERROR_RULES",
    "SEARCH_UPSTREAM_EXCEPTIONS",
    "UNCLASSIFIED_ERROR_RULE",
    "USER_ADMIN_ERROR_RULES",
    "VECTOR_SEARCH_ERROR_RULES",
    "AgentChatErrorCode",
    "ErrorContractRule",
    "SanitizedValidationRoute",
    "VectorSearchErrorCode",
    "VectorSearchErrorResponse",
    "build_agent_chat_error_response",
    "build_error_response",
    "build_pipeline_error_response",
    "build_scheduled_job_error_response",
    "build_user_admin_error_response",
    "build_vector_search_error_response",
    "resolve_error_contract",
]
