"""把一次 Agent 运行从 HTTP 连接上拆下来，交给进程内的后台驱动者。

**为什么需要这个模块**（完整论据见 ``docs/adr/0035-run-outlives-its-subscriber.md``）：一次运行
原先由浏览器的响应体驱动——客户端拉响应体、响应体拉事件流、事件流拉模型，没有人拉，模型就不
往前走。于是关掉页面、断网、切走会话，这次运行立刻被掐断，而模型那次调用很可能已经处理完并计了
费：钱花了、答案没了。现在运行的推进由本模块的驱动者负责，HTTP 响应退化成它的一个**可选订阅者**，
浏览器走了照旧跑完、结果自然落进会话历史。

**这里只做「把事件流抽干、把事件分发给订阅者、收尾时释放会话位」**：事件怎么翻译在
``agent/streaming.py``，SSE 的行格式和心跳在 ``api/agent_chat.py``，持久化状态怎么算终态在
``agent/replay.py``，会话行怎么读写（占位、续活、释放）在 ``services/agent_thread_service.py``。
本模块不直接写 SQL、不调用模型、不碰 FastAPI。

**订阅者集合可以为空**，这是本模块存在的意义，也是它与「生成器 + 反向压力」最本质的区别：驱动者
不等任何人来拉。代价是不再有反向压力，所以订阅者的队列是无界的——一次运行的输出有上界（模型调用
次数和正文长度都有上限），而断开的客户端会被 ASGI 服务器取消生成器、随即退订，不会长期堆积。
"""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langchain_core.messages import AIMessage
from langgraph.errors import GraphDrained
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import RunControl

from agent_lab.agent.context import AgentContext
from agent_lab.agent.limits import (
    RUN_DRAIN_TIMEOUT_SECONDS,
    RUN_EVENT_POLL_INTERVAL_SECONDS,
    RUN_HANDOVER_SCAN_INTERVAL_SECONDS,
    RUN_LIVENESS_UPDATE_INTERVAL_SECONDS,
    RUN_STATE_POLL_INTERVAL_SECONDS,
)
from agent_lab.agent.replay import build_replay_turns
from agent_lab.agent.streaming import (
    MODEL_NODE,
    PersistedModelMessage,
    build_terminal_event,
    stream_agent_events,
)
from agent_lab.config.llm import LangSmithSettings, get_langsmith_settings
from agent_lab.schemas.agent_chat import (
    AgentChatEvent,
    AgentDoneEvent,
    AgentErrorEvent,
    AgentTokenEvent,
)


if TYPE_CHECKING:
    # 只在类型检查时导入：运行单元只在构造时收下这个对象、转手调用它的方法，运行时不构造它。
    from agent_lab.services.agent_thread_service import AgentThreadService, DrainedRunClaim


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentRun:
    """一次已经在跑的运行，以及正在看它的那些订阅者。

    订阅者是一个队列集合，而不是一个「当前连接」：浏览器可能在中途断开又重连（刷新），而运行
    本身只有一次。队列元素为 ``None`` 表示这次运行的输出已经发完，订阅者可以收尾了。

    Attributes:
        run_id: 本次运行的标识，与提问携带、以及补写消息上记的是同一个值。
        thread_id: 本次运行所属的会话。
        subscribers: 正在接收事件的队列集合；为空表示当前没有人看，运行照旧跑完。
        task: 驱动这次运行的后台任务；由注册表持有强引用，否则可能被垃圾回收掉。
        emitted_text: 服务端已经推出去、但尚未落库的模型文本。

            口径是「**尚未落库**的那部分」，不是「整次运行」：一次运行里模型节点会被调用多次
            （每次工具调用后都要再问一次），先完成的那些节点已经把文本落库了。累计整次运行会在
            收尾时把已落库的文本再写一遍，回放出的答案出现重复。所以边界取「最近一次已落库的
            模型消息」——每收到一条 ``PersistedModelMessage`` 就归零，之后重新累。
        touched_at: 上一次把会话活跃时间续到库里的时刻，取事件循环的单调时钟。
        stop_requested: 是否有人要求停下这次运行。由本进程的轮询协程从库里读到后写进来，
            驱动者每一轮只看这个内存标志——它不为此碰数据库（ADR 0037）。
        control: 本次运行的排空控制器，透传给 ``graph.astream``。进程收尾时 ``drain()`` 请求它
            排空，图会在当前 superstep 落盘后停下并抛 ``GraphDrained``。
        drain_requested: 本次运行是否被本进程请求过排空。用来区分「收尾时被排空的运行」与
            「其他原因被取消的运行」——两者补写与释放的处置相同，只有日志不同。
    """

    run_id: UUID
    thread_id: UUID
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    emitted_text: str = ""
    touched_at: float = 0.0
    stop_requested: bool = False
    control: RunControl = field(default_factory=RunControl)
    drain_requested: bool = False

    def subscribe(self) -> asyncio.Queue:
        """登记一个新的订阅者并返回它的队列。

        Returns:
            本次运行的后续事件会依次进入这个队列；``None`` 表示已经发完。

        Notes:
            订阅是「从此刻开始」，不回放已经发过的事件：刷新之后要看的是最终内容，不是把这一轮
            已经产生的实时文字重发一遍（见 spec 的「超出范围：在途逐字续看」）。
        """

        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """取消一个订阅者。重复取消无害。"""

        self.subscribers.discard(queue)

    def publish(self, event: AgentChatEvent | None) -> None:
        """把一个事件送给当前所有订阅者。

        Args:
            event: 要分发的事件；``None`` 表示输出结束。

        Notes:
            纯内存操作，不执行 I/O、不等待任何订阅者。**刻意不在队列满时阻塞**：等待慢客户端
            会让驱动者停在分发这一步，等于把运行重新挂回连接的快慢上。
        """

        for queue in self.subscribers:
            queue.put_nowait(event)

    def close(self) -> None:
        """通知所有订阅者运行已经收尾，并清空订阅者集合。"""

        self.publish(None)
        self.subscribers.clear()


class AgentRunRegistry:
    """本 API 进程正在驱动的那几次运行。

    每个 API 进程一个实例，生命周期与进程一致：lifespan 启动时 ``start``、退出时 ``close``。
    它持有的强引用是必需的——``asyncio`` 只保留任务的弱引用，没人引用时驱动任务可能被垃圾回收，
    表现是「后台运行跑到一半静悄悄消失」。

    Attributes:
        _threads: 会话 Service；本注册表用它的三个方法读写 ``agent_threads``（释放占位、续活、读状态）。
        _event_poll_interval: 驱动者等待下一个事件的时间片；留成参数只为让测试不必真的等一秒。
        _liveness_interval: 往库里续 ``last_active_at`` 的节奏；同样只为让测试传小值。
        _state_poll_interval: 批量读「停止请求」的节奏；同样只为让测试传小值。
        _drain_timeout: 进程收尾时等手上在途运行走到边界的总预算；只为让测试传小值。
        _graph: 进程级共享的已编译 Agent 图，供接手续跑用；``None`` 表示拿不到（Agent 装配
            失败），此时不去认领「等接手」的标记。
        _langsmith_settings: 接手续跑时用的追踪配置；``None`` 表示按进程配置取（见 ``_settings``）。
        _handover_scan_interval: 扫描「等接手」标记的节奏；只为让测试传小值。
        _poller: 读停止请求的协程任务；随注册表启动，随它关闭。
        _scanner: 扫描并接手的协程任务；随注册表启动，随它关闭。
        _draining: 本进程是否已经进入收尾；为真时不再接受新的接手（见 ADR 0040）。
        _runs: ``thread_id`` 到那次运行的映射。同一个会话同时最多一次运行（见 ADR 0037），
            所以用会话 id 做键而不是运行 id。
    """

    def __init__(
        self,
        *,
        threads: "AgentThreadService",
        graph: CompiledStateGraph | None = None,
        langsmith_settings: LangSmithSettings | None = None,
        event_poll_interval: float = RUN_EVENT_POLL_INTERVAL_SECONDS,
        liveness_interval: float = RUN_LIVENESS_UPDATE_INTERVAL_SECONDS,
        state_poll_interval: float = RUN_STATE_POLL_INTERVAL_SECONDS,
        drain_timeout: float = RUN_DRAIN_TIMEOUT_SECONDS,
        handover_scan_interval: float = RUN_HANDOVER_SCAN_INTERVAL_SECONDS,
    ) -> None:
        """记录会话 Service 与时间片参数，不建任务、不碰数据库。

        Args:
            threads: 会话 Service。它自己无状态（真正贵的是数据库连接，而连接归它按需开关），
                所以注册表持有一个实例不构成额外的连接占用。
            graph: 进程级共享的已编译 Agent 图。接手续跑只能用它（与对话入口同一张图），
                拿不到就不认领标记。
            langsmith_settings: 接手续跑时的追踪配置；省略时按进程配置取。
            event_poll_interval: 驱动者每等多久回来看一眼停止标志。生产不要传，默认值才是
                安全的那个；测试传小值是为了不必为了看一次停止而真的等满一秒。
            liveness_interval: 续活节奏；测试传小值才能在毫秒级验证「活着的运行不会被判成僵尸」。
            state_poll_interval: 停止请求的轮询节奏；改大只会让停止变慢，不会让它失效。
            drain_timeout: 收尾时等排空的总预算；测试传小值才能验证「到点按放弃收尾」。
            handover_scan_interval: 扫描「等接手」标记的节奏；测试传小值才能在毫秒级验证接手。
        """

        self._threads = threads
        self._graph = graph
        self._langsmith_settings = langsmith_settings
        self._event_poll_interval = event_poll_interval
        self._liveness_interval = liveness_interval
        self._state_poll_interval = state_poll_interval
        self._drain_timeout = drain_timeout
        self._handover_scan_interval = handover_scan_interval
        self._poller: asyncio.Task | None = None
        self._scanner: asyncio.Task | None = None
        self._draining = False
        self._runs: dict[UUID, AgentRun] = {}

    def _settings(self) -> LangSmithSettings:
        """取接手续跑用的追踪配置：注入了就用注入的那份，否则读进程配置。"""

        return self._langsmith_settings or get_langsmith_settings()

    async def start(self) -> None:
        """启动停止请求轮询与接手扫描两个协程。

        Notes:
            每个 API 进程一套，与进程同寿。接手扫描启动时先扫一次，之后按固定节奏再扫。
            重复调用无害。
        """

        if self._poller is None:
            self._poller = asyncio.create_task(
                self._poll_stop_requests(), name="agent-run-stop-poller"
            )
        if self._scanner is None:
            self._scanner = asyncio.create_task(
                self._scan_handovers(), name="agent-run-handover-scanner"
            )

    async def drain(self, *, timeout: float | None = None) -> None:
        """请求本进程手上的在途运行排空到可交接的边界，最多等 ``timeout`` 秒。

        部署或重启的收尾从这里开始：对手上**所有**在途运行一起请求排空，然后并发等它们走到
        边界（一个 superstep 的结束点，状态已落盘）。到点还没走到的由 ``close()`` 取消，按
        既有的「被中断」路径收尾（补写已输出内容、释放会话位）——这是规格里说的「放弃」。

        本方法只请求排空与等待，它自己不做补写、不释放会话位，也不写任何标记：那三件事都在
        驱动者 ``_drive`` 里发生（它才知道这次运行是走到了边界还是被取消）。

        Notes:
            **不等连接**。排空的触发点是 ASGI 生命周期收尾，此时 uvicorn 已经停掉连接与请求任务
            （超时后直接掐），而运行早就与连接解耦，所以那条订阅连接断掉不影响这里。执行
            PostgreSQL 写入（每一次收尾）。

            重复调用无害：第二次看到手上已经没有运行会直接返回。
        """

        self._draining = True
        runs = list(self._runs.values())
        for run in runs:
            run.drain_requested = True
            run.control.request_drain("shutdown")
        tasks = {run.task for run in runs if run.task is not None}
        if not tasks:
            return
        budget = self._drain_timeout if timeout is None else timeout
        _, pending = await asyncio.wait(tasks, timeout=budget)
        for run in runs:
            if run.task is not None and run.task in pending:
                # 只记两个 id：这是给运维看的「这次部署丢了一次运行」，不该带正文或提问。
                logger.warning(
                    "排空超时，运行按放弃收尾 thread_id=%s run_id=%s",
                    run.thread_id,
                    run.run_id,
                )

    async def close(self) -> None:
        """停掉轮询协程，并停掉所有还在驱动的运行，不等它们收尾。

        Notes:
            跟着 ``drain()`` 之后调用：那时还留在 ``_runs`` 里的都是排空超时的那几个，这里取消它们
            就是规格说的「放弃收尾」（补写已输出内容、释放会话位）。单独调用（比如没走排空）时行为
            与改动前一致：取消并释放。

            **收尾可能来不及**——强杀、内存耗尽、断电都属于这一类，会话上的在途标记只能等失活
            判定自动释放，这段窗口已确认为接受的边界（见 ADR 0036），不是这里的 bug。
        """

        if self._poller is not None:
            self._poller.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._poller
            self._poller = None
        if self._scanner is not None:
            self._scanner.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._scanner
            self._scanner = None

        tasks = [run.task for run in self._runs.values() if run.task is not None]
        self._runs.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            # 收尾过程里还会碰数据库，而那时连接池可能已经在关；失败只能是日志，不能挡住退出。
            with suppress(asyncio.CancelledError, Exception):
                await task

    async def _poll_stop_requests(self) -> None:
        """按固定节奏把「有人要求停下」读进内存标志。

        为什么不直接取消：停止请求可能落在另一个进程上（生产 API 是 2 个副本、每个容器一个进程），拿不到
        本进程的运行对象。所以库里那两列是唯一真相，跨进程靠它们传递，代价是最多一个轮询周期的延迟
        ——对「用户按下停止按钮」这个动作而言感知不到（见 ADR 0037）。

        Notes:
            执行 PostgreSQL 读。库不可用时只记日志、下一轮再试：停止信号晚一点到，比因为一次读失败
            把轮询协程整个弄死要好。
        """

        while True:
            await asyncio.sleep(self._state_poll_interval)
            if not self._runs:
                continue
            try:
                states = await self._threads.read_run_states(thread_ids=list(self._runs))
            except Exception as exc:
                logger.warning("读取停止请求失败 error_type=%s", type(exc).__name__)
                continue
            for thread_id, (active_run_id, stop_requested_at) in states.items():
                run = self._runs.get(thread_id)
                # 只有在途 id 与本地这次运行相同才认：不同就是上一次运行留下的标记，
                # 不能拿来停这一次（那正是停止请求迟到时的情形）。
                if run is None or active_run_id != run.run_id:
                    continue
                if stop_requested_at is not None:
                    run.stop_requested = True

    async def _scan_handovers(self) -> None:
        """按固定节奏扫描并接手「被排空、等接手」的运行。

        启动时先扫一次，之后每隔 ``handover_scan_interval`` 扫一次。**扫到什么就接什么，不设
        「一轮最多接几个」的上限**：那些运行本来就在跑，换个进程跑不会增加负载；加了上限只会
        让排在后面的运行在失活阈值前没被接走、被误判成「没人接手」。

        Notes:
            执行 PostgreSQL 读与写入（扫描、抢所有权、重建上下文）。单个会话接手失败只影响那一个：
            扫描循环继续，下一轮再试别的。
        """

        while True:
            await self._take_over_drained()
            await asyncio.sleep(self._handover_scan_interval)

    async def _take_over_drained(self) -> None:
        """扫一遍「等接手」标记，把能接的接下来。

        **认领之前先看图拿不拿得到**：Agent 装配失败（``graph is None``）时不认领，把标记留给
        能跑的进程——否则一个坏副本会替健康副本把本可接手的运行收成未完成。这和「认领之后失败」
        是两回事，后者才是放弃。
        """

        if self._draining or self._graph is None:
            return
        try:
            thread_ids = await self._threads.list_drained_thread_ids()
        except Exception as exc:
            logger.warning("扫描排空标记失败 error_type=%s", type(exc).__name__)
            return
        for thread_id in thread_ids:
            if self._draining:
                return
            if thread_id in self._runs:
                # 本进程已经在跑这个会话（理论上不可能同时有标记），别接第二次。
                continue
            try:
                claim = await self._threads.claim_drained_run(thread_id=thread_id)
            except Exception as exc:
                logger.warning("抢占排空运行失败 thread_id=%s error_type=%s", thread_id, type(exc).__name__)
                continue
            if claim is None:
                # 标记已被别的进程消费，或标记没有对应的在途运行；两种都什么都不做。
                continue
            await self._take_over(claim)

    async def _take_over(self, claim: "DrainedRunClaim") -> None:
        """重建运行上下文并接着跑那一次运行。

        范围取自**上一轮冻结在 checkpoint 里的那一份**（不是会话行上的当前选择，否则续跑后的节点
        会换一个范围作答）；提示词与账号从会话行取。拿不到范围就不接手，按放弃收尾。

        Notes:
            重建上下文失败（认领之后）与接手后运行中途抛错是两条不同的放弃时机，两者都只尝试一次
            不重试：区分「暂时失败」与「永久失败」区分不出来（ADR 0037）。
        """

        graph = self._graph
        if graph is None:  # pragma: no cover - 调用方已经挡过，留作类型收窄
            return
        found, scope = await self._read_frozen_scope(
            graph, thread_id=claim.thread_id, run_id=claim.run_id
        )
        if not found:
            await self._abandon_claim(claim, reason="重建不出运行范围")
            return
        context = AgentContext(
            run_id=claim.run_id,
            system_prompt=claim.system_prompt,
            scope=scope,
            user_id=claim.user_id,
            thread_id=claim.thread_id,
        )
        logger.info("接手续跑 thread_id=%s run_id=%s", claim.thread_id, claim.run_id)
        run = AgentRun(run_id=claim.run_id, thread_id=claim.thread_id)
        self._launch(
            run,
            graph=graph,
            message=None,
            context=context,
            langsmith_settings=self._settings(),
        )

    @staticmethod
    async def _read_frozen_scope(
        graph: CompiledStateGraph,
        *,
        thread_id: UUID,
        run_id: UUID,
    ) -> tuple[bool, Any]:
        """从 checkpoint 里取「这次运行冻结的知识库范围」。

        取法是回放模块已有的解析（提问消息上的 ``agent_run.scope``），不另造一套。

        Returns:
            ``(找到了没有, 范围)``。第一个值为假表示**这一轮已经不在 checkpoint 里**——最典型是
            历史压缩把那条提问消息抹掉了。此时不能接手（不能拿会话行的当前选择冒充当时范围）。
            第一个值为真但范围是 ``None`` 是合法的：当初那次运行本来就没有范围。

        Notes:
            执行会话历史读取（checkpointer 的读 I/O），不写任何东西。
        """

        snapshot = await graph.aget_state({"configurable": {"thread_id": str(thread_id)}})
        turns, _, _ = build_replay_turns((snapshot.values or {}).get("messages") or [])
        turn = next((item for item in turns if item.run_id == run_id), None)
        if turn is None:
            return False, None
        return True, turn.scope

    async def _abandon_claim(self, claim: "DrainedRunClaim", *, reason: str) -> None:
        """接手失败时把这次运行收成未完成、释放会话位。

        标记在抢所有权时已经被消费掉，所以这里只释放占位；**只尝试一次不重试**。

        Args:
            claim: 抢到的那次运行。
            reason: 放弃原因的分类标签（固定短语，不带异常文本或用户内容）。

        Notes:
            执行 PostgreSQL 写入。失败只记日志：它会由失活判定在阈值后自动释放。
        """

        try:
            await self._threads.finish_run(thread_id=claim.thread_id, run_id=claim.run_id)
        except Exception as exc:
            logger.error(
                "接手失败后释放会话占位也失败 thread_id=%s run_id=%s error_type=%s",
                claim.thread_id,
                claim.run_id,
                type(exc).__name__,
            )
        logger.warning(
            "接手失败，运行按放弃收尾 thread_id=%s run_id=%s reason=%s",
            claim.thread_id,
            claim.run_id,
            reason,
        )

    def begin(
        self,
        *,
        graph: CompiledStateGraph,
        thread_id: UUID,
        run_id: UUID,
        message: str,
        context: AgentContext,
        langsmith_settings: LangSmithSettings,
    ) -> AgentRun:
        """创建一次运行并在后台开始驱动它，立刻返回它的订阅入口。

        Args:
            graph: 进程级共享的已编译 Agent 图。
            thread_id: 本次运行所属会话。
            run_id: 本次运行的标识。
            message: 用户这一轮的提问。
            context: 本次运行的上下文（提示词、范围、账号与会话标识）。
            langsmith_settings: 追踪开关与凭据。

        Returns:
            已经开始跑的 ``AgentRun``；调用方订阅它拿事件、或直接不管它。

        Notes:
            本方法不执行任何 I/O，也不等运行结束。任务的异常不会冒泡到调用方——它在驱动者
            内部被分类成 ``error`` 事件，和「事件流抛异常」那条路保持一致。
        """

        run = AgentRun(run_id=run_id, thread_id=thread_id)
        return self._launch(
            run,
            graph=graph,
            message=message,
            context=context,
            langsmith_settings=langsmith_settings,
        )

    def _launch(
        self,
        run: AgentRun,
        *,
        graph: CompiledStateGraph,
        message: str | None,
        context: AgentContext,
        langsmith_settings: LangSmithSettings,
    ) -> AgentRun:
        """把一次运行登记进本进程并启动驱动任务；``begin`` 与接手续跑共用。

        Args:
            run: 已经建好、带运行 id 与会话 id 的 ``AgentRun``。
            graph: 进程级共享的已编译 Agent 图。
            message: 用户这一轮的提问；``None`` 表示以「无新输入」的方式续跑。
            context: 本次运行的上下文。
            langsmith_settings: 追踪开关与凭据。

        Returns:
            同一个 ``run``；调用方订阅它拿事件、或直接不管它。
        """

        run.touched_at = asyncio.get_running_loop().time()
        self._runs[run.thread_id] = run
        run.task = asyncio.create_task(
            self._drive(
                run,
                graph=graph,
                message=message,
                context=context,
                langsmith_settings=langsmith_settings,
            ),
            name=f"agent-run-{run.run_id}",
        )
        return run

    async def _drive(
        self,
        run: AgentRun,
        *,
        graph: CompiledStateGraph,
        message: str | None,
        context: AgentContext,
        langsmith_settings: LangSmithSettings,
    ) -> None:
        """把事件流读到结束，并在这个过程中把事件分发给当时在看的订阅者。

        与 ``api/agent_chat.py`` 原先那段「带心跳的转发」共用同一个手工取迭代器的形状，原因也
        相同：要「等一下、没等到就干点别的、再回来接着等同一个事件」，普通 ``async for`` 做不到。
        区别是本函数把结果**推给订阅者**而不是从函数里 ``yield`` 出去，这样它就不必由谁在拉。

        Args:
            run: 本次运行。
            graph: 进程级共享的已编译 Agent 图。
            message: 用户这一轮的提问；**``None`` 表示接手续跑**（不问新问题，从 checkpoint
                里尚未完成的节点接着跑）。
            context: 本次运行的上下文。
            langsmith_settings: 追踪开关与凭据。

        Notes:
            执行模型、Qdrant、PostgreSQL 的读 I/O（都在 ``stream_agent_events`` 内部）。已分类的
            失败不会从这里抛出——翻译层已经把它们转成 ``error`` 事件了。唯一会冒出来的是
            ``GraphDrained``：它是「排空到边界」的正常收尾信号，不是错误。
        """

        resume = message is None
        iterator = stream_agent_events(
            graph,
            message=message or "",
            thread_id=run.thread_id,
            context=context,
            langsmith_settings=langsmith_settings,
            control=run.control,
            resume=resume,
        ).__aiter__()
        pending: asyncio.Task | None = None
        # 结束时还要不要补写「已输出但未落库」的那段文本。两个正常终态会把它置假：
        # ``done`` 表示这一轮由 LangGraph 自己按节点写完了，``error`` 在补写之后转给订阅者。
        needs_write_back = True
        # 结束时发给订阅者的最后一个事件。所有路径都在 ``finally`` 里统一发出，因为释放会话位
        # 必须先于它（理由见 ``_release``）。
        terminal: AgentChatEvent | None = None
        # 是否因为「有人要求停止」而收尾。它决定要不要自己算一个终态事件——这条路上没有现成的
        # 终态可转发（事件流是被我们取消掉的）。
        stopped = False
        # 是否因为「有人要求排空」而停在可交接的边界。它与 stopped 是两种结局，不能混。
        drained = False
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(iterator.__anext__())
                # 用 wait 而不是 wait_for：wait 超时后**不取消**任务，所以下一轮还能接着等
                # 同一次 __anext__；wait_for 会把它取消掉，等于丢一个正在生成的事件。
                done, _ = await asyncio.wait({pending}, timeout=self._event_poll_interval)
                await self._touch_if_due(run)
                if not done:
                    if run.stop_requested:
                        # 协作式停止：取消的只是这一次模型调用，**运行单元本身不被取消**，所以下面
                        # 的补写与收尾发生在完全正常的上下文里，没有任何取消在传播。
                        await self._cancel_pending(pending)
                        pending = None
                        stopped = True
                        break
                    continue
                finished, pending = pending, None
                try:
                    event = finished.result()
                except StopAsyncIteration:
                    break
                # 一个模型节点刚完整结束、它的文本已随 checkpoint 落库：累积口径的边界推到这里。
                if isinstance(event, PersistedModelMessage):
                    run.emitted_text = ""
                    continue
                if isinstance(event, AgentTokenEvent):
                    run.emitted_text += event.text
                # 上游失败（含超时、限流）也是「被中断」：先把已经推出去的那段留住，再把失败
                # 转给订阅者。顺序不能反——订阅者收到 error 之后可能立刻回放，那时内容就该已在。
                if isinstance(event, AgentErrorEvent):
                    await self._keep_emitted_text(run, graph=graph)
                    needs_write_back = False
                    terminal = event
                    break
                if isinstance(event, AgentDoneEvent):
                    needs_write_back = False
                    terminal = event
                    break
                run.publish(event)
        except GraphDrained:
            # 排空：图停在当前 superstep 之后、下一个 superstep 之前，那一步的结果已经落盘。
            # 这不是失败，也不是「停止」：不补写、不标未完成、不释放会话位，留给接手者续跑
            # （见 ADR 0040）。补写会把已经落盘的内容写成重复的一截，而释放会话位会让接手
            # 找不到它。
            drained = True
            needs_write_back = False
            terminal = None
        finally:
            # 正常结束、异常、以及进程退出时被取消，都会走到这里。
            if not drained and needs_write_back:
                # 事件流没有走到终态就结束了，或者运行单元自己决定收尾（停止）。两种都是
                # 「被中断」，都该把用户已经看到的文字留在会话里。
                await self._keep_emitted_text(run, graph=graph)
            if not drained and stopped:
                # 停止这条路没有现成的终态事件，按持久化状态算一个：前端保持连接等的就是它，
                # 而且它与刷新后回放看到的是同一份口径（同一个 build_terminal_event）。
                terminal = await build_terminal_event(
                    graph,
                    thread_id=run.thread_id,
                    run_id=run.run_id,
                    scope=context.scope,
                )
            if pending is not None:
                pending.cancel()
                # 必须等它真的结束：__anext__ 还在跑的时候 aclose() 会直接 RuntimeError。
                with suppress(asyncio.CancelledError, Exception):
                    await pending
            with suppress(asyncio.CancelledError, Exception):
                await iterator.aclose()
            if drained:
                # 排空不走释放：会话位留着、标记写下，接手者靠它确认这次运行还挂着（见 ADR 0040）。
                await self._mark_drained(run)
                # 只记 id，让运维能从日志看出排空发生过。
                logger.info(
                    "Agent 运行已排空，等待接手 thread_id=%s run_id=%s",
                    run.thread_id,
                    run.run_id,
                )
            else:
                await self._release(run)
            if terminal is not None:
                run.publish(terminal)
            self._runs.pop(run.thread_id, None)
            run.close()
            logger.info(
                "Agent 运行收尾 thread_id=%s run_id=%s stopped=%s drained=%s",
                run.thread_id,
                run.run_id,
                stopped,
                drained,
            )

    @staticmethod
    async def _cancel_pending(pending: asyncio.Task) -> None:
        """取消「等下一个事件」那一步，并等它真的结束。

        取消的是 ``graph.astream`` 那一次迭代，也就是正在跑的那次模型调用；驱动者自己继续往下走，
        所以收尾写入不会发生在被取消的上下文里。必须等它结束再继续：还在跑的时候关生成器会直接
        ``RuntimeError``。
        """

        pending.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await pending

    async def _mark_drained(self, run: AgentRun) -> None:
        """给一次走到可交接边界的运行写下「等接手」标记。

        标记写在会话行上，接手者靠它知道「该接着跑哪一次运行」；它不释放会话位，所以那个会话
        在接手或放弃之前不接受新提问（见 ADR 0040）。

        Notes:
            执行 PostgreSQL 写入。失败只记日志：写不下标记最坏的后果是那个会话等失活阈值后自动
            解锁，用户重新提问——这比因为一次写入失败把整个进程退出流程弄断要好。
        """

        try:
            updated = await self._threads.mark_drained(
                thread_id=run.thread_id,
                run_id=run.run_id,
            )
        except Exception as exc:
            logger.error(
                "写下排空标记失败 thread_id=%s run_id=%s error_type=%s",
                run.thread_id,
                run.run_id,
                type(exc).__name__,
            )
            return
        if not updated:
            # 条件写的是「占位的就是这次运行」；0 行说明它已经被失活判定顶掉，不能把标记写到
            # 新运行头上。
            logger.warning(
                "排空标记未写下：这次运行已不再占位 thread_id=%s run_id=%s",
                run.thread_id,
                run.run_id,
            )

    async def _release(self, run: AgentRun) -> None:
        """释放这次运行占下的会话位。

        **必须在发出终态事件之前调用。** 反过来的话，用户拿到 ``done`` 立刻追问，会撞上自己刚刚
        结束的那次运行还没释放的占位，得到一次莫名其妙的 409——而那次运行其实已经跑完了。

        Notes:
            执行 PostgreSQL 写入。失败只记日志：它会由失活判定在阈值后自动释放，而收尾失败不能
            反过来丢掉本次运行的终态事件。
        """

        try:
            await self._threads.finish_run(thread_id=run.thread_id, run_id=run.run_id)
        except Exception as exc:
            logger.error(
                "释放会话占位失败 thread_id=%s run_id=%s error_type=%s",
                run.thread_id,
                run.run_id,
                type(exc).__name__,
            )

    async def _touch_if_due(self, run: AgentRun) -> None:
        """到点就把这次运行的会话活跃时间续一下，供失活判定区分「在跑」与「进程已死」。

        Notes:
            执行 PostgreSQL 写入。失败只记日志：续不上最多让这次运行在阈值后被当成失活，
            而模型调用本身不受影响。
        """

        now = asyncio.get_running_loop().time()
        if now - run.touched_at < self._liveness_interval:
            return
        # 先记时刻再写库：写失败了也不会每一轮都重试一次，把库打得更狠。
        run.touched_at = now
        try:
            await self._threads.touch_run(
                thread_id=run.thread_id,
                run_id=run.run_id,
                now=datetime.now(UTC),
            )
        except Exception as exc:
            logger.warning(
                "续会话活跃时间失败 thread_id=%s run_id=%s error_type=%s",
                run.thread_id,
                run.run_id,
                type(exc).__name__,
            )

    async def _keep_emitted_text(self, run: AgentRun, *, graph: CompiledStateGraph) -> None:
        """把已推出但尚未落库的模型输出补写进会话。

        为什么要写这一笔（见 ADR 0036）：checkpoint 的写入时机是**每个节点完整结束时**，不是每个
        token。模型节点要等整个答案吐完才算结束，所以中断一个正在输出答案的节点，那一整格都不
        落库；而用户在屏幕上确实已经看到了半截文字。已经跑完的那些节点不需要补救，补的只有
        「模型已经吐出、但所在节点没跑完」的那一段。

        Args:
            run: 本次运行；它带着待补写的文本。
            graph: 进程级共享的已编译 Agent 图，用来写会话历史。

        Notes:
            **没有文本就不写**：写一条空的模型消息会把这一轮变成「有回答但内容为空」的怪状态。

            消息上带 ``agent_run``，其 ``run_id`` 与这一轮提问携带的相等、``completed`` 取假。
            取假是为了与截断语义一致——取真会把一段半句话记成完整答案。这个字段只影响回放给出的
            ``status``，不影响文本是否显示。

            显式给 ``as_node``：省略时 LangGraph 会试从 ``versions_seen`` 推断，推断不出就抛
            ``InvalidUpdateError: Ambiguous update, specify as_node``。写的是模型消息，所以按模型
            节点记——这同时让图的 ``next`` 按「模型之后该走哪」重新算，不会把这一轮挂成待跑工具。

            执行会话历史写入（真实部署下是 checkpointer 自己的 psycopg 连接）。**写入失败只记日志**：
            收尾失败不能反过来中断占位释放和订阅者收尾，那会把一次可自愈的写入失败放大成会话卡住。
        """

        if not run.emitted_text:
            return
        text, run.emitted_text = run.emitted_text, ""
        try:
            await graph.aupdate_state(
                {"configurable": {"thread_id": str(run.thread_id)}},
                {
                    "messages": [
                        AIMessage(
                            content=text,
                            additional_kwargs={
                                "agent_run": {"run_id": str(run.run_id), "completed": False}
                            },
                        )
                    ]
                },
                as_node=MODEL_NODE,
            )
        except Exception as exc:
            # 只记类型：异常文本可能带连接串或正文。
            logger.error(
                "补写被中断运行的已输出内容失败 thread_id=%s run_id=%s error_type=%s",
                run.thread_id,
                run.run_id,
                type(exc).__name__,
            )


__all__ = ["AgentRun", "AgentRunRegistry"]
