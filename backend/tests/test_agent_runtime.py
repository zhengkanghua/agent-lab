"""``AgentRuntime.build`` 自建 checkpointer 连接池时的参数守护。

其余 Agent 测试都注入 ``InMemorySaver``（不需要建表、不需要数据库），因此**建池那条分支
一直没有测试覆盖**——本文件专门补它。

为什么这条分支值得单独守：池的三个参数各自对应一个真实发生过或必然发生的故障，而它们的
错法都不会在启动时报错，只在运行一段时间后才显形：

1. 少了 ``check``：空闲期间被服务端掐掉的连接会被原样交给 checkpointer，表现是「检索一切
   正常、只有提问失败」，日志里是 ``discarding closed connection`` 加一条
   ``OperationalError``。业务侧 Engine 有 ``pool_pre_ping`` 兜着，这个池没有。
2. 少了显式 ``min_size``：psycopg_pool 的默认值是 4，配置却允许把 ``max_size`` 填到 1，
   相撞时构造直接 ``ValueError``。
3. ``open`` 不为 False：``build`` 的契约是不执行 I/O，构造时建连会在事件循环还没起来时炸。

本文件不连接 PostgreSQL：``open=False`` 意味着构造连接池不建连，断言只读池对象的属性。
"""

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from psycopg_pool import AsyncConnectionPool

from agent_lab.agent.runtime import AgentRuntime
from agent_lab.api.error_contract import (
    AGENT_CHAT_ERROR_RULES,
    resolve_error_contract,
)
from agent_lab.config.llm import LlmProvider, LlmSettings

from tests.agent_helpers import ScriptedChatModel, run


# 指向一个不存在的地址即可：全文件都不建连。用 postgresql+psycopg:// 前缀是有意的，
# 它同时顺带证明 build 会把 SQLAlchemy 风格的 URL 转成 psycopg 认的连接串。
UNUSED_DATABASE_URL = "postgresql+psycopg://user:pass@127.0.0.1:5432/unused"


def offline_llm_settings(*, pool_size: int = 4) -> LlmSettings:
    """构造一份指向本机 Ollama 的配置，不会被真正调用。"""

    return LlmSettings(
        provider=LlmProvider.OLLAMA,
        base_url="http://127.0.0.1:11434",
        model="offline-test-model",
        fallback_model="offline-test-fallback",
        checkpoint_pool_size=pool_size,
    )


def build_runtime(*, pool_size: int = 4) -> AgentRuntime:
    """装配一个走「自建连接池」分支的 Runtime。

    Args:
        pool_size: 传给 ``LLM_CHECKPOINT_POOL_SIZE`` 的值，即池的 ``max_size``。

    Returns:
        已装配但未 ``open`` 的 Runtime，其 ``pool`` 非空。

    Notes:
        不执行任何 I/O：模型是假的，连接池 ``open=False``，工具虽然被构造但不会被调用。

        必须在事件循环里调 ``build``：``AsyncPostgresSaver.__init__`` 会调
        ``asyncio.get_running_loop()``。这不是本文件的特殊要求，生产路径也一样——
        ``build`` 是在 FastAPI lifespan 里被 await 的，天然有循环。所以这里用 ``run``
        包一层，而不是把它当同步函数直接调。
    """

    async def build() -> AgentRuntime:
        return AgentRuntime.build(
            llm_settings=offline_llm_settings(pool_size=pool_size),
            search_service=None,  # type: ignore[arg-type]
            session_factory=None,  # type: ignore[arg-type]
            database_url=UNUSED_DATABASE_URL,
            model=ScriptedChatModel(responses=[]),
        )

    return run(build())


def test_the_pool_checks_a_connection_before_handing_it_out() -> None:
    """池必须带取连接前探活，等价于业务侧 Engine 的 ``pool_pre_ping``。

    这是本文件最重要的一条。``check`` 默认是 ``None``，而 psycopg_pool 的
    ``_check_connection`` 第一句就是「没有 check 就直接返回」——于是死连接被原样交出去，
    第一条 SQL 抛 ``psycopg.OperationalError``。

    更麻烦的是坏连接不会自己消失：``max_lifetime`` 和 ``max_idle`` 只在连接**归还**时检查，
    不巡检躺在池里的空闲连接。所以池里有几条死连接，就要害几次提问失败才换干净。
    """

    runtime = build_runtime()

    assert runtime.pool is not None
    assert runtime.pool._check is AsyncConnectionPool.check_connection


def test_the_pool_accepts_the_smallest_configurable_size() -> None:
    """``checkpoint_pool_size`` 允许的最小值必须真的能建出池来。

    配置声明的范围是 1..32，而 psycopg_pool 的 ``min_size`` 默认 4，
    ``max_size < min_size`` 会在构造时直接 ``ValueError``。不显式给 ``min_size`` 的话，
    配置文档说能填 1、填了服务起不来，而且错误发生在 lifespan 里，不在校验阶段。
    """

    runtime = build_runtime(pool_size=1)

    assert runtime.pool is not None
    assert runtime.pool.max_size == 1
    assert runtime.pool.min_size <= 1


def test_the_pool_can_still_shrink_at_the_default_size() -> None:
    """``min_size`` 必须严格小于 ``max_size``，否则池永远不收缩。

    psycopg_pool 的 ``_shrink_pool`` 要求「当前连接数 > min_size」才回收一条。两者相等时
    这个条件永远不成立，空闲连接会一直留在池里——正是上面那条探活断言要防的那批连接的来源。
    """

    runtime = build_runtime(pool_size=4)

    assert runtime.pool is not None
    assert runtime.pool.min_size < runtime.pool.max_size


def test_building_the_pool_does_not_connect() -> None:
    """``build`` 不能建连：它的契约是纯构造，且此时事件循环可能还没起来。

    ``open`` 默认为 True，会在构造时就尝试连接数据库。本用例用一个连不通的地址反证：
    如果 ``open`` 漏写成默认值，``build_runtime`` 自己就会抛异常或挂住。
    """

    runtime = build_runtime()

    assert runtime.pool is not None
    assert runtime.pool.closed is True


def test_an_injected_checkpointer_does_not_get_a_pool() -> None:
    """注入了 checkpointer 就不该再建池，否则离线测试会凭空多出一个数据库连接池。"""

    runtime = AgentRuntime.build(
        llm_settings=offline_llm_settings(),
        search_service=None,  # type: ignore[arg-type]
        session_factory=None,  # type: ignore[arg-type]
        database_url=UNUSED_DATABASE_URL,
        checkpointer=InMemorySaver(),
        model=ScriptedChatModel(responses=[]),
    )

    assert runtime.pool is None


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_retryable"),
    [
        (psycopg.OperationalError("connection lost"), "agent_checkpointer_connection_lost", True),
        # 表不存在是 ProgrammingError。它必须留在兜底里：那是漏跑 init-checkpointer 的信号，
        # 重试永远好不了，说成「连接中断、稍后重试」会让运维一直重试一个死掉的东西。
        (psycopg.ProgrammingError('relation "checkpoints" does not exist'), "agent_internal_error", False),
    ],
)
def test_a_dead_checkpointer_connection_is_told_apart_from_a_missing_table(
    error: Exception,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    """运行中途断连要报可重试的专用码，表缺失仍走不可重试的兜底。

    两者都是 ``psycopg.Error`` 的子类，所以规则只能挂 ``OperationalError``；挂
    ``psycopg.Error`` 会把表缺失一起吞进「稍后重试」。
    """

    rule = resolve_error_contract(error, AGENT_CHAT_ERROR_RULES)

    assert rule.code == expected_code
    assert rule.retryable is expected_retryable


def test_the_dead_connection_rule_is_not_shadowed_by_a_generic_database_rule() -> None:
    """原生 psycopg 的异常不能被 SQLAlchemy 或 ConnectionError 那几条规则捞走。

    这条钉住的是根因诊断里最容易想错的一步：``psycopg.OperationalError`` 既不是
    ``sqlalchemy.exc.SQLAlchemyError`` 也不是内置 ``ConnectionError`` 的子类，所以在加专用
    规则之前，它必然落到 ``Exception`` 兜底。谁要是哪天把专用规则删掉、指望别的规则捞住它，
    这条会失败。
    """

    assert not issubclass(psycopg.OperationalError, ConnectionError)
    rule = resolve_error_contract(
        psycopg.OperationalError("connection lost"),
        AGENT_CHAT_ERROR_RULES,
    )
    assert rule.code != "agent_internal_error"
