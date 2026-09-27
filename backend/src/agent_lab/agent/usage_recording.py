"""把模型客户端包一层，让每一次模型调用都经过采集点。

本模块住在 Agent 侧，所以可以依赖 LangChain；它只负责把一次调用的可观察事实装成
``usage.contracts.UsageRecord`` 交给注入的采集器，不碰数据库、不读配置、不构造客户端，
用量子包因此仍然与领域解耦（见 ``docs/adr/0033-usage-module-independent-of-domain.md``）。

**为什么包在公开调用方法外面，而不是实现／覆写生成钩子。** 图和中间件调用的都是
``invoke`` / ``ainvoke`` 这类公开方法，包在这里一个生成钩子都不用碰，「这次要不要走流式」
仍由底层模型自己判断：框架先看子类有没有覆写 ``_stream`` / ``_astream``，覆写了才判定
「支持流式」，而生产里真正让底层流式的是挂上去的流式回调处理器。改写生成钩子会直接改掉
这个判断，所以本类**只**覆写公开方法，``_stream`` / ``_astream`` 一概不动。

这条选择带来两个必须显式处理的后果，都在下面各自的注释里：

* ``bind_tools`` / ``bind`` 必须把结果重新挂回包装（否则工具丢了，或者记账丢了）；
* 模型的可观测属性必须逐项手工委托（否则中间件读到的是包装的默认值，而不是真实模型的）。

**每次调用恰好一条记录**：正常返回记一条完成，抛异常（含被取消）记一条失败后原样再抛。为什
么失败也要记，见 ``_failed_outcome``。

**包装的存在只应当带来「多一条记录」**，不应当改变任何其它可观察行为。
"""

import logging
import time
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langgraph.runtime import get_runtime
from pydantic import Field

from agent_lab.usage.contracts import (
    UsageCollector,
    UsageRecord,
    UsageSource,
    UsageStatus,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class _CallOutcome:
    """一次调用的结局，成功与失败共用的中间形状。

    把「成功」与「失败」的差别收敛到四个 token 数、状态与来源上，组装契约的那段代码因此只有
    一份；后果是新增一种结局时只需多一个构造它的工厂函数，不会把契约漏填成两套。
    """

    occurred_at: datetime
    started: float
    status: UsageStatus
    source: UsageSource
    input_tokens: int
    output_tokens: int
    cached_tokens: int | None
    total_tokens: int


def _completed_outcome(result: Any, *, started: float, occurred_at: datetime) -> _CallOutcome:
    """正常返回的结局：token 取上游报的，来源按「拿到没拿到用量对象」判定。"""

    # 上游有没有给用量对象，决定「自报」还是「缺失」。给了就是自报，哪怕报回来的总量都是 0；
    # 没给就把 token 记 0、把来源记缺失——两者靠来源这一列区分。
    usage = getattr(result, "usage_metadata", None)
    input_tokens = _usage_token(usage, "input_tokens") or 0
    output_tokens = _usage_token(usage, "output_tokens") or 0
    reported_total = _usage_token(usage, "total_tokens")
    return _CallOutcome(
        occurred_at=occurred_at,
        started=started,
        status=UsageStatus.COMPLETED,
        source=UsageSource.UPSTREAM if usage is not None else UsageSource.MISSING,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=_cache_read_tokens(usage),
        total_tokens=reported_total if reported_total is not None else input_tokens + output_tokens,
    )


def _failed_outcome(*, started: float, occurred_at: datetime) -> _CallOutcome:
    """异常的结局：状态失败、token 全 0、来源缺失。

    耗时算到异常发生的这一刻（组装契约时取单调时钟），所以被取消的那次不会把取消之后的时间
    也算进去。上游报错、超时、被取消三种结束方式在这里是同一种结局，因为它们对账本而言就是
    同一件事：这次调用没拿到用量。
    """

    return _CallOutcome(
        occurred_at=occurred_at,
        started=started,
        status=UsageStatus.FAILED,
        source=UsageSource.MISSING,
        input_tokens=0,
        output_tokens=0,
        cached_tokens=None,
        total_tokens=0,
    )


class UsageRecordingChatModel(BaseChatModel):
    """把一次模型调用记成一条 ``UsageRecord`` 的包装模型。

    它不是装饰器式的「加个回调」：LangChain 的回调拿不到这次调用属于哪个账号、哪次运行，
    而运行身份必须从当前运行的上下文读（见 ``_resolve_identity``），所以采集点只能落在
    调用本身外面。

    装配时用 ``wrap_with_usage_recording`` 而不是直接构造本类。

    Attributes:
        target: 被包装的真实模型，也是所有可观测属性的来源。工具绑定产生新对象时它保持不变。
        collector: 记录一条事实的目标；缺省装配给的是空实现。
        runner: 本次调用实际要跑的对象。未绑定过工具时是 ``target``；绑定过工具或参数时是
            底层产出的绑定结果，这样工具才能真的到达底层模型。它只影响「怎么调」，
            不影响属性委托与模型名。
    """

    target: BaseChatModel = Field(exclude=True, repr=False)
    collector: Any = Field(exclude=True, repr=False)
    runner: Runnable | None = Field(default=None, exclude=True, repr=False)

    def __init__(
        self,
        *,
        target: BaseChatModel,
        collector: UsageCollector,
        runner: Runnable | None = None,
        **kwargs: Any,
    ) -> None:
        """构造包装，并把 ``profile`` 对齐到被包装模型。

        ``collector`` 在这里声明成 ``UsageCollector`` 而不是字段的 ``Any``：字段类型用协议
        会让 Pydantic 生成不出校验器，而构造入口是唯一需要类型提示的地方。

        ``profile`` 是 ``BaseChatModel`` 的**字段**（不是属性），所以它只能在这里按值对齐，
        不能在下面写成委托属性——字段与同名属性撞在一起时，属性会被当成字段默认值。
        """

        kwargs.setdefault("profile", target.profile)
        super().__init__(target=target, collector=collector, runner=runner, **kwargs)

    # 1、公开调用入口：记账只发生在这里。

    def invoke(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """调用模型，正常返回或抛异常都记一条。

        Raises:
            BaseException: 底层模型抛出的一切异常原样向上传播。记账在 ``except BaseException``
                里完成——取消抛的是 ``CancelledError``，它是 ``BaseException`` 而不是
                ``Exception``，用后者接不住；记完无条件重新抛出，吞掉它会把「已停止」变成
                「继续跑」。
        """

        # 1、进入包装层的时刻与单调时钟起点。发生时刻取 UTC，耗时用单调时钟：墙钟会被
        #    校时改跳，用它的差值算耗时会得到负数或者跳变。
        occurred_at = datetime.now(UTC)
        started = time.monotonic()

        # 2、交给实际要跑的对象。config 原样下传——往里塞一份空的 callbacks、或者传
        #    stream=False，都会真的把流式关掉，而那正好是生产入口依赖的东西。
        try:
            result = self._call_target().invoke(input, config, stop=stop, **kwargs)
        except BaseException:
            self._deliver(_failed_outcome(started=started, occurred_at=occurred_at))
            raise

        # 3、正常返回：记一条完成，然后原样把结果交回去。
        self._deliver(_completed_outcome(result, started=started, occurred_at=occurred_at))
        return result

    async def ainvoke(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """``invoke`` 的异步版本；生产入口（图与中间件）走的就是这一条。

        Raises:
            BaseException: 与 ``invoke`` 相同，包括被取消时抛出的 ``CancelledError``。
        """

        occurred_at = datetime.now(UTC)
        started = time.monotonic()
        try:
            result = await self._call_target().ainvoke(input, config, stop=stop, **kwargs)
        except BaseException:
            self._deliver(_failed_outcome(started=started, occurred_at=occurred_at))
            raise
        self._deliver(_completed_outcome(result, started=started, occurred_at=occurred_at))
        return result

    def stream(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """流式入口，原样转发给底层模型。

        这里不记账，失败也不记：本期的取数路径是上面的 ``invoke`` / ``ainvoke``（生产入口用
        ``graph.astream`` 的 ``messages`` 模式，靠回调处理器让底层走流式，它调用的仍是
        ``ainvoke``）。覆盖本方法的目的是让外部直接调 ``stream`` / ``astream`` 时不撞上基类
        未实现的生成钩子，而不是在这条路上取数。它的代价要如实记下：若将来有别的链路直接调
        ``stream``，那条链路上的调用不在账本里。
        """

        return self._call_target().stream(input, config, stop=stop, **kwargs)

    def astream(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """``stream`` 的异步版本，同样是原样转发；理由见 ``stream``。"""

        return self._call_target().astream(input, config, stop=stop, **kwargs)

    # 2、绑定：既要真的把工具交给底层，又要让绑定之后的调用仍然经过包装。

    def bind(self, **kwargs: Any) -> Runnable[Any, Any]:
        """绑定参数，返回仍然记账的包装。

        没有参数时直接返回自身：``create_agent`` 在没有工具可用时会对模型调一次空 ``bind``，
        为它多套一层绑定没有意义。

        Returns:
            参数绑定在底层产出上的新包装。
        """

        if not kwargs:
            return self
        return self._with_runner(self.target.bind(**kwargs))

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, Any]:
        """把工具绑定到**底层**模型上，再把绑定结果重新挂回包装。

        两边都不能简化：

        * 在绑定方法里返回自身，工具就丢了——模型不再调用检索工具，而且不报错；
        * 返回底层的绑定结果，记账就丢了——从这一轮开始，这次运行剩下的模型调用一条也不会
          被记录。

        还要容得下底层根本不接受绑定的情形（离线测试的假模型直接返回自己）：那时退回
        「不绑工具、仍由包装记账」，而不是把自己换成底层对象。

        Args:
            tools: 要交给模型判断是否调用的工具。
            **kwargs: 绑定参数（如 ``tool_choice``），原样下传。

        Returns:
            工具绑定在底层产出上的新包装；底层不接受绑定时返回自身。
        """

        bound = self.target.bind_tools(tools, **kwargs)
        if bound is self.target:
            return self
        return self._with_runner(bound)

    # 3、可观测属性：逐项显式委托，不做兜底转发。

    @property
    def _llm_type(self) -> str:
        """模型类型标识。中间件靠它判断 provider（如摘要压缩的 token 计数口径）。"""

        return self.target._llm_type

    @property
    def _identifying_params(self) -> Mapping[str, Any]:
        """缓存与标识用的参数。"""

        return self.target._identifying_params

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        """LangSmith 参数；中间件会读它拿 provider 与模型名。"""

        return self.target._get_ls_params(stop=stop, **kwargs)

    @property
    def model_name(self) -> str | None:
        """模型名（OpenAI 兼容分支）。"""

        return self.target.model_name

    @property
    def model(self) -> str | None:
        """模型名（Ollama 分支把名字放在这个字段上）。"""

        return self.target.model

    @property
    def default_headers(self) -> dict[str, str] | None:
        """默认请求头。"""

        return self.target.default_headers

    @property
    def request_timeout(self) -> Any:
        """单次请求超时。"""

        return self.target.request_timeout

    @property
    def temperature(self) -> Any:
        """采样温度。"""

        return self.target.temperature

    @property
    def max_retries(self) -> Any:
        """客户端自带重试次数（本项目为 0，重试统一由中间件负责）。"""

        return self.target.max_retries

    @property
    def model_kwargs(self) -> dict[str, Any]:
        """provider 特有的请求参数（OpenAI 兼容分支）。"""

        return self.target.model_kwargs

    @property
    def use_responses_api(self) -> bool | None:
        """是否走 Responses API（OpenAI 兼容分支的构造参数）。"""

        return self.target.use_responses_api

    @property
    def client_kwargs(self) -> dict[str, Any]:
        """provider 特有的客户端构造参数（Ollama 分支的超时与请求头都在这里）。"""

        return self.target.client_kwargs

    # 4、基类要求的生成钩子。

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """接到公开调用入口，而不是另写一条不受记账覆盖的取数路径。

        图与中间件都不经过这里（它们调 ``invoke`` / ``ainvoke``）；只有直接调
        ``generate`` / ``agenerate`` 时才用得上，保持与未包装时一致即可。
        """

        result = self.invoke(messages, stop=stop, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=result)])

    # 5、内部实现。

    def _call_target(self) -> Runnable[Any, Any]:
        """本次调用实际要跑的对象：绑定过就是绑定结果，否则是底层模型。"""

        return self.runner if self.runner is not None else self.target

    def _with_runner(self, runner: Runnable[Any, Any]) -> "UsageRecordingChatModel":
        """换一个调用目标、保持同一个包装身份，用于 ``bind`` / ``bind_tools``。"""

        return UsageRecordingChatModel(
            target=self.target,
            collector=self.collector,
            runner=runner,
            profile=self.profile,
        )

    def _deliver(self, outcome: "_CallOutcome") -> None:
        """把一次调用的结局装成契约交给采集器。

        本方法**不抛异常**：它跑在对话链路上，采集失败绝不能变成对话失败。能失败的地方
        （取身份、取模型名、构造契约、采集器本身）统一在最后兜住并留一条日志——这里漏出去
        一个异常，代价是用户这一次提问直接失败。
        """

        try:
            self.collector.record(self._build_record(outcome))
        except Exception:
            logger.exception("用量记录失败，已忽略以免影响对话")

    def _build_record(self, outcome: "_CallOutcome") -> UsageRecord:
        """把结局翻成契约。

        成功与失败共用一条组装路径，差别只在 ``outcome`` 提供的三个量：状态、用量来源与四个
        token 数。失败时 token 全为 0、来源为缺失，而**账号／会话／运行仍然照取**：失败的那次
        调用同样属于某次提问，丢了归属就再也追不回来。
        """

        # 1、运行身份可能取不到。取不到只把账号／会话／运行记成缺失，**不**把这次调用
        #    判成失败：只有拿不到用量才是「用量缺失」。
        user_id, thread_id, run_id = self._resolve_identity()

        return UsageRecord(
            occurred_at=outcome.occurred_at,
            duration_ms=max(0, int((time.monotonic() - outcome.started) * 1000)),
            status=outcome.status,
            source=outcome.source,
            model_name=self._resolve_model_name(),
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            cached_tokens=outcome.cached_tokens,
            total_tokens=outcome.total_tokens,
        )

    def _resolve_model_name(self) -> str | None:
        """取这次调用实际用的模型名。

        优先用 ``_get_ls_params()['ls_model_name']``：两个 provider 都实现它，值就是配的那个
        模型名（主模型与备用模型因此能分辨），而且不依赖上游在响应里回什么。取不到时记缺失，
        不让它把整条记录拖没。
        """

        try:
            name = self.target._get_ls_params().get("ls_model_name")
        except Exception:
            return None
        return name if isinstance(name, str) and name else None

    def _resolve_identity(self) -> tuple[Any, Any, Any]:
        """从当前运行的上下文里读账号、会话与运行三个引用。

        模型层拿不到 ``ModelRequest``，只能靠 ``langgraph.runtime.get_runtime()`` 读当前运行
        的上下文；模型节点、以及中间件内部发起的调用（历史摘要压缩）在这一点上一样。

        取不到时返回三个 ``None``（归属未知）。**归属缺失必须留一条日志**：三条查询接口都按
        账号过滤，账号为空的记录谁都查不到，不记就没有人知道账本正在少算。取身份失败本身只在
        这里记 warning，不影响这次调用的状态。
        """

        context: Any = None
        try:
            runtime = get_runtime()
            context = runtime.context if runtime is not None else None
        except Exception as exc:
            logger.warning(
                "用量记录读取运行上下文失败，本次调用归入归属未知 error_type=%s",
                type(exc).__name__,
            )

        user_id = getattr(context, "user_id", None)
        thread_id = getattr(context, "thread_id", None)
        run_id = getattr(context, "run_id", None)
        if user_id is None:
            logger.warning(
                "用量记录缺少账号归属，这条记录谁都查不到 thread_id=%s run_id=%s",
                thread_id,
                run_id,
            )
        return user_id, thread_id, run_id


def wrap_with_usage_recording(
    model: BaseChatModel,
    collector: UsageCollector,
) -> UsageRecordingChatModel:
    """把模型包一层，让它的每次调用都交给 ``collector``。

    Args:
        model: 要包装的模型客户端，可以是主模型也可以是备用模型。
        collector: 记录的接收方；生产注入真实采集器，缺省装配注入空实现。

    Returns:
        包装后的模型，可原样交给 ``create_agent`` 与 ``build_agent_middleware``。
    """

    return UsageRecordingChatModel(target=model, collector=collector)


def _usage_token(usage: Any, key: str) -> int | None:
    """从上游用量对象里取一个 token 数。

    上游没用用量对象、或者没报这一项时返回 ``None``——调用方据此把「没报」与「报了 0」
    分开，不能在这里顺手补 0，否则两者在记录里就再也分不出来了。
    """

    if not isinstance(usage, Mapping):
        return None
    value = usage.get(key)
    return value if isinstance(value, int) else None


def _cache_read_tokens(usage: Any) -> int | None:
    """从上游用量对象的输入明细里取缓存命中 token。

    键名以 ``cache_read`` 结尾（不同服务档位会带前缀），所以只能按后缀匹配：写死任何一个
    具体键名，换个档位就取不到，而取不到的后果是这一列从「有值」变成「缺失」，看起来像上游
    没报缓存。

    Returns:
        缓存命中 token 数；上游没报这一项时返回 ``None``（不是 0）。
    """

    if not isinstance(usage, Mapping):
        return None
    details = usage.get("input_token_details")
    if not isinstance(details, Mapping):
        return None
    for key, value in details.items():
        if isinstance(key, str) and key.endswith("cache_read") and isinstance(value, int):
            return value
    return None


__all__ = ["UsageRecordingChatModel", "wrap_with_usage_recording"]
