"""``GET /ready`` 的完全离线测试：三样判据、响应形状、以及「不进对外接口定义」。

不连接 PostgreSQL、Qdrant，也不访问任何大模型：业务库那个探针靠替换 ``get_db_session`` 的假
会话，checkpointer 那个靠带假池的 Agent Runtime 替身，配置校验靠注入自己的协程。这样三条判据
各自都能被单独按下去，而每条断言的观察点都是 HTTP 状态码与响应体。
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from psycopg_pool import PoolClosed
from sqlalchemy.exc import OperationalError

from agent_lab.agent.limits import MODEL_CATALOG_TIMEOUT_SECONDS
from agent_lab.agent.model_catalog import LlmModelNotListedError
from agent_lab.db.session import get_db_session
from tests.app_helpers import create_offline_app


def run(coroutine: Any) -> Any:
    """执行异步 HTTP 测试，不引入额外 pytest 异步插件。"""

    return asyncio.run(coroutine)


class _FakeSession:
    """业务库会话替身：配置成「查得动」或「查不动」。"""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def execute(self, _statement: Any) -> None:
        if self._error is not None:
            raise self._error


class _ReadyConnection:
    """能执行最小 SQL 的连接替身。"""

    async def execute(self, _statement: Any) -> None:
        return None


class _ReadyPool:
    """能取到连接的 checkpointer 池替身。"""

    @asynccontextmanager
    async def connection(self):
        yield _ReadyConnection()


class _ClosedPool:
    """取不到连接的池替身。

    抛 ``PoolClosed`` 而不是随便一个异常：那是「池没打开/已关闭」在生产里真正会抛的类型。
    """

    @asynccontextmanager
    async def connection(self):
        raise PoolClosed("连接池已关闭")
        yield  # pragma: no cover - 上面那行 raise 已经结束生成器


class _RuntimeWithPool:
    """只满足生命周期与就绪探针所需的 Agent Runtime 替身。"""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _failing_runtime_factory(_service: Any, _collector: Any) -> Any:
    """模拟 Agent 装配失败（例如缺模型凭据）。"""

    raise RuntimeError("缺少模型凭据")


def build_app(
    *,
    pool: Any = None,
    session_error: Exception | None = None,
    check: Any = None,
    runtime_fails: bool = False,
) -> FastAPI:
    """造一个三条判据都能单独控制的应用。

    Args:
        pool: checkpointer 池替身；省略时用一个能取到连接的。
        session_error: 非空时让业务库探针抛这个异常。
        check: 启动后那次配置校验的实现；省略时用离线默认（不发请求、立即成功）。
        runtime_fails: 为 ``True`` 时让 Agent Runtime 装配失败（就绪判据里的第一项）。
    """

    overrides: dict[str, Any] = {
        "agent_runtime_factory": (
            _failing_runtime_factory
            if runtime_fails
            else (lambda _service, _collector: _RuntimeWithPool(pool or _ReadyPool()))
        ),
    }
    if check is not None:
        overrides["model_catalog_check"] = check
    app = create_offline_app(**overrides)
    app.dependency_overrides[get_db_session] = lambda: _FakeSession(session_error)
    return app


async def _get_ready(app: FastAPI, *, wait_for_verdict: bool = True) -> httpx.Response:
    """在显式 lifespan 内取一次 ``/ready``。

    默认先等启动后那次配置校验落下结论：不等的话，断言就变成在赌任务调度，而「等」的那条路
    （``wait_for_verdict=False``）正好是「校验还没出结论」这一判据本身。
    """

    async with app.router.lifespan_context(app):
        if wait_for_verdict:
            await app.state.model_catalog_verdict.wait()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/ready")


def test_ready_when_all_three_hold() -> None:
    """三样都过 → 200，响应体只有状态。"""

    response = run(_get_ready(build_app()))

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_not_ready_when_the_business_database_fails() -> None:
    """业务库探活失败 → 503；响应体里不许出现依赖名或错误原文。"""

    error = OperationalError("SELECT 1", {}, Exception("connection refused to postgresql"))
    response = run(_get_ready(build_app(session_error=error)))

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "postgresql" not in response.text
    assert "OperationalError" not in response.text


def test_not_ready_when_the_checkpointer_pool_has_no_connection() -> None:
    """checkpointer 连接池取不到连接 → 503。

    这一条必须独立于业务库那条：checkpointer 走的是它自己的 psycopg 池，业务库探活探不到它，
    而「会话记忆够不着」的新版本恰恰是最需要被拦下来的那一种。
    """

    response = run(_get_ready(build_app(pool=_ClosedPool())))

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "PoolClosed" not in response.text


def test_not_ready_when_the_agent_runtime_failed_to_assemble() -> None:
    """Agent 装配失败 → 未就绪。

    「检索能用、提问用不了」是刻意允许的进程状态（进程照起、/agent/* 返 503），但它必须如实
    报成未就绪，否则部署会把一个聊天用不了的版本换上去。
    """

    response = run(_get_ready(build_app(runtime_fails=True)))

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_not_ready_while_the_startup_check_has_no_verdict_yet() -> None:
    """后台那次校验还没出结论就算未就绪（上游一直不回答时，这一项会一直挂在这里）。"""

    async def hanging_check() -> None:
        await asyncio.Event().wait()

    response = run(
        _get_ready(build_app(check=hanging_check), wait_for_verdict=False)
    )

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_not_ready_after_the_check_found_a_missing_model() -> None:
    """校验已经给出「模型名不在上游列表里」→ 未就绪（结论之前与之后都算未就绪）。"""

    async def missing_model() -> None:
        raise LlmModelNotListedError("上游模型列表中没有以下模型：auto。")

    response = run(_get_ready(build_app(check=missing_model)))

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "auto" not in response.text


def test_upstream_not_answering_still_reports_ready() -> None:
    """上游「不回答」不算未就绪：校验的判据是「有没有证据说配置错了」，不是上游健康。

    生产实现里「拉不到模型列表」会静默放过（见 ``agent/model_catalog.py``），所以这条用
    「校验正常返回」表示它——把上游可用性做成部署门禁，等于让别人的故障挡住自己的发布。
    """

    async def unreachable_upstream() -> None:
        return None

    response = run(_get_ready(build_app(check=unreachable_upstream)))

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_endpoint_is_not_in_the_openapi_schema() -> None:
    """就绪端点不进对外接口定义；既有的 /health 照旧留着。

    它是给编排用的内部端点，产品侧没有消费者。进了 schema 就等于对前端做出承诺，而前端的类型
    是从 schema 生成的——那会把一个内部实现细节变成需要维护的契约。
    """

    paths = build_app().openapi()["paths"]

    assert "/ready" not in paths
    assert "/health" in paths


def test_a_slow_startup_check_does_not_delay_serving() -> None:
    """启动不等那次上游自检：把它拖住，进程照样立刻开始接受请求。

    「开始服务」的可观察含义就是「生命周期返回了、端口上的请求有人答」，所以这里量的是「进入
    lifespan + 一次请求」的耗时，并与那次自检自己的超时上限比：要证明的是「不等它」，而不是
    「等得少一点」。
    """

    async def hanging_check() -> None:
        await asyncio.Event().wait()

    app = build_app(check=hanging_check)

    async def scenario() -> tuple[float, httpx.Response]:
        started = time.perf_counter()
        response = await _get_ready(app, wait_for_verdict=False)
        return time.perf_counter() - started, response

    elapsed, response = run(scenario())

    assert elapsed < MODEL_CATALOG_TIMEOUT_SECONDS / 10
    # 校验还没结论，所以此刻是未就绪——但进程已经在答复请求了。
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
