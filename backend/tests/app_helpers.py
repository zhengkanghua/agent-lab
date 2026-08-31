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

from typing import Any

from fastapi import FastAPI

from tests.auth_helpers import skip_environment_admin_sync


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


def create_offline_app(**overrides: Any) -> FastAPI:
    """创建三个工厂都默认为离线替身的应用，并集中收拢 ``type: ignore``。

    Args:
        **overrides: 直接透传给 ``create_app`` 的参数，用来覆盖任一默认替身。常见的是
            ``runtime_factory``（注入本文件自己的 fake 检索 Runtime）；想测真实 Agent
            装配就传 ``agent_runtime_factory``。

    Returns:
        已挂载全部路由的应用；lifespan 不访问 PostgreSQL、Ollama、Qdrant 或大模型。

    Notes:
        ``pipeline_runtime_factory`` 不在这里给默认值：它在 lifespan 里不会被调用，只在
        ``POST /pipeline/run-once`` 请求路径构造，给了默认值反而会让人以为启动时也用它。
    """

    from agent_lab.main import create_app

    # 1、先铺离线默认值，再让调用方的 overrides 覆盖，保证「漏写=安全」而不是「漏写=连真库」。
    defaults: dict[str, Any] = {
        "agent_runtime_factory": offline_agent_runtime_factory,
        "environment_admin_sync": skip_environment_admin_sync,
    }
    return create_app(**{**defaults, **overrides})  # type: ignore[arg-type]


__all__ = [
    "OfflineAgentRuntime",
    "create_offline_app",
    "offline_agent_runtime_factory",
]
