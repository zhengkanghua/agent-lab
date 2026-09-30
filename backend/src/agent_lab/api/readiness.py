"""提供只读的就绪端点（``GET /ready``），把「能服务」与「已就绪」分开。

为什么新增一个端点、而不是把 ``/health`` 做深：``/health`` 刻意只回答「进程活着、数据库能连」，
理由是不让健康检查本身成为新的故障来源（见 ``api/health.py`` 的模块说明）。部署时需要的却是另
一个问题——「这个容器现在能不能接班」——它必须把「会话记忆够不够得着」和「启动后那次上游配置
校验的结论」一起看。把这两项塞进 ``/health``，运维与云监控拿到的就是一个失真的信号，所以这里
是新增而不是改造。

判据**三样一起看**（少任何一样，都会让一个「检索能用、提问用不了」的新版本被部署换上去）：

1. 业务库能执行一次最小查询；
2. checkpointer 连接池能取到连接——它是独立连接池，业务库探活探不到它；
3. 启动后那次上游「列模型」校验（``ModelCatalogVerdict``）的当前结论：还没出结论、或已经失败，
   都算未就绪。

**上游「不回答」不算未就绪。** 那次校验的判据是「有没有证据说配置错了」，拿不到模型列表时它
放行（见 ``agent/model_catalog.py``），所以本端点反映的是那次校验的结论，不是上游的可用性。
把上游抖动做成部署门禁，等于让别人的故障挡住自己的发布。

**响应体只给状态**，与 ``/health`` 的固定消息同一原则：不写哪一项没过、不放依赖名与错误原文。
真正的边界是端口与安全组，不是响应体里多写几个字。它也**不进对外接口定义**
（``include_in_schema=False``）：这是给编排用的内部端点，产品侧没有消费者，前端的生成类型因此
不需要重新生成。

**它的结论在 Swarm 里是有后果的。** 容器健康检查不过，agent 会把容器杀掉重启（实测）——所以
「配置写错」的版本会被反复杀掉，而不是像在本机开发那样长期半好地在线。部署场景下这正是我们要
的（旧任务不被替掉、更新自己回滚）；代价是「进程照起、检索照服务」只在被杀之前那几十秒内成立。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.agent.model_catalog import LlmModelNotListedError
from agent_lab.config.settings import Settings, get_settings
from agent_lab.db.session import get_db_session


logger = logging.getLogger(__name__)
router = APIRouter(tags=["readiness"])


class ModelCatalogVerdict:
    """启动后并发跑的那次上游「列模型」校验，以及它当前的结论。

    **为什么并发跑而不是在生命周期里 await**：这次校验要发一次 HTTP GET，上游卡住时最长占满
    ``MODEL_CATALOG_TIMEOUT_SECONDS``。它不属于「能服务」的前提（检索、阅读、流水线都不需要
    生成式模型），把它挡在启动路径上等于让所有请求陪着一起等；而它要表达的那件事——「配置写错
    的版本不许替掉好版本」——由本对象的结论经 ``/ready`` 表达，不需要占用启动时间。

    三态：``pending``（还没出结论）→ ``ok`` / ``failed``。``pending`` 也算未就绪：就绪是给部署
    看门的信号，「还没问过上游」不能说这个版本能接班。

    Attributes:
        state: 当前结论；``pending`` / ``ok`` / ``failed``。
        error: 失败时那个异常，其余情况为 ``None``。调用方只该读它的**类型**：对话链路据此判断
            这次失败是不是「配置的模型名不在上游列表里」，从而把这次提问直接判成
            ``llm_model_not_found``，而不是让它跑到模型调用那一步、再靠上游报错的形状去猜。
    """

    def __init__(self) -> None:
        self._state: Literal["pending", "ok", "failed"] = "pending"
        self._error: BaseException | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def state(self) -> Literal["pending", "ok", "failed"]:
        """当前结论。"""

        return self._state

    @property
    def error(self) -> BaseException | None:
        """失败时的异常对象（未完成或成功时为 ``None``）。"""

        return self._error

    def start(self, check: Callable[[], Awaitable[None]]) -> None:
        """把这次校验挂到后台跑，立刻返回，不等结论。

        Args:
            check: 校验协程工厂；生产实现是 ``main.verify_configured_llm_models``，
                离线测试注入不发请求的替身。

        Notes:
            必须在一个运行中的事件循环里调用（生命周期里天然满足）。只创建任务，
            不执行 I/O，也不吞掉异常——异常在 ``_run`` 里落成 ``error`` 与 ``failed``。
        """

        self._task = asyncio.create_task(self._run(check), name="agent-lab-model-catalog-check")

    async def _run(self, check: Callable[[], Awaitable[None]]) -> None:
        """跑一次校验并把结论落到本对象上；异常不往外抛。"""

        try:
            await check()
        except LlmModelNotListedError as exc:
            # 这一条是「确实有证据说配置错了」，与下面那条的区别见 ``error`` 的说明。
            self._error = exc
            self._state = "failed"
            logger.error("启动配置校验未通过 error_type=%s", type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - 校验失败不能拖垮进程，只把它记成未就绪
            # 只记类型：LLM 配置里有凭据，异常文本可能把它带出来。
            self._error = exc
            self._state = "failed"
            logger.error("启动配置校验失败 error_type=%s", type(exc).__name__)
        else:
            self._state = "ok"

    async def wait(self) -> None:
        """等这次校验出结论。

        收尾不靠它（见 ``close``），它给测试用：离线用例要确定性地拿到「结论已经落下来」
        这个状态，而不是靠 sleep 去赌一个毫秒级的任务调度。
        """

        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task

    async def close(self) -> None:
        """取消还在跑的校验，不留悬空任务。

        Notes:
            不等待结论：收尾时那个结论已经没有消费者了。已经被取消或已经完成时是空操作。
        """

        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await self._task


class ReadinessResponse(BaseModel):
    """就绪端点的响应体：只有状态，没有依赖名与错误原文。"""

    status: Literal["ready", "not_ready"] = Field(
        description="进程当前是否可以接班：检索与阅读可用、会话记忆可达、启动配置校验未发现配置错误。",
    )


def get_model_catalog_verdict(request: Request) -> ModelCatalogVerdict | None:
    """从应用状态取出启动配置校验的结论（FastAPI 依赖注入函数）。

    取不到时返回 ``None`` 而不是抛异常：两个调用方都不把它当错误——``/ready`` 按「未就绪」
    处理，对话链路按「没有证据说配置错」处理。用异常表达「应用没走过生命周期」会让这两种
    语义都变成 500，而它们各自都有更准确的答案。

    Args:
        request: 当前 HTTP 请求，用于访问所属应用的 ``state``。

    Returns:
        生命周期里建好的 ``ModelCatalogVerdict``；应用未经过生命周期启动时为 ``None``。

    Notes:
        只读取进程内对象，不构造也不触发任何 I/O。
    """

    return getattr(request.app.state, "model_catalog_verdict", None)


@router.get(
    "/ready",
    include_in_schema=False,
    summary="内部就绪端点：新版本能不能接班",
    description=(
        "只读端点，不鉴权。三样一起看：业务库能执行最小查询、checkpointer 连接池能取到连接、"
        "启动后那次上游配置校验的结论。全部通过返回 200 与 ``{\"status\": \"ready\"}``，"
        "否则返回 503 与 ``{\"status\": \"not_ready\"}``；两种响应体都不含依赖名与错误原文。"
    ),
)
async def readiness(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    """评估当前进程能不能接班，并把结论如实报成 200 / 503。

    刻意不抛异常：这个端点被编排按固定间隔轮询，「探活本身失败」与「判据不过」对调用方是同一个
    结论（不能接班），所以两种情况的响应形状必须一致，不能让其中一个变成 500 或断连接。

    Args:
        request: 当前 HTTP 请求，用于取出进程级 Agent Runtime 与启动配置校验结论。
        session: FastAPI 注入的业务库会话，用于执行最小查询。
        settings: 应用配置，提供探活的等待上限。

    Returns:
        就绪时 200、未就绪时 503；响应体都只有 ``status`` 一个字段。

    Notes:
        只执行两次最小查询（业务库与 checkpointer 各一次 ``SELECT 1``），不写任何数据、
        不访问 Ollama/Qdrant/大模型。checkpointer 那个探针走的是独立连接池，与业务库那次
        不共用连接。
    """

    ready = await evaluate_readiness(
        request,
        session=session,
        timeout=settings.database_health_check_timeout,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ReadinessResponse(status="ready" if ready else "not_ready").model_dump(),
    )


async def evaluate_readiness(
    request: Request,
    *,
    session: AsyncSession,
    timeout: float,
) -> bool:
    """三样一起看，任何一样不过就是未就绪。

    Args:
        request: 当前 HTTP 请求，用于取出进程级 Agent Runtime 与启动配置校验结论。
        session: 业务库会话。
        timeout: 两个探针各自的等待上限（复用配置里的健康检查超时：两者都是「等一个有界响应」，
            语义相同，不再多一个旋钮）。

    Returns:
        三样都通过时为 ``True``。

    Notes:
        探针失败只记异常类型，不把异常文本带进响应体或日志正文——连接串里有凭据。
    """

    runtime = getattr(request.app.state, "agent_runtime", None)
    verdict = get_model_catalog_verdict(request)
    if runtime is None or verdict is None:
        # 两种情况都算未就绪：Agent 装配失败（进程照起、检索照服务，但提问这条链路做不了），
        # 或应用根本没走过生命周期（测试里直接 import 出来的 app）。
        return False
    if verdict.state != "ok":
        # pending 与 failed 都不算就绪：前者是「还没问过上游」，后者是「已经问到配置错了」。
        return False
    if not await _business_database_reachable(session, timeout=timeout):
        return False
    return await _checkpointer_pool_reachable(runtime, timeout=timeout)


async def _business_database_reachable(session: AsyncSession, *, timeout: float) -> bool:
    """业务库能不能执行一次最小查询。"""

    try:
        # 应用级总超时可以覆盖 DNS 返回多个地址、驱动逐个尝试所产生的累计等待。
        async with asyncio.timeout(timeout):
            await session.execute(text("SELECT 1"))
    except (TimeoutError, SQLAlchemyError) as exc:
        logger.error("就绪检查：业务库探活失败 error_type=%s", type(exc).__name__)
        return False
    return True


async def _checkpointer_pool_reachable(runtime: object, *, timeout: float) -> bool:
    """checkpointer 连接池能不能取到连接。

    ``pool`` 为 ``None`` 时不作为判据：那表示注入了外部 checkpointer（离线测试用
    ``InMemorySaver``），本进程没有自己的池可探，把它算成「不过」会让那种装配永远不就绪。
    生产的 ``AgentRuntime.build`` 一定自建池，所以这条路不会掩盖真实故障。
    """

    pool = getattr(runtime, "pool", None)
    if pool is None:
        return True
    try:
        async with asyncio.timeout(timeout):
            async with pool.connection() as connection:
                # 取到连接还不够：psycopg 的池会把坏连接交出来（它只在归还时巡检），
                # 所以真发一条最小 SQL，才算「够得着」。
                await connection.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - 池超时、池已关闭、连接失败分属不同层级，都算未就绪
        logger.error("就绪检查：checkpointer 连接池取不到连接 error_type=%s", type(exc).__name__)
        return False
    return True


__all__ = [
    "ModelCatalogVerdict",
    "ReadinessResponse",
    "evaluate_readiness",
    "get_model_catalog_verdict",
    "router",
]
