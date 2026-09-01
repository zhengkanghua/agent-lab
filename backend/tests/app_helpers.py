"""离线 HTTP 测试的应用工厂：把 ``create_app`` 的三个真实工厂一次性换成不做 I/O 的替身。

为什么需要这个模块：``create_app`` 的每个工厂参数都有**生产默认值**，测试漏掉哪个，
lifespan 就会拿真实的那个去连真实服务。这不是理论风险——``agent_runtime_factory``
就曾经在 5 个测试文件里被集体漏掉，导致每次进 lifespan 都要等满 psycopg 连接池的
30 秒超时，而且 lifespan 里那个 ``except Exception`` 会把失败咽掉、测试照常通过，
所以整整一段时间没人发现测试根本没离线。

因此本模块的默认值是「安全」而不是「真实」：漏写参数最多让替身生效，不会退回去连真实
服务。要测真实装配的用例，显式传自己的工厂覆盖即可（``test_agent_chat_api.py`` 就是
这么做的）。

本模块不访问网络、不连 PostgreSQL、不碰 Qdrant，也不读 ``.env``。
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver

from agent_lab.agent.errors import AgentThreadNotFoundError
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.config.llm import LlmProvider, LlmSettings
from agent_lab.services.agent_thread_service import derive_thread_title
from tests.auth_helpers import (
    SUPERUSER_ID,
    allow_reader,
    allow_superuser,
    skip_environment_admin_sync,
)


# 指向本机回环、模型名写死的假配置。provider 用 OLLAMA 是因为它不要求 API Key——
# openai_compatible 在 key 为空时会抛 ``LlmConfigurationError``，而这些测试注入了假模型，
# 根本不会发出请求，没必要为此编一个假密钥。
OFFLINE_LLM_SETTINGS = LlmSettings(
    provider=LlmProvider.OLLAMA,
    base_url="http://127.0.0.1:11434",
    model="offline-test-model",
    fallback_model="offline-test-fallback",
)


class OfflineAgentRuntime:
    """只满足 lifespan 的 ``open``/``close`` 契约的 Agent Runtime 替身。

    刻意不带 ``graph``：本替身给的是「不测 Agent 的那些文件」用的，它们验证的是 401/404/422
    契约和脱敏响应，与 Agent 无关。没有 ``graph`` 意味着一旦有人在这类文件里请求
    ``/agent/chat``，会明确炸在缺属性上，而不是拿到一个「看起来能用其实什么都没装」的假
    Agent 给出可疑的通过结果。真要测 ``/agent/*``，注入真实 ``AgentRuntime.build``
    加 ``InMemorySaver``，见 ``test_agent_chat_api.py``。

    Attributes:
        opened: 是否被 lifespan 打开过，供需要断言启动顺序的用例使用。
        closed: 是否被 lifespan 关闭过。
    """

    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    async def open(self) -> None:
        """记录已打开，不建任何连接池。"""

        self.opened = True

    async def close(self) -> None:
        """记录已关闭，不执行外部 I/O。"""

        self.closed = True


def offline_agent_runtime_factory(_service: Any) -> OfflineAgentRuntime:
    """忽略检索 Service，返回不做 I/O 的 Agent Runtime 替身。

    Args:
        _service: lifespan 传入的检索 Service；替身不需要它，留参数只为匹配工厂签名。

    Returns:
        全新的 ``OfflineAgentRuntime``。
    """

    return OfflineAgentRuntime()


class InMemoryAgentThreadService:
    """在内存字典里实现会话归属，语义与 ``AgentThreadService`` 对齐但不碰数据库。

    为什么需要它：``get_agent_thread_service`` 的真实实现持有进程级 session 工厂，绑的是 ``.env``
    里那个真实 ``DATABASE_URL``。任何请求 ``/agent/*`` 的测试只要不覆盖这个依赖，就会真的去连
    PostgreSQL——Windows 上直接 ``InterfaceError``，Linux 上等满连接超时。所以
    ``create_offline_app`` 默认把它换掉，漏写覆盖的后果是「用了替身」而不是「连了真库」。

    它刻意复用真实实现的 ``derive_thread_title``，这样标题截断规则只有一处；归属判断则是这里
    自己写的字典查找——真实实现那份是 SQL 的 ``WHERE user_id``，无法在没有数据库的情况下执行。
    这就是本替身的覆盖边界：它能证明「路由把归属判断交给了 Service」，不能证明那条 SQL 写对了。
    后者由 ``tests/test_agent_thread_service.py``（语句级）和环境变量门控的真库集成测试负责。

    Attributes:
        threads: ``thread_id`` 到 ``(user_id, title, created_at, last_active_at)`` 的映射。
        deleted: 被 ``delete_thread_record`` 删掉的 id，按调用顺序。
    """

    def __init__(self) -> None:
        self.threads: dict[UUID, SimpleNamespace] = {}
        self.deleted: list[UUID] = []

    async def ensure_thread(
        self,
        *,
        user_id: UUID,
        thread_id: UUID | None,
        first_message: str,
    ) -> UUID:
        """新建或续活一个会话，归属不符时抛 ``AgentThreadNotFoundError``。"""

        now = datetime.now(UTC)
        if thread_id is None:
            created = uuid4()
            self.threads[created] = SimpleNamespace(
                thread_id=created,
                user_id=user_id,
                title=derive_thread_title(first_message),
                created_at=now,
                last_active_at=now,
            )
            return created

        record = self.threads.get(thread_id)
        if record is None or record.user_id != user_id:
            raise AgentThreadNotFoundError
        record.last_active_at = now
        return thread_id

    async def list_threads(
        self,
        *,
        user_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[SimpleNamespace], int]:
        """按最近活跃倒序分页返回该账号的会话。"""

        owned = [
            record for record in self.threads.values() if record.user_id == user_id
        ]
        owned.sort(key=lambda record: (record.last_active_at, record.thread_id), reverse=True)
        return owned[offset : offset + limit], len(owned)

    async def get_owned_thread(self, *, user_id: UUID, thread_id: UUID) -> SimpleNamespace:
        """读取一个会话并确认归属。"""

        record = self.threads.get(thread_id)
        if record is None or record.user_id != user_id:
            raise AgentThreadNotFoundError
        return record

    async def delete_thread_record(self, *, user_id: UUID, thread_id: UUID) -> None:
        """删除归属记录，不存在或不属于该账号时抛异常。"""

        record = self.threads.get(thread_id)
        if record is None or record.user_id != user_id:
            raise AgentThreadNotFoundError
        del self.threads[thread_id]
        self.deleted.append(thread_id)

    async def list_known_thread_ids(self) -> set[UUID]:
        """返回全部账号的会话 id。"""

        return set(self.threads)


def create_offline_app(**overrides: Any) -> FastAPI:
    """创建三个工厂都默认为离线替身的应用，并集中收拢 ``type: ignore``。

    Args:
        **overrides: 直接透传给 ``create_app`` 的参数，用来覆盖任一默认替身。常见的是
            ``runtime_factory``（注入本文件自己的 fake 检索 Runtime）；想测真实 Agent
            装配就传 ``agent_runtime_factory``。

    Returns:
        已挂载全部路由的应用；lifespan 不访问 PostgreSQL、Ollama、Qdrant 或大模型。
        ``app.state.offline_threads`` 上挂着那个内存会话 Service，需要预置或断言会话数据的
        用例直接取它，不必自己再覆盖一遍依赖。

    Notes:
        ``pipeline_runtime_factory`` 不在这里给默认值：它在 lifespan 里不会被调用，只在
        ``POST /pipeline/run-once`` 请求路径构造，给了默认值反而会让人以为启动时也用它。

        ``get_agent_thread_service`` 用的是 ``dependency_overrides`` 而不是 ``create_app`` 参数：
        它是请求级依赖，不是启动时装配的组件，``create_app`` 的签名里没有它的位置。
    """

    from agent_lab.api.dependencies import get_agent_thread_service
    from agent_lab.main import create_app

    # 1、先铺离线默认值，再让调用方的 overrides 覆盖，保证「漏写=安全」而不是「漏写=连真库」。
    defaults: dict[str, Any] = {
        "agent_runtime_factory": offline_agent_runtime_factory,
        "environment_admin_sync": skip_environment_admin_sync,
    }
    app = create_app(**{**defaults, **overrides})  # type: ignore[arg-type]

    # 2、会话归属 Service 换成内存替身。少了这一步，任何请求 /agent/* 的测试都会真去连
    #    PostgreSQL（真实依赖持有绑定 .env 的进程级 session 工厂）。挂到 state 上是为了让用例
    #    既能预置数据、又不用重复写一遍 override。
    offline_threads = InMemoryAgentThreadService()
    app.state.offline_threads = offline_threads
    app.dependency_overrides[get_agent_thread_service] = lambda: offline_threads
    return app


class FakeSearchService:
    """记录检索请求并返回空结果，不执行任何网络 I/O。

    两个用处：证明 Agent 拿到的是同一个 Service 实例，以及在 Agent 装配失败时充当
    「只读链路还活着」的探针。
    """

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def search(self, request: Any) -> list[Any]:
        """记录请求并返回空结果。"""

        self.calls.append(request)
        return []


class FakeSearchRuntime:
    """只暴露 API 与 Agent 装配所需的 ``service`` 字段。"""

    def __init__(self) -> None:
        self.service = FakeSearchService()
        self.closed = False

    async def close(self) -> None:
        """记录关闭，不访问外部资源。"""

        self.closed = True


def create_agent_app(
    model: Any,
    *,
    superuser: bool = True,
    agent_build_error: Exception | None = None,
) -> tuple[FastAPI, FakeSearchRuntime]:
    """创建装着**真实** ``AgentRuntime`` 的离线应用。

    与 ``create_offline_app`` 的默认替身不同，这里注入的是真实 ``AgentRuntime.build``（配假模型和
    ``InMemorySaver``）。也就是说「图怎么编译、事件怎么排序、历史怎么存」仍由生产代码决定，只有模型
    和存储被换掉。需要请求 ``/agent/*`` 的测试都该用这个。

    Args:
        model: 注入的假聊天模型。
        superuser: 为 ``False`` 时只覆盖普通用户依赖，保留真实超级用户检查，用来测权限拒绝。
        agent_build_error: 非空时让 Agent 工厂抛这个异常，模拟装配失败。

    Returns:
        ``(应用, 假检索 Runtime)``。检索 Runtime 用来断言关闭顺序，或在装配失败时当只读探针。

    Notes:
        不连 PostgreSQL、Qdrant，不访问网络，也不调真实大模型。会话归属仍走
        ``create_offline_app`` 装的内存替身，取 ``app.state.offline_threads`` 即可预置数据。
    """

    search_runtime = FakeSearchRuntime()

    def agent_factory(service: Any) -> AgentRuntime:
        if agent_build_error is not None:
            raise agent_build_error
        # 断言 Agent 复用的是同一个检索 Service 实例，而不是自己另建一个——另建的那个不会被
        # lifespan 关闭，也不会出现在 ``search_runtime.service.calls`` 里。
        assert service is search_runtime.service
        return AgentRuntime.build(
            llm_settings=OFFLINE_LLM_SETTINGS,
            search_service=service,
            session_factory=None,  # type: ignore[arg-type]
            database_url="postgresql+psycopg://unused/unused",
            checkpointer=InMemorySaver(),
            model=model,
            # 退避是真 sleep。这些用例断言的是 HTTP 契约，不需要等。
            retry_initial_delay=0.0,
        )

    grant = allow_superuser if superuser else allow_reader
    app = grant(
        create_offline_app(
            runtime_factory=lambda: search_runtime,
            agent_runtime_factory=agent_factory,
        )
    )
    return app, search_runtime


async def send(
    app: FastAPI,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    """在显式 lifespan 内发送一个非流式 ASGI 请求。

    Args:
        app: 待测应用。
        method: HTTP 方法。
        path: 请求路径。
        **kwargs: 透传给 ``httpx.AsyncClient.request``。

    Returns:
        完整读取过的响应。
    """

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


def seed_owned_thread(
    app: FastAPI,
    thread_id: UUID,
    *,
    user_id: UUID = SUPERUSER_ID,
    title: str = "预置会话",
    last_active_at: datetime | None = None,
) -> SimpleNamespace:
    """在内存会话表里预置一行归属记录。

    Args:
        app: 由 ``create_offline_app`` 造出的应用。
        thread_id: 要预置的会话 id。
        user_id: 归属账号，默认与 ``allow_superuser`` 覆盖出的当前账号一致。传别的值即可构造
            「这是别人的会话」。
        title: 会话标题。
        last_active_at: 最后活跃时间；省略时用当前时间。想构造确定的排序就显式传。

    Returns:
        刚写进去的那行记录，便于随后修改或断言。
    """

    now = datetime.now(UTC)
    record = SimpleNamespace(
        thread_id=thread_id,
        user_id=user_id,
        title=title,
        created_at=now,
        last_active_at=last_active_at or now,
    )
    app.state.offline_threads.threads[thread_id] = record
    return record


__all__ = [
    "OFFLINE_LLM_SETTINGS",
    "FakeSearchRuntime",
    "FakeSearchService",
    "InMemoryAgentThreadService",
    "OfflineAgentRuntime",
    "create_agent_app",
    "create_offline_app",
    "offline_agent_runtime_factory",
    "seed_owned_thread",
    "send",
]
