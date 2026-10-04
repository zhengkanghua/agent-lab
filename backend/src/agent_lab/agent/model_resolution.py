"""按当前运行的上下文解析当轮模型客户端，并把每一次调用转交给它。

会话里选中的模型进的是运行上下文（``AgentContext.llm_model``），而模型层够不到请求对象——
它只能通过 ``langgraph.runtime.get_runtime()`` 拿到当前运行，用量采集点读账号与会话用的是
同一个机制。本模块就是那个消费方：装配期不读环境变量、不发请求、不连库（所以模型目录为空
时进程照样起得来），每次调用时才按当轮那个模型的 id 去目录里解析客户端。

**解析带缓存**：同一个模型只构造一次客户端，之后复用（不然每次模型调用都要新建一套连接池
并重做 TLS 握手）。键是模型 id，命中后信任 60 秒，超时只重新校验那一行的内容——内容没变就
继续用同一个客户端，变了才重建，行没了才淘汰（细节见 ``CatalogModelResolver``）。可用性判定
**不经过这里**：刚停用的模型必须立刻选不到，那是开始运行之前那道 HTTP 门的事，它直接读目录表。

**取不到就明确失败**，绝不静默拿一个没人选的模型去回答。这是「不静默换模型」那条决策在
运行期的落点：开始运行之前那道 HTTP 门已经判过可用性，这里只负责「当轮那个模型」本身。

**这里不重新判可用性。** 一条在途运行被排空、由别的进程接手时，用的仍是提问消息里冻结的
那一份快照，而那道 HTTP 门不会为接手再走一遍——那期间模型可能已经被停用，接手这一轮依旧要
用它跑完（要停就停整个会话，或让上游自己失败）。所以本模块只读目录里的那一行，不看它的
启用位。

**为什么包装而不是中间件**：交给图的客户端必须是一个「按运行上下文解析」的包装，这样摘要
那一次调用也跟随会话模型（摘要中间件拿到的是同一个装配期对象），并且不必新增带
``before_model`` 的中间件——那个会让图的入口节点改名，部署撞上在途运行就接不上手。

**包装层次**：本模块的 ``ResolvingChatModel`` 在用量采集包装的**里面**
（``runtime.py`` 里塞进图的是 ``wrap_with_usage_recording(ResolvingChatModel(...))``）。
采集靠被包对象读模型名与用量，所以解析包装要把可观测属性按项委托给当轮解析到的客户端；
反过来，解析包装与它构造出来的客户端都不再包采集，否则摘要那次调用会被记两条。
"""

import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langgraph.runtime import get_runtime
from pydantic import Field

from agent_lab.agent.chat_model import build_chat_model
from agent_lab.agent.limits import (
    MODEL_CLIENT_CACHE_MAX_ENTRIES,
    MODEL_CLIENT_CACHE_TTL_SECONDS,
)
from agent_lab.config.llm import LlmProvider, LlmSettings
from agent_lab.repositories.llm_model_repository import LlmModelRepository
from agent_lab.services.llm_credential_cipher import CredentialCipher, build_credential_cipher


class RunModelUnresolvedError(RuntimeError):
    """一次运行解析不出当轮要用的模型客户端。

    两种情况共用一个异常：运行上下文里没有模型（这条运行没人选过模型，或调用点根本没在图上
    跑），以及目录里已经没有当轮那个 id 那一行（本表只有停用、没有删除，所以这是不变量被
    破坏）。两者对使用者是同一件事——这一次运行没有可用的模型，绝不能换一个来回答。

    它刻意不是 ``AgentError``：错误契约表里没有对应规则，``AgentError`` 的约定是「一定查得到
    ``code``」，而这里没有合适的既有 ``code``，也不该为此新开一条面向用户的文案。它落到
    ``agent_internal_error`` 兜底（不可重试的 500），由流层的日志指出是哪一次运行、什么原因。
    """


class ModelClientResolver(Protocol):
    """按模型 id 给出一个可直接调用的客户端。"""

    async def resolve_client(self, model_id: UUID) -> BaseChatModel:
        """返回当轮那个模型的客户端；解析不出来时抛 ``RunModelUnresolvedError``。"""
        ...


@dataclass
class _CachedClient:
    """缓存里的一条：构造好的客户端、它对应的那一行内容、以及那一行被校验的时刻。

    ``fingerprint`` 是「那一行内容」的指纹（见 ``_catalog_fingerprint``），``checked_at`` 是
    最近一次拿它跟目录表核对过的时刻；两者都只在本模块内部用，所以是私有形状。
    """

    client: BaseChatModel
    fingerprint: tuple[Any, ...]
    checked_at: float


class CatalogModelResolver:
    """按 id 读模型目录里的那一行渠道，据它构造客户端，并按 id 缓存。

    **这是「解析到一个客户端」的唯一一处**：接入类型、地址、凭据与上游模型名都从这里出去，
    缓存也就只能加在这里，别处不得再解析。

    **缓存三条语义**（对应 ADR 0046 的「未命中」与「变旧」分开处理）：

    - 未命中就直接查库并构造，所以刚新建的模型立刻可用，没有生效延迟；
    - 命中后信任 ``MODEL_CLIENT_CACHE_TTL_SECONDS``，这段时间内不查库；
    - 过期后只重新校验那一行的内容：内容没变继续复用**同一个**客户端（无条件重建会让一个被
      频繁使用的模型每分钟丢一次连接池与 TLS 连接），内容变了才重建，行没了则淘汰。

    比哪几项见 ``_catalog_fingerprint``；**启用位不在其中**。

    **并发**：本类假定整个实例跑在一个事件循环里。API 进程里多个请求会同时解析，而这个类
    的入口全是异步的（``resolve_client``），所以缓存用普通的 ``OrderedDict`` 就够——
    读缓存、写缓存这两段之间没有 ``await``，不会被别的请求插进来；唯一的 ``await``（读目录表）
    两侧各读一次缓存，见 ``resolve_client`` 的注释。
    """

    def __init__(
        self,
        *,
        settings: LlmSettings,
        session_factory: Any,
        cipher: CredentialCipher | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """记住进程级参数与 session 工厂，不建连、不查库、不读环境变量。

        Args:
            settings: 进程级 LLM 配置（温度、超时、User-Agent）。
            session_factory: 进程级 session 工厂；每次需要读目录时开一个短会话，读完就还。
            cipher: 现成的加解密器；省略时在**第一次真的要用凭据**时按环境构造（见
                ``_credential_cipher``）。测试注入它是为了不依赖环境变量。
            clock: 取「当前时间」的函数，返回单调递增的秒数。默认 ``time.monotonic``；
                测试注入一个可推进的时钟，于是不必真等 60 秒，也不必去断言缓存内部字段。
        """

        self._settings = settings
        self._session_factory = session_factory
        self._cipher = cipher
        self._clock = clock
        # 进程级的一份缓存：按最近使用顺序排列，超出上界时从头部（最久未用）淘汰。
        self._clients: OrderedDict[UUID, _CachedClient] = OrderedDict()

    async def resolve_client(self, model_id: UUID) -> BaseChatModel:
        """给出当轮那个模型的客户端：命中缓存就直接给，否则读目录并按需重建。

        Args:
            model_id: 当轮那个模型条目的 id。

        Returns:
            按那一行渠道配置构造出来的客户端；同一个 id 在内容没变时返回的是同一个对象。

        Raises:
            RunModelUnresolvedError: 目录里没有这一行（同时把这条缓存淘汰掉）。
            LlmConfigurationError: 接入类型要求凭据但这一行没有凭据。
            LlmCredentialKeyUnavailableError: 凭据解不开（主密钥缺失或换过）。

        Notes:
            未命中或过期时执行一次 PostgreSQL 读查询；真的重建时才多一次内存解密（凭据的密文
            参与指纹比较，所以「只是校验内容」不必解密）。**不判可用性**：接手一条在途运行时
            必须仍用快照里那个模型跑完（见模块说明）。凭据明文只交给客户端构造函数。
        """

        now = self._clock()
        cached = self._clients.get(model_id)
        if cached is not None and now - cached.checked_at < MODEL_CLIENT_CACHE_TTL_SECONDS:
            self._clients.move_to_end(model_id)
            return cached.client

        # 从这里到本方法结束只有下面这一次 await：之后对缓存的读改写不会被别的请求插进来，
        # 所以普通字典就够，不需要锁。也正因为中间会 await，下面做完 I/O 要重新看一眼缓存——
        # 并发解析同一个模型时，先返回的那一个已经建好了客户端，后到的直接用它的。
        async with self._session_factory() as session:
            row = await LlmModelRepository(session).get_model_with_provider(model_id)

        now = self._clock()
        if row is None:
            self._clients.pop(model_id, None)
            raise RunModelUnresolvedError(
                "模型目录里已经查不到当轮选定的那个模型，不能用别的模型替它回答。"
            )
        record, provider = row
        fingerprint = _catalog_fingerprint(record, provider)
        cached = self._clients.get(model_id)
        if cached is not None and cached.fingerprint == fingerprint:
            # 内容没变：只把「校验过」的那一刻往后推，客户端（连接池与 TLS 连接）原样留着。
            cached.checked_at = now
            self._clients.move_to_end(model_id)
            return cached.client
        client = build_chat_model(
            self._settings,
            provider=LlmProvider(provider.provider),
            base_url=provider.base_url,
            credential=self._credential(provider.credential_ciphertext),
            model=record.upstream_model_name,
        )
        self._remember(
            model_id,
            _CachedClient(client=client, fingerprint=fingerprint, checked_at=now),
        )
        return client

    def _remember(self, model_id: UUID, entry: _CachedClient) -> None:
        """写入缓存，并把最久未用的那些淘汰到上界之内。

        **只看最近使用顺序，不看启用位**：停用的模型还要支撑一条在途运行跑完，淘汰它等于让
        接手那一轮重新构造客户端；而真正不再被选中的条目会自己排到队头，先被淘汰。
        淘汰只是把它从字典里移走，不会动已经在用它的人：那一次运行的调用栈上还引用着这个对象，
        它照常跑完，只是下一次解析会重新构造。
        """

        self._clients[model_id] = entry
        self._clients.move_to_end(model_id)
        while len(self._clients) > MODEL_CLIENT_CACHE_MAX_ENTRIES:
            self._clients.popitem(last=False)

    def _credential(self, ciphertext: str | None) -> str:
        """解出这条渠道的凭据明文；没有凭据时返回空串（本地的接入类型不需要凭据）。"""

        if not ciphertext:
            return ""
        return self._credential_cipher().decrypt(ciphertext)

    def _credential_cipher(self) -> CredentialCipher:
        """按需构造加解密器，并留在本实例上复用。

        密钥只在这一刻才被读取和校验：没有凭据要解的渠道（例如一条 ollama 渠道）不会碰它，
        所以没配主密钥的部署照样跑得动这类渠道，装配期也不读任何环境变量。
        """

        if self._cipher is None:
            self._cipher = build_credential_cipher()
        return self._cipher


def _catalog_fingerprint(record: Any, provider: Any) -> tuple[Any, ...]:
    """一条目录行里「改了就必须重建客户端」的那几项。

    地址、接入类型、凭据、上游模型名、上下文窗口。凭据比的是**密文**：换凭据必然换密文（Fernet
    每次加密的密文都不同），而比密文就不必在每次校验时解密。

    **启用位不在其中**：停用只是「不再被新的选择解析到」，那道 HTTP 门会先拦住；而接手一条在途
    运行还要用同一个客户端跑完，这时重建没有意义，反而把连接池丢掉。

    上下文窗口不参与客户端构造（它在运行期从运行上下文读），但它同样是「这一条模型配置」的一部分：
    预算与压缩口径跟着它变，所以改了也应该换上按新窗口构造的客户端，而不是让旧对象继续被用。
    """

    return (
        provider.base_url,
        provider.provider,
        provider.credential_ciphertext,
        record.upstream_model_name,
        record.context_window,
    )


# 本次调用解析到的客户端，按协程隔离。
#
# **为什么不挂在包装实例上**：图是进程级共享的，两个会话用不同模型时，挂在实例上的那份会被
# 对方覆盖。上下文变量按协程隔离，正是为了这件事——中间件那边读当轮窗口（``_CONTEXT_WINDOW``）
# 用的是同一套机制。
#
# **为什么设置之后不复位**：用量采集在调用返回**之后**才取模型名
# （见 ``usage_recording._resolve_model_name``），复位会让那一列静默变成缺失，而现有失败
# 路径里没有一条能发现它。同一协程里的下一次调用会重新设置它，所以「当轮」这个语义仍然成立。
_CURRENT_CLIENT: ContextVar[BaseChatModel | None] = ContextVar(
    "agent_run_chat_model", default=None
)


class ResolvingChatModel(BaseChatModel):
    """装配期不解析、每次调用按当前运行的上下文解析并转交的客户端包装。

    ``profile`` 是 ``BaseChatModel`` 的**字段**（不是属性），所以它只能在构造时按值给出、
    不能在下面写成委托属性——字段与同名属性撞在一起时，属性会被当成字段默认值。它在这里
    留空：运行期真正用的窗口由摘要中间件从按协程隔离的上下文变量读
    （见 ``agent.middleware`` 的 ``_get_profile_limits``），而装配期摘要中间件构造校验读的
    那份占位窗口由 ``_with_placeholder_window`` 补在**最外层**的采集包装上。

    Attributes:
        resolver: 解析来源；生产是 ``CatalogModelResolver``，测试注入替身。
        tools: 装配期绑上的工具；到调用时才交给当轮客户端，因为那时还没有客户端可用。
    """

    resolver: Any = Field(exclude=True, repr=False)
    tools: tuple[Any, ...] | None = Field(default=None, exclude=True, repr=False)
    tools_kwargs: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
    bind_kwargs: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)

    def __init__(
        self,
        *,
        resolver: ModelClientResolver,
        tools: Sequence[Any] | None = None,
        tools_kwargs: dict[str, Any] | None = None,
        bind_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """构造包装；参数都是进程级对象，不含任何「本次运行」的值。

        ``resolver`` 在这里声明成 ``ModelClientResolver`` 而不是字段的 ``Any``：字段类型用协议
        会让 Pydantic 生成不出校验器，而构造入口是唯一需要类型提示的地方。
        """

        super().__init__(
            resolver=resolver,
            tools=None if tools is None else tuple(tools),
            tools_kwargs=dict(tools_kwargs or {}),
            bind_kwargs=dict(bind_kwargs or {}),
            **kwargs,
        )

    # 1、公开调用入口：解析只发生在这里。

    def invoke(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """同步入口：本包装没有它，明确失败而不是静默走另一条路。

        Raises:
            RunModelUnresolvedError: 本包装只支持异步入口。
        """

        self._raise_async_only()

    async def ainvoke(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """生产入口（图与中间件都走这一条）；先解析当轮客户端，再原样转交。

        ``config`` 原样下传——往里塞一份空的 callbacks、或者关掉流式，都会真的改掉底层行为，
        而那正是生产入口依赖的东西。
        """

        runnable = self._apply_bindings(await self._resolve())
        return await runnable.ainvoke(input, config, stop=stop, **kwargs)

    def stream(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """同步流式入口：理由与 ``invoke`` 相同。

        Raises:
            RunModelUnresolvedError: 本包装只支持异步入口。
        """

        self._raise_async_only()

    async def astream(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """``stream`` 的异步版本。解析要先 ``await``，所以这里是一个异步生成器。"""

        runnable = self._apply_bindings(await self._resolve())
        async for chunk in runnable.astream(input, config, stop=stop, **kwargs):
            yield chunk

    # 2、绑定：装配期只记下来，调用时才交给当轮客户端。

    def bind(self, **kwargs: Any) -> Runnable[Any, Any]:
        """绑定参数，返回仍然按当轮模型解析的包装。

        没有参数时返回自身：``create_agent`` 在没有工具可用时会对模型调一次空 ``bind``，
        为它多套一层绑定没有意义。有参数时记在副本上——装配期没有客户端可绑。
        """

        if not kwargs:
            return self
        return self._with(bind_kwargs=dict(kwargs))

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, Any]:
        """把工具记在副本上，调用时交给当轮解析到的那个客户端。

        不能在装配期绑定：那时既没有客户端，也不知道这一轮用的是哪个模型。
        """

        return self._with(tools=tuple(tools), tools_kwargs=dict(kwargs))

    # 3、可观测属性：逐项显式委托，不做兜底转发。

    @property
    def _llm_type(self) -> str:
        """模型类型标识。

        **这一项只能是静态值，不委托给当轮客户端**：摘要中间件在**装配期**用它挑近似 token
        计数器的口径（``_get_approximate_token_counter`` 读它判 anthropic），而那一刻还没有
        任何运行、也解析不出客户端。本项目只支持 openai 兼容与 ollama 两种接入类型，两者都
        走默认口径，所以静态值不改变任何行为。
        """

        return "resolving-chat-model"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        """LangSmith 参数；用量采集点从这里取这次调用的上游模型名。

        一次调用还没解析过客户端时返回空字典而不是抛错：摘要中间件会在「这一轮还没开始调
        模型」时读一次它（上游用 ``ls_provider`` 比对上一轮自报的用量是否已经超线），那时没有
        客户端可用。代价是那条「按上一轮自报用量提前压缩」的路径在我们这里不成立，压缩一律
        按窗口占比触发。
        """

        client = _CURRENT_CLIENT.get()
        if client is None:
            return {}
        return client._get_ls_params(stop=stop, **kwargs)

    @property
    def model_name(self) -> str | None:
        """模型名（OpenAI 兼容分支把名字放在这个字段上）。"""

        return self._current_client().model_name

    @property
    def model(self) -> str | None:
        """模型名（Ollama 分支把名字放在这个字段上）。"""

        return self._current_client().model

    @property
    def default_headers(self) -> dict[str, str] | None:
        """默认请求头。"""

        return self._current_client().default_headers

    @property
    def request_timeout(self) -> Any:
        """单次请求超时。"""

        return self._current_client().request_timeout

    @property
    def temperature(self) -> Any:
        """采样温度。"""

        return self._current_client().temperature

    @property
    def max_retries(self) -> Any:
        """客户端自带重试次数（本项目为 0，重试统一由中间件负责）。"""

        return self._current_client().max_retries

    @property
    def model_kwargs(self) -> dict[str, Any]:
        """provider 特有的请求参数（OpenAI 兼容分支）。"""

        return self._current_client().model_kwargs

    @property
    def use_responses_api(self) -> bool | None:
        """是否走 Responses API（OpenAI 兼容分支的构造参数）。"""

        return self._current_client().use_responses_api

    @property
    def client_kwargs(self) -> dict[str, Any]:
        """provider 特有的客户端构造参数（Ollama 分支的超时与请求头都在这里）。"""

        return self._current_client().client_kwargs

    # 4、基类要求的生成钩子。

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """基类要求的生成钩子；本包装没有同步入口，所以这里也明确失败。

        图与中间件走的都是 ``ainvoke``（它们本来就是异步的），所以这个方法在生产路径上不会
        被调；直接调 ``generate`` / ``agenerate`` 的人会拿到一个说得清的错误。

        Raises:
            RunModelUnresolvedError: 本包装只支持异步入口。
        """

        self._raise_async_only()

    # 5、内部实现。

    async def _resolve(self) -> BaseChatModel:
        """解析当轮客户端，并把它放进当前协程的上下文变量。"""

        client = await self.resolver.resolve_client(self._current_model_id())
        _CURRENT_CLIENT.set(client)
        return client

    def _raise_async_only(self) -> None:
        """同步入口一律失败。

        解析要读一次目录（异步），同步栈里没办法等它；而本项目塞进图的中间件里有一个只实现
        了异步钩子，所以整张图本来就不能同步跑。与其在这里造一条跑不到的同步路径，不如让它
        明确失败。
        """

        raise RunModelUnresolvedError(
            "解析包装只支持异步入口（ainvoke / astream）：当轮模型要按运行上下文异步解析。"
        )

    def _current_client(self) -> BaseChatModel:
        """取本协程解析到的客户端。

        Raises:
            RunModelUnresolvedError: 本协程还没有解析过（没有调用在途）。宁可明确失败，也不
                返回 None 让读它的人以为模型真的没有名字。
        """

        client = _CURRENT_CLIENT.get()
        if client is None:
            raise RunModelUnresolvedError(
                "解析包装的可观测属性只在一次调用期间有值；本协程还没有解析过当轮模型。"
            )
        return client

    def _current_model_id(self) -> UUID:
        """从当前运行的上下文里读当轮那个模型条目的 id。

        Raises:
            RunModelUnresolvedError: 运行上下文里没有模型（例如这次运行根本没经过「开始运行
                之前解析」那道门）。
        """

        context = _current_run_context()
        model_id = getattr(getattr(context, "llm_model", None), "id", None)
        if model_id is None:
            raise RunModelUnresolvedError(
                "当前运行的上下文里没有当轮选定的模型，不能凭空挑一个模型来回答。"
            )
        return model_id

    def _apply_bindings(self, client: BaseChatModel) -> Runnable[Any, Any]:
        """把装配期记下的绑定补到当轮客户端上。

        Args:
            client: 当轮解析出来的客户端。

        Returns:
            绑定过参数与工具的可调用对象；没有绑定过任何东西时就是客户端本身。
        """

        runnable: Runnable[Any, Any] = client
        if self.bind_kwargs:
            runnable = runnable.bind(**self.bind_kwargs)
        if self.tools is not None:
            runnable = runnable.bind_tools(self.tools, **self.tools_kwargs)
        return runnable

    def _with(
        self,
        *,
        tools: Sequence[Any] | None = None,
        tools_kwargs: dict[str, Any] | None = None,
        bind_kwargs: dict[str, Any] | None = None,
    ) -> "ResolvingChatModel":
        """造一个只换了绑定内容的副本，用于 ``bind`` / ``bind_tools``。"""

        return ResolvingChatModel(
            resolver=self.resolver,
            tools=tools if tools is not None else self.tools,
            tools_kwargs=tools_kwargs if tools_kwargs is not None else self.tools_kwargs,
            bind_kwargs=bind_kwargs if bind_kwargs is not None else self.bind_kwargs,
            profile=self.profile,
        )


def _current_run_context() -> Any:
    """当前运行的 ``AgentContext``；不在图上跑时返回 ``None``。

    模型层拿不到 ``ModelRequest``，只能靠 ``get_runtime()`` 读当前运行——用量采集点读账号与
    会话用的是同一个入口，理由与写法都见 ``usage_recording._resolve_identity``。
    """

    try:
        runtime = get_runtime()
    except Exception:
        return None
    return runtime.context if runtime is not None else None


def build_run_model(
    *,
    settings: LlmSettings,
    session_factory: Any,
    resolver: ModelClientResolver | None = None,
) -> ResolvingChatModel:
    """构造按当轮模型解析的客户端包装（装配期只构造对象）。

    Args:
        settings: 进程级 LLM 配置，透传给解析出来的客户端构造。
        session_factory: 进程级 session 工厂，供解析时读模型目录。
        resolver: 解析来源；省略时按目录表解析。测试注入替身是为了不连数据库。

    Returns:
        尚未绑定到任何运行的包装，可交给 ``wrap_with_usage_recording``。
    """

    return ResolvingChatModel(
        resolver=resolver
        or CatalogModelResolver(settings=settings, session_factory=session_factory)
    )


__all__ = [
    "CatalogModelResolver",
    "ModelClientResolver",
    "ResolvingChatModel",
    "RunModelUnresolvedError",
    "build_run_model",
]
