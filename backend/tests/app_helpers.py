"""离线 HTTP 测试的应用工厂：把 ``create_app`` 的四个真实工厂一次性换成不做 I/O 的替身。

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

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver

from agent_lab.agent.errors import AgentRunInProgressError, AgentThreadNotFoundError
from agent_lab.agent.limits import RUN_ZOMBIE_THRESHOLD_SECONDS
from agent_lab.agent.runs import AgentRunRegistry
from agent_lab.agent.thread_messages import RunMessageRows
from agent_lab.agent.runtime import AgentRuntime
from agent_lab.config.llm import LlmSettings
from agent_lab.models.agent_thread_message import AgentThreadMessageRecord
from agent_lab.services.agent_thread_service import DrainedRunClaim, derive_thread_title
from agent_lab.usage.collector import NoopUsageCollector
from agent_lab.knowledge.scope import KnowledgeBaseSelection, ResolvedKnowledgeBaseScope
from agent_lab.knowledge.domain import KnowledgeBaseNotFoundError
from tests.agent_scope_helpers import NEWS_SCOPE
from tests.agent_helpers import OFFLINE_LANGSMITH_SETTINGS
from tests.auth_helpers import (
    SUPERUSER_ID,
    allow_reader,
    allow_superuser,
    skip_environment_admin_sync,
)


# 进程级配置里只剩温度、超时、User-Agent 与连接池大小：模型本身来自模型目录，而本文件
# 一律注入假模型，根本不会构造真实客户端，也不会发出请求。
OFFLINE_LLM_SETTINGS = LlmSettings()


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


def offline_agent_runtime_factory(_service: Any, _usage_collector: Any) -> OfflineAgentRuntime:
    """忽略检索 Service 与用量采集器，返回不做 I/O 的 Agent Runtime 替身。

    Args:
        _service: lifespan 传入的检索 Service；替身不需要它，留参数只为匹配工厂签名。
        _usage_collector: lifespan 传入的用量采集器；替身同样不需要它。

    Returns:
        全新的 ``OfflineAgentRuntime``。
    """

    return OfflineAgentRuntime()


# 离线测试里的运行协调周期。生产值是秒级（见 agent/limits.py），测试把它们压到毫秒级，
# 否则「停止真的停住」「运行结束后立刻恢复可提交」这类用例每一条都要白等一秒以上。
OFFLINE_RUN_POLL_INTERVAL_SECONDS = 0.005
OFFLINE_RUN_LIVENESS_INTERVAL_SECONDS = 0.05

# 离线测试的排空上限。生产是 120 秒（见 agent/limits.py）；这里取一个远小于它、又足够让
# 普通假模型走完当前 superstep 的值，免得退出 lifespan 的用例白等两分钟。要验证「到点按放弃
# 收尾」的用例显式传一个更小的值。
OFFLINE_DRAIN_TIMEOUT_SECONDS = 2.0

# 离线测试扫描「等接手」标记的节奏；压到毫秒级才能在毫秒级验证接手。
OFFLINE_HANDOVER_SCAN_INTERVAL_SECONDS = 0.005


def offline_agent_run_registry_factory(
    threads: "InMemoryAgentThreadService",
    runtime: Any = None,
    *,
    drain_timeout: float | None = None,
) -> AgentRunRegistry:
    """返回不做真实 I/O 的进程级运行注册表。

    注册表自己不碰数据库，读写会话状态都交给注入的 Service；离线缺省值要它用**那一份内存替身**，
    否则它会拿真实的 session 工厂去连库，而那种失败不会让测试报错，只会变成一次超时。

    Args:
        threads: ``create_offline_app`` 建好的内存会话 Service。
        runtime: lifespan 传进来的 Agent Runtime；替身（``OfflineAgentRuntime``）没有 ``graph``，
            所以这种文件里注册表拿不到图、不会去认领「等接手」标记——与生产的「图装配失败」同形。
        drain_timeout: 排空上限；``None`` 时用离线默认值（远小于生产，见
            ``OFFLINE_DRAIN_TIMEOUT_SECONDS``）。测试传小值验证「到点按放弃收尾」。

    Returns:
        全新的 ``AgentRunRegistry``，周期参数为毫秒级。
    """

    return AgentRunRegistry(
        threads=threads,
        graph=getattr(runtime, "graph", None),
        # 离线固定关掉追踪：不能让测试意外向 LangSmith 上报。
        langsmith_settings=OFFLINE_LANGSMITH_SETTINGS,
        event_poll_interval=OFFLINE_RUN_POLL_INTERVAL_SECONDS,
        liveness_interval=OFFLINE_RUN_LIVENESS_INTERVAL_SECONDS,
        state_poll_interval=OFFLINE_RUN_POLL_INTERVAL_SECONDS,
        drain_timeout=(
            OFFLINE_DRAIN_TIMEOUT_SECONDS if drain_timeout is None else drain_timeout
        ),
        handover_scan_interval=OFFLINE_HANDOVER_SCAN_INTERVAL_SECONDS,
    )


class OfflineUsageRuntime:
    """只满足 lifespan 的 ``collector`` / ``close`` 契约的用量库替身。

    刻意不带 engine 与 session：它给的是「不测用量链路的文件」用的，让那些用例不必设置
    ``LLMOPS_DATABASE_URL`` 就能起应用（不设覆盖时，lifespan 会去读真实配置并直接失败）。
    真要测用量链路的用例自己注入真实 ``UsageRuntime``。

    Attributes:
        collector: 空实现，记下的东西不落任何地方。
        session_factory: 可选的用量库会话工厂。不传就与真装配的"用量库连不上"同形（属性在、
            值缺），查询接口据此返回 503；要测查询链路时由用例传入真实（内存 SQLite）的工厂。
        closed: 是否被 lifespan 关过；用来断言释放顺序。
    """

    def __init__(self, session_factory: Any = None) -> None:
        self.collector = NoopUsageCollector()
        self.session_factory = session_factory
        self.closed = False

    async def close(self) -> None:
        """标记已释放，不碰任何外部资源。"""

        self.closed = True


def offline_usage_runtime_factory() -> OfflineUsageRuntime:
    """返回不做 I/O 的用量库替身。

    Returns:
        全新的 ``OfflineUsageRuntime``。
    """

    return OfflineUsageRuntime()


@dataclass(frozen=True)
class RecordedRunMessages:
    """一次「把某一轮写进会话历史表」调用的现场。

    内存替身只能给出「什么时候、带着哪些组调了写入方」；行怎么落、重复收尾怎么保幂等、顺序号
    怎么算都由真实 Service 在真实库上验证（``tests/test_agent_thread_messages.py``），替身不
    复制那套语义，免得两份实现漂移。

    Attributes:
        thread_id: 写哪个会话。
        run_id: 哪一次运行的收尾在写；Service 靠它认「本次运行那一组」。
        runs: 快照里能组出来的全部组（已投影成行），整份交给 Service 自己去挑。
    """

    thread_id: UUID
    run_id: UUID
    runs: tuple[RunMessageRows, ...]


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
        prompts: ``user_id`` 到该账号偏好提示词的映射；不在这里的账号视为没配过。
            真实实现去 ``user_preferences`` 表取，替身用这个字典模拟「取到 / 取不到」两支。
        messages: 会话历史表的替身：``thread_id`` 到那一会话的消息行，回放读它。
    """

    def __init__(self) -> None:
        self.threads: dict[UUID, SimpleNamespace] = {}
        self.deleted: list[UUID] = []
        self.prompts: dict[UUID, str] = {}
        # 每一次「写会话历史」都记一笔现场，用例靠它断言写入的时机与内容；行本身也放进
        # ``messages``（回放读它），真库上的写法与顺序号由 ``tests/test_agent_thread_messages.py`` 验。
        self.recorded_run_messages: list[RecordedRunMessages] = []
        # 会话历史表的替身：``thread_id`` 到那一会话的行，按顺序号排。
        self.messages: dict[UUID, list[AgentThreadMessageRecord]] = {}

    async def ensure_thread(
        self,
        *,
        user_id: UUID,
        thread_id: UUID | None,
        first_message: str,
        run_id: UUID,
        scope: KnowledgeBaseSelection | None = None,
        llm_model_id: UUID | None = None,
    ) -> tuple[UUID, str | None]:
        """新建或续活一个会话并占下这次运行的位，与真实实现的语义对齐。

        返回 ``(会话 id, 会话提示词)``。新建时从 ``prompts`` 取该账号的偏好当快照；续聊时
        沿用会话里存的那份，**不回读偏好**——与真实实现同义，这条差异是测试要守住的行为。

        占位规则也照真实实现那一份写：``active_run_id`` 为空、**或那次运行已超过失活阈值**
        （进程崩了，没有任何清理动作会执行）时才能占；占下时顺手清掉陈旧的停止请求。
        真实实现把这三件事放在同一条 ``UPDATE`` 的 ``WHERE`` 里（见
        ``services/agent_thread_service.ensure_thread``），这里只能逐句照抄它的语义。

        ``llm_model_id`` 与 ``scope`` 同一条规则：带了就写回会话行（改了就是记住），
        没带就一字不动。
        """

        now = datetime.now(UTC)
        if thread_id is None:
            created = uuid4()
            system_prompt = self.prompts.get(user_id)
            self.threads[created] = SimpleNamespace(
                thread_id=created,
                user_id=user_id,
                title=derive_thread_title(first_message),
                scope=(scope or KnowledgeBaseSelection(mode="all")).model_dump(mode="json"),
                llm_model_id=llm_model_id,
                system_prompt=system_prompt,
                created_at=now,
                last_active_at=now,
                active_run_id=run_id,
                stop_requested_at=None,
                drained_at=None,
            )
            return created, system_prompt

        record = self.threads.get(thread_id)
        if record is None or record.user_id != user_id:
            raise AgentThreadNotFoundError
        if not is_claimable(record, now):
            raise AgentRunInProgressError
        record.last_active_at = now
        record.active_run_id = run_id
        record.stop_requested_at = None
        if scope is not None:
            record.scope = scope.model_dump(mode="json")
        if llm_model_id is not None:
            record.llm_model_id = llm_model_id
        return thread_id, record.system_prompt

    async def finish_run(self, *, thread_id: UUID, run_id: UUID) -> None:
        """释放占位；只有仍然占着位的那个运行能释放。"""

        record = self.threads.get(thread_id)
        if record is not None and record.active_run_id == run_id:
            record.active_run_id = None
            record.stop_requested_at = None

    async def request_stop(
        self, *, thread_id: UUID, run_id: UUID, now: datetime | None = None
    ) -> None:
        """写停止请求；只有在途运行的 id 相等时才写（与真实实现同义）。"""

        record = self.threads.get(thread_id)
        if record is not None and record.active_run_id == run_id:
            record.stop_requested_at = now or datetime.now(UTC)

    async def read_run_states(
        self, *, thread_ids: list[UUID]
    ) -> dict[UUID, tuple[UUID | None, datetime | None]]:
        """批量返回在途运行 id 与停止请求时刻。"""

        return {
            thread_id: (self.threads[thread_id].active_run_id, self.threads[thread_id].stop_requested_at)
            for thread_id in thread_ids
            if thread_id in self.threads
        }

    async def touch_run(self, *, thread_id: UUID, run_id: UUID, now: datetime) -> int:
        """把在途运行的会话活跃时间推后；不再占位时返回 0。"""

        record = self.threads.get(thread_id)
        if record is None or record.active_run_id != run_id:
            return 0
        record.last_active_at = now
        return 1

    async def mark_drained(
        self, *, thread_id: UUID, run_id: UUID, now: datetime | None = None
    ) -> int:
        """写下「等接手」标记；只在仍然占着位的运行上写（与真实实现同义）。"""

        record = self.threads.get(thread_id)
        if record is None or record.active_run_id != run_id:
            return 0
        record.drained_at = now or datetime.now(UTC)
        return 1

    async def list_drained_thread_ids(self) -> list[UUID]:
        """列出所有带「等接手」标记的会话。"""

        return [
            record.thread_id
            for record in self.threads.values()
            if record.drained_at is not None
        ]

    async def claim_drained_run(self, *, thread_id: UUID) -> DrainedRunClaim | None:
        """抢一条「等接手」运行的所有权；置空标记并把接手所需的上下文一并返回。

        内存替身里「判标记非空 + 置空」之间没有 await 点，所以它天然原子——它只能证明「路由/
        扫描把抢所有权交给了 Service」，真库上那条条件写入的原子性由 ``test_agent_thread_service``
        的语句级断言负责。
        """

        record = self.threads.get(thread_id)
        if record is None or record.drained_at is None:
            return None
        record.drained_at = None
        if record.active_run_id is None:
            return None
        return DrainedRunClaim(
            thread_id=thread_id,
            run_id=record.active_run_id,
            user_id=record.user_id,
            system_prompt=record.system_prompt,
        )

    async def record_run_messages(
        self, *, thread_id: UUID, run_id: UUID, runs: tuple[RunMessageRows, ...]
    ) -> int:
        """记下一次写入调用的现场，并把行放进内存里的那张表。

        回放改读会话历史表之后，替身必须真的存下这些行，否则离线用例里的回放永远是空的。
        它只做到「写进去就能读回来」：顺序号接着会话里已有的最大值加一，已经写过的组跳过
        （写入侧会把快照里能组出来的组全交过来，不跳过就会把同一轮写两遍）。**真实实现那套
        「按（会话，运行）整组替换 + 沿用第一次的顺序号」不在这里复制**：它是库上的语义
        （唯一键、幂等、跨会话隔离），由 ``tests/test_agent_thread_messages.py`` 在真实
        Service 与真实库上验。

        Returns:
            这次调用带着的那些组里的行数。真实实现返回的是「实际写入的行数」，替身不回填
            一个假的数——消费方只在日志里用它。
        """

        self.recorded_run_messages.append(
            RecordedRunMessages(thread_id=thread_id, run_id=run_id, runs=tuple(runs))
        )
        stored = self.messages.setdefault(thread_id, [])
        next_seq = max((row.seq for row in stored), default=-1) + 1
        written = 0
        for group in runs:
            if any(row.run_id == group.run_id for row in stored):
                continue
            for row in group.rows:
                stored.append(
                    AgentThreadMessageRecord(
                        id=uuid4(),
                        thread_id=thread_id,
                        run_id=group.run_id,
                        seq=next_seq,
                        role=row.role,
                        text=row.text,
                        tool_name=row.tool_name,
                        tool_arguments=row.tool_arguments,
                        tool_call_id=row.tool_call_id,
                        failed=row.failed,
                        evidence=row.evidence,
                        run_meta=row.run_meta,
                        created_at=datetime.now(UTC),
                    )
                )
                next_seq += 1
                written += 1
        return written

    async def read_thread_messages(self, *, thread_id: UUID) -> list[AgentThreadMessageRecord]:
        """按顺序号读出这个会话的行；与真实实现同义。"""

        return sorted(self.messages.get(thread_id, []), key=lambda row: row.seq)

    async def update_scope(self, *, user_id, thread_id, scope):
        record = await self.get_owned_thread(user_id=user_id, thread_id=thread_id)
        record.scope = scope.model_dump(mode="json")

    async def update_llm_model(self, *, user_id, thread_id, llm_model_id):
        """保存会话的模型选择；与真实实现同义：不判可用性、不看在途运行。"""

        record = await self.get_owned_thread(user_id=user_id, thread_id=thread_id)
        record.llm_model_id = llm_model_id

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
        """删除归属记录与会话历史表里这一会话的行，不存在或不属于该账号时抛异常。"""

        record = self.threads.get(thread_id)
        if record is None or record.user_id != user_id:
            raise AgentThreadNotFoundError
        del self.threads[thread_id]
        # 与真实实现同义：会话行与历史行在同一个事务里删，库里没有外键约束，少删一边就是孤儿。
        self.messages.pop(thread_id, None)
        self.deleted.append(thread_id)

    async def list_known_thread_ids(self) -> set[UUID]:
        """返回全部账号的会话 id。"""

        return set(self.threads)


def create_offline_app(
    *,
    threads: "InMemoryAgentThreadService | None" = None,
    drain_timeout: float | None = None,
    **overrides: Any,
) -> FastAPI:
    """创建三个工厂都默认为离线替身的应用，并集中收拢 ``type: ignore``。

    Args:
        threads: 现成的内存会话 Service；省略时新建一个。传它是为了让两个应用实例共享同一份
            会话行（接手续跑的用例要把前一个实例排空后的库状态交给新实例）。
        drain_timeout: 运行注册表的排空上限；省略时用生产常量。测试传小值验证「到点按放弃收尾」。
        **overrides: 直接透传给 ``create_app`` 的参数，用来覆盖任一默认替身。常见的是
            ``runtime_factory``（注入本文件自己的 fake 检索 Runtime）；想测真实 Agent
            装配就传 ``agent_runtime_factory``。

    Returns:
        已挂载全部路由的应用；lifespan 不访问 PostgreSQL、Ollama、Qdrant 或大模型。
        ``app.state.offline_threads`` 上挂着那个内存会话 Service，需要预置或断言会话数据的
        用例直接取它，不必自己再覆盖一遍依赖。

    Notes:
        ``get_agent_thread_service`` 用的是 ``dependency_overrides`` 而不是 ``create_app`` 参数：
        它是请求级依赖，不是启动时装配的组件，``create_app`` 的签名里没有它的位置。
    """

    from agent_lab.api.dependencies import (
        get_agent_thread_service,
        get_llm_model_selection_service,
    )
    from agent_lab.main import create_app

    # 0、会话归属 Service 换成内存替身。**必须在 create_app 之前建**：运行注册表在 lifespan
    #    里装配，它读的必须是同一个替身；晚一步建就只能让注册表拿真实的 session 工厂去连库，
    #    而那个失败不会让测试报错，只会变成一次超时。
    offline_threads = threads or InMemoryAgentThreadService()
    # 0、目录替身同理：没有它，「解析当轮模型」那道门会拿真实的进程级 session 工厂去连库。
    #    默认给一条可用的默认模型，否则既有用例会集体停在「没有可用的模型」那个 409 上。
    offline_llm_models = InMemoryLlmModelSelectionService()

    # 1、先铺离线默认值，再让调用方的 overrides 覆盖，保证「漏写=安全」而不是「漏写=连真库」。
    defaults: dict[str, Any] = {
        "agent_runtime_factory": offline_agent_runtime_factory,
        "agent_run_registry_factory": lambda runtime=None: offline_agent_run_registry_factory(
            offline_threads, runtime, drain_timeout=drain_timeout
        ),
        "environment_admin_sync": skip_environment_admin_sync,
        "task_service_factory": lambda: object(),
        # 用量库替身：不覆盖它就会去读真实的 LLMOPS_DATABASE_URL，而离线测试没有那份配置。
        "usage_runtime_factory": offline_usage_runtime_factory,
    }
    app = create_app(**{**defaults, **overrides})  # type: ignore[arg-type]

    # 2、会话归属 Service 换成内存替身。少了这一步，任何请求 /agent/* 的测试都会真去连
    #    PostgreSQL（真实依赖持有绑定 .env 的进程级 session 工厂）。挂到 state 上是为了让用例
    #    既能预置数据、又不用重复写一遍 override。
    app.state.offline_threads = offline_threads
    app.dependency_overrides[get_agent_thread_service] = lambda: offline_threads
    app.state.offline_llm_models = offline_llm_models
    app.dependency_overrides[get_llm_model_selection_service] = lambda: offline_llm_models
    return app


# 离线替身里那条默认模型。任何请求 ``/agent/chat`` 的用例都会经过「解析当轮模型」那道门，
# 而真实实现持的是绑定 ``.env`` 的进程级 session 工厂——没有替身就只能去连真 PostgreSQL。
DEFAULT_OFFLINE_MODEL_ID = UUID("40000000-0000-4000-8000-0000000000f0")


class OfflineCatalogModel:
    """内存目录里的一条可用模型。

    可用性拆成两个开关（自己启用 / 所属渠道启用），与库上那两列一一对应：替身也要能构造出
    「模型自己开着、而渠道停了」这一种，那正是停用一条渠道会带走整批模型的形状。
    """

    def __init__(
        self,
        id: UUID,
        *,
        display_name: str | None = "演示模型",
        upstream_model_name: str = "offline-test-model",
        context_window: int = 32768,
        enabled: bool = True,
        provider_enabled: bool = True,
        is_default: bool = False,
    ) -> None:
        self.id = id
        self.display_name = display_name
        self.upstream_model_name = upstream_model_name
        self.context_window = context_window
        self.enabled = enabled
        self.provider_enabled = provider_enabled
        self.is_default = is_default

    @property
    def available(self) -> bool:
        """自己启用且所属渠道也启用——与仓库里那条 join 查询同一判据。"""

        return self.enabled and self.provider_enabled


class InMemoryLlmModelSelectionService:
    """内存版的「解析当轮模型」，语义与 ``LlmModelSelectionService`` 对齐但不碰数据库。

    为什么需要它：真实实现持进程级 session 工厂（绑 ``.env`` 的 ``DATABASE_URL``），
    没有替身就只能去连真库。默认目录里有一条可用的默认模型——否则每个请求 ``/agent/chat``
    的既有用例都会停在「没有可用的模型」那个 409 上。

    ``resolved`` 记下每次解析请求里的 id（按调用顺序），用例靠它断言「解析确实发生了、而且
    用的是哪一份」；真库上的查询语义由 ``tests/test_llm_model_selection_service.py`` 验。
    """

    def __init__(self, models: list[OfflineCatalogModel] | None = None) -> None:
        self.models: list[OfflineCatalogModel] = (
            models
            if models is not None
            else [OfflineCatalogModel(DEFAULT_OFFLINE_MODEL_ID, is_default=True)]
        )
        self.resolved: list[UUID | None] = []

    def find(self, model_id: UUID) -> OfflineCatalogModel | None:
        """按 id 取一条，不管它当前可不可用（读展示名要用这条）。"""

        return next((item for item in self.models if item.id == model_id), None)

    async def resolve_for_run(self, llm_model_id: UUID | None):
        """翻成当轮快照；失败种类与真实实现一一对应。"""

        from agent_lab.services.llm_model_errors import (
            LlmModelNotFoundError,
            LlmModelUnavailableError,
            NoAvailableLlmModelsError,
        )
        from agent_lab.services.llm_model_selection_service import snapshot_of

        self.resolved.append(llm_model_id)
        if llm_model_id is None:
            default = next(
                (item for item in self.models if item.is_default and item.available), None
            )
            if default is None:
                raise NoAvailableLlmModelsError
            return snapshot_of(default)
        record = self.find(llm_model_id)
        if record is None:
            raise LlmModelNotFoundError
        if not record.available:
            raise LlmModelUnavailableError
        return snapshot_of(record)

    async def describe_choice(self, llm_model_id: UUID | None):
        """读一个已存的选择在目录里的样子；**不判可用性、不抛错**。"""

        from agent_lab.services.llm_model_selection_service import snapshot_of

        if llm_model_id is None:
            return None
        record = self.find(llm_model_id)
        return None if record is None else snapshot_of(record)


class FakeSearchService:
    """记录检索请求并返回空结果，不执行任何网络 I/O。

    两个用处：证明 Agent 拿到的是同一个 Service 实例，以及在 Agent 装配失败时充当
    「只读链路还活着」的探针。
    """

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def resolve_scope(self, selection):
        if selection.mode == "selected" and set(selection.knowledge_base_ids) != set(NEWS_SCOPE.knowledge_base_ids):
            raise KnowledgeBaseNotFoundError
        return ResolvedKnowledgeBaseScope(mode=selection.mode, knowledge_bases=NEWS_SCOPE.knowledge_bases)

    async def search_documents(self, request, *, resolved_scope):
        self.calls.append(request)
        return []

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
    model: Any = None,
    *,
    model_resolver: Any = None,
    superuser: bool = True,
    anonymous: bool = False,
    agent_build_error: Exception | None = None,
    usage_collector: Any = None,
    usage_runtime: Any = None,
    checkpointer: Any = None,
    threads: "InMemoryAgentThreadService | None" = None,
    drain_timeout: float | None = None,
    agent_run_registry_factory: Any = None,
) -> tuple[FastAPI, FakeSearchRuntime]:
    """创建装着**真实** ``AgentRuntime`` 的离线应用。

    与 ``create_offline_app`` 的默认替身不同，这里注入的是真实 ``AgentRuntime.build``（配假模型和
    ``InMemorySaver``）。也就是说「图怎么编译、事件怎么排序、历史怎么存」仍由生产代码决定，只有模型
    和存储被换掉。需要请求 ``/agent/*`` 的测试都该用这个。

    Args:
        model: 注入的假聊天模型；省略时必须给 ``model_resolver``，否则运行期按目录解析
            （那时会去连数据库，离线用例拿不到）。
        model_resolver: 模型解析来源的替身。给了它就走上生产的解析包装链（用量采集在外面、
            解析在里面），只是目录读取换成预置的客户端。
        superuser: 为 ``False`` 时把当前账号换成普通账号（仍算已登录），用来测「普通账号能进」。
        anonymous: 为 ``True`` 时**不覆盖任何认证依赖**，请求表现为完全没带凭据，用来测 401。
            它与 ``superuser=False`` 是两件事：后者是「登录了但不是超管」，前者是「没登录」。
            权限放开后这两者必须分开，否则「没凭据进不来」这条会被一个普通账号的替身悄悄满足。
        agent_build_error: 非空时让 Agent 工厂抛这个异常，模拟装配失败。
        usage_collector: 非空时用它替掉 lifespan 传进来的用量采集器，供需要断言「这次对话记了
            什么」的用例注入记录用的假采集器。
        usage_runtime: 非空时用它替掉离线用量库替身，供需要走完整用量链路的用例注入带真实
            会话工厂与采集器的资源（例如跑完一轮对话再查用量接口）。
        checkpointer: 会话历史存储；省略时新建一个 ``InMemorySaver``。传现成的对象是为了让两个
            应用实例共享同一份会话历史（接手续跑用例要把前一个实例留下的 checkpoint 交给新实例）。
        threads: 会话归属的内存 Service；省略时新建。与 ``checkpointer`` 同理，传现成的
            对象才能让两个实例看到同一批会话行。
        drain_timeout: 运行注册表的排空上限；省略时用生产常量。测试传小值验证「到点按放弃收尾」。

        agent_run_registry_factory: 覆盖运行注册表的装配；省略时用离线默认。需要「有可用的图、
            但不扫描接手标记」的用例传一个 ``graph=None`` 的注册表，用来把「占位被拒」与「接手」
            两件事分开验证。

    Returns:
        ``(应用, 假检索 Runtime)``。检索 Runtime 用来断言关闭顺序，或在装配失败时当只读探针。

    Notes:
        不连 PostgreSQL、Qdrant，不访问网络，也不调真实大模型。会话归属仍走
        ``create_offline_app`` 装的内存替身，取 ``app.state.offline_threads`` 即可预置数据。
    """

    search_runtime = FakeSearchRuntime()

    def agent_factory(service: Any, lifespan_collector: Any) -> AgentRuntime:
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
            checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
            model=model,
            model_resolver=model_resolver,
            usage_collector=usage_collector if usage_collector is not None else lifespan_collector,
            # 退避是真 sleep。这些用例断言的是 HTTP 契约，不需要等。
            retry_initial_delay=0.0,
        )

    app = create_offline_app(
        runtime_factory=lambda: search_runtime,
        agent_runtime_factory=agent_factory,
        usage_runtime_factory=(
            (lambda: usage_runtime) if usage_runtime is not None else offline_usage_runtime_factory
        ),
        threads=threads,
        drain_timeout=drain_timeout,
        **(
            {"agent_run_registry_factory": agent_run_registry_factory}
            if agent_run_registry_factory is not None
            else {}
        ),
    )
    if not anonymous:
        app = (allow_superuser if superuser else allow_reader)(app)
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
    system_prompt: str | None = None,
    active_run_id: UUID | None = None,
    stop_requested_at: datetime | None = None,
    drained_at: datetime | None = None,
    llm_model_id: UUID | None = None,
) -> SimpleNamespace:
    """在内存会话表里预置一行归属记录。

    Args:
        app: 由 ``create_offline_app`` 造出的应用。
        thread_id: 要预置的会话 id。
        user_id: 归属账号，默认与 ``allow_superuser`` 覆盖出的当前账号一致。传别的值即可构造
            「这是别人的会话」。
        title: 会话标题。
        last_active_at: 最后活跃时间；省略时用当前时间。想构造确定的排序、或想构造「上一次运行
            已经失活」就显式传一个很久以前的时刻。
        system_prompt: 该会话的提示词快照；省略等同「用内置默认提示词」。
        active_run_id: 预置一个在途运行，用来构造「这个会话正在跑」。
        stop_requested_at: 预置一个陈旧的停止请求，用来验证新一次占位会把它清掉。
        drained_at: 预置一个「已排空、等接手」标记，用来验证它拒绝新提问、也不会被失活分支顶掉。
        llm_model_id: 预置会话保存的模型选择；省略就是「没选过」，提问时用默认模型。

    Returns:
        刚写进去的那行记录，便于随后修改或断言。
    """

    now = datetime.now(UTC)
    record = SimpleNamespace(
        thread_id=thread_id,
        user_id=user_id,
        title=title,
        scope={"mode": "all"},
        llm_model_id=llm_model_id,
        system_prompt=system_prompt,
        created_at=now,
        last_active_at=last_active_at or now,
        active_run_id=active_run_id,
        stop_requested_at=stop_requested_at,
        drained_at=drained_at,
    )
    app.state.offline_threads.threads[thread_id] = record
    return record


def is_claimable(record: SimpleNamespace, now: datetime) -> bool:
    """这行会话能不能被新的运行占下（与真实实现的条件写入同义）。

    Args:
        record: 内存会话表里的一行。
        now: 判定用的当前时刻。

    Returns:
        ``True`` 表示没有在途运行、没有排空标记，或那次运行已经超过失活阈值。
    """

    if record.drained_at is not None:
        # 带排空标记的会话连失活分支也不放行：旧的 checkpoint 停在半途，新提问会继续跑旧节点。
        return False
    if record.active_run_id is None:
        return True
    return record.last_active_at < now - timedelta(seconds=RUN_ZOMBIE_THRESHOLD_SECONDS)


__all__ = [
    "OFFLINE_LLM_SETTINGS",
    "DEFAULT_OFFLINE_MODEL_ID",
    "OFFLINE_DRAIN_TIMEOUT_SECONDS",
    "OFFLINE_HANDOVER_SCAN_INTERVAL_SECONDS",
    "FakeSearchRuntime",
    "FakeSearchService",
    "InMemoryAgentThreadService",
    "InMemoryLlmModelSelectionService",
    "OfflineAgentRuntime",
    "OfflineCatalogModel",
    "OfflineUsageRuntime",
    "RecordedRunMessages",
    "create_agent_app",
    "create_offline_app",
    "offline_agent_run_registry_factory",
    "offline_agent_runtime_factory",
    "offline_usage_runtime_factory",
    "seed_owned_thread",
    "send",
]
