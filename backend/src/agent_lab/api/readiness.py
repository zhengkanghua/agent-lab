"""提供只读的就绪端点（``GET /ready``），把「能服务」与「已就绪」分开。

为什么新增一个端点、而不是把 ``/health`` 做深：``/health`` 刻意只回答「进程活着、数据库能连」，
理由是不让健康检查本身成为新的故障来源（见 ``api/health.py`` 的模块说明）。部署时需要的却是另
一个问题——「这个容器现在能不能接班」——它必须把「会话记忆够不够得着」看进来（那件事 ``/health``
探不到）。把它塞进 ``/health``，运维与云监控拿到的就是一个失真的信号，所以这里是新增而不是改造。

判据**两样一起看**（少任何一样，都会让一个「检索能用、会话记不住」的新版本被部署换上去）：

1. 业务库能执行一次最小查询；
2. checkpointer 连接池能取到连接——它是独立连接池，业务库探活探不到它。

**生成式模型配得对不对不在这里判**（2026-10-02 决定）：模型不是「能服务」的前提（检索、阅读、
流水线都不需要它），而且以后可能接自建模型、名字不一定在上游的清单里，所以启动时不再向上游
列模型、也不拿它当上线门禁。配置写错会等到第一次提问才暴露。

**响应体只给状态**，与 ``/health`` 的固定消息同一原则：不写哪一项没过、不放依赖名与错误原文。
真正的边界是端口与安全组，不是响应体里多写几个字。它也**不进对外接口定义**
（``include_in_schema=False``）：这是给编排用的内部端点，产品侧没有消费者，前端的生成类型因此
不需要重新生成。

**探针有自己的等待上限，而且比外部那个小**：编排文件里健康检查的单次超时是 3 秒，本模块两个
探针各自最多等 ``READINESS_PROBE_TIMEOUT_SECONDS``，所以本端点总在自己回答、不会被外面掐断——
被掐断的请求既不会留下「哪一项没过」的日志，也谈不上「如实回答」。

**它的结论在 Swarm 里是有后果的。** 容器健康检查不过，agent 会把容器杀掉重启（实测）——所以
「业务库或会话记忆连不上」的版本会被反复杀掉，而不是像在本机开发那样长期半好地在线。部署场景下这正是我们要
的（旧任务不被替掉、更新自己回滚）；代价是「进程照起、检索照服务」只在被杀之前那几十秒内成立。

**生产核对（2026-10-01 切上 Swarm 后实测）**：本端点是编排文件里 API 服务的健康检查目标，
正常时返回 `200` 与 `{"status":"ready"}`。排查时要分清两个位置：容器的健康状态只在节点上的
`docker ps` 里看得到（服务定义里读不到）；而服务的更新顺序、镜像引用与停止宽限分别在
`Spec.UpdateConfig`、`TaskTemplate.ContainerSpec.Image`、`TaskTemplate.StopGracePeriod` 下，
用 `--format '{{json …}}'` 读（点号写法对部分字段会报 `map has no entry`）。
"""

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_lab.db.session import get_db_session


logger = logging.getLogger(__name__)
router = APIRouter(tags=["readiness"])

# 两个探针各自的等待上限，必须**明显小于**编排文件里健康检查的单次超时（3 秒）。
# 两者之间的关系不是巧合：健康检查每 5 秒问一次本端点，而本端点要先回答才能报出结论。
# 上限一旦超过外面那个，探针就会在回答之前被掐断——那种失败在应用日志里什么都不留，
# 排查时只看得到「健康检查失败」。改这里时把 ``backend/docker-stack.yml`` 里 API 的
# ``healthcheck.timeout`` 一起看。
READINESS_PROBE_TIMEOUT_SECONDS = 2.0


class ReadinessResponse(BaseModel):
    """就绪端点的响应体：只有状态，没有依赖名与错误原文。"""

    status: Literal["ready", "not_ready"] = Field(
        description="进程当前是否可以接班：检索与阅读可用、会话记忆可达。",
    )


@router.get(
    "/ready",
    include_in_schema=False,
    summary="内部就绪端点：新版本能不能接班",
    description=(
        "只读端点，不鉴权。两样一起看：业务库能执行最小查询、checkpointer 连接池能取到连接。全部通过返回 200 与 ``{\"status\": \"ready\"}``，"
        "否则返回 503 与 ``{\"status\": \"not_ready\"}``；两种响应体都不含依赖名与错误原文。"
    ),
)
async def readiness(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JSONResponse:
    """评估当前进程能不能接班，并把结论如实报成 200 / 503。

    刻意不抛异常：这个端点被编排按固定间隔轮询，「探活本身失败」与「判据不过」对调用方是同一个
    结论（不能接班），所以两种情况的响应形状必须一致，不能让其中一个变成 500 或断连接。

    Args:
        request: 当前 HTTP 请求，用于取出进程级 Agent Runtime。
        session: FastAPI 注入的业务库会话，用于执行最小查询。

    Returns:
        就绪时 200、未就绪时 503；响应体都只有 ``status`` 一个字段。

    Notes:
        只执行两次最小查询（业务库与 checkpointer 各一次 ``SELECT 1``），不写任何数据、
        不访问 Ollama/Qdrant/大模型。checkpointer 那个探针走的是独立连接池，与业务库那次
        不共用连接。
    """

    ready = await evaluate_readiness(request, session=session)
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ReadinessResponse(status="ready" if ready else "not_ready").model_dump(),
    )


async def evaluate_readiness(request: Request, *, session: AsyncSession) -> bool:
    """两样一起看，任何一样不过就是未就绪。

    Args:
        request: 当前 HTTP 请求，用于取出进程级 Agent Runtime。
        session: 业务库会话。

    Returns:
        两样都通过时为 ``True``。

    Notes:
        探针失败只记异常类型，不把异常文本带进响应体或日志正文——连接串里有凭据。
    """

    runtime = getattr(request.app.state, "agent_runtime", None)
    if runtime is None:
        # 未就绪：Agent 装配失败（进程照起、检索照服务，但提问这条链路做不了），或应用根本没
        # 走过生命周期（测试里直接 import 出来的 app）。
        return False
    if not await _business_database_reachable(session):
        return False
    return await _checkpointer_pool_reachable(runtime)


async def _business_database_reachable(session: AsyncSession) -> bool:
    """业务库能不能执行一次最小查询。"""

    try:
        # 应用级总超时可以覆盖 DNS 返回多个地址、驱动逐个尝试所产生的累计等待。
        async with asyncio.timeout(READINESS_PROBE_TIMEOUT_SECONDS):
            await session.execute(text("SELECT 1"))
    except (TimeoutError, SQLAlchemyError) as exc:
        logger.error("就绪检查：业务库探活失败 error_type=%s", type(exc).__name__)
        return False
    return True


async def _checkpointer_pool_reachable(runtime: object) -> bool:
    """checkpointer 连接池能不能取到连接。

    ``pool`` 为 ``None`` 时不作为判据：那表示注入了外部 checkpointer（离线测试用
    ``InMemorySaver``），本进程没有自己的池可探，把它算成「不过」会让那种装配永远不就绪。
    生产的 ``AgentRuntime.build`` 一定自建池，所以这条路不会掩盖真实故障。
    """

    pool = getattr(runtime, "pool", None)
    if pool is None:
        return True
    try:
        async with asyncio.timeout(READINESS_PROBE_TIMEOUT_SECONDS):
            async with pool.connection() as connection:
                # 取到连接还不够：psycopg 的池会把坏连接交出来（它只在归还时巡检），
                # 所以真发一条最小 SQL，才算「够得着」。
                await connection.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - 池超时、池已关闭、连接失败分属不同层级，都算未就绪
        logger.error("就绪检查：checkpointer 连接池取不到连接 error_type=%s", type(exc).__name__)
        return False
    return True


__all__ = [
    "ReadinessResponse",
    "evaluate_readiness",
    "router",
]
