"""组装 Agent 的中间件流水线，并把「一次运行的边界」固化在这里。

中间件是 LangGraph 在「模型调用」和「工具调用」外面套的一圈钩子，用来做重试、降级、
限流、历史压缩、未注册工具守卫和错误兜底，这样这些横切关注点不用散落进工具实现和路由里。

**顺序有语义，且和直觉相反**：列表里越靠后的越内层、越先执行，所以重试类必须排在兜底类
之后，否则兜底会在内层先把异常吞掉、重试永远不触发。完整论据和实测数据见
``docs/adr/0005-middleware-order-semantics.md``；改动本模块的列表顺序前先读它。

本模块只组装对象，不调用模型或工具、不执行 I/O、不读环境变量（配置由调用方传入）。
"""

import logging
from datetime import UTC, date, datetime

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRequest,
    ModelRetryMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
    ToolCallRequest,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
    dynamic_prompt,
)
from langchain_core.language_models import BaseChatModel

from agent_lab.agent.context import AgentContext
from agent_lab.agent.errors import ModelResponseInvalidError
from agent_lab.agent.limits import (
    MODEL_CALL_RUN_LIMIT,
    MODEL_RETRY_MAX,
    RETRY_INITIAL_DELAY_SECONDS,
    SUMMARIZATION_KEEP_MESSAGES,
    SUMMARIZATION_TRIGGER_MESSAGES,
    TOOL_CALL_RUN_LIMIT,
    TOOL_RETRY_MAX,
)
from agent_lab.agent.prompts import (
    CURRENT_DATE_PROMPT_TEMPLATE,
    DEFAULT_SYSTEM_PROMPT,
    SUMMARY_PROMPT,
)
from agent_lab.api.error_contract import (
    AGENT_TOOL_ERROR_RULES,
    resolve_error_contract,
)


logger = logging.getLogger(__name__)


def append_current_date(prompt: str, *, today: date | None = None) -> str:
    """在提示词末尾追加当前日期。

    模型不知道今天几号——它只知道训练语料截止在什么时候。不告诉它，「最近」「今天」这类
    相对时间就会以训练截止那个时间点为基准，而语料库里的新闻是持续入库的，两者对不上。

    追加而不是替换，也不参与「默认还是自定义」的选择：日期是环境事实，不是回答规范。用户
    换掉的是后者，不该顺带把「今天几号」也换掉，所以两条路都要过这一步。

    Args:
        prompt: 已经选定的系统提示词（默认的或用户自定义的）。
        today: 当作「今天」的日期；``None`` 表示取 UTC 当天。只为测试能钉住一个固定日期
            而留的参数，生产路径不传——和本模块 ``retry_initial_delay`` 只为测试传 0
            是同一个做法。

    Returns:
        末尾带上日期段的提示词。

    Notes:
        纯内存拼接，不执行 I/O。时区固定 UTC，和 ``published_at`` 的存储时区一致，
        这样模型看到的「今天」与检索时间过滤用的边界是同一套基准。
    """

    current_date = today if today is not None else datetime.now(UTC).date()
    return prompt + CURRENT_DATE_PROMPT_TEMPLATE.format(current_date=current_date.isoformat())


def select_system_prompt(context: AgentContext | None, *, today: date | None = None) -> str:
    """决定一次运行用哪份系统提示词。

    刻意做成只接收 ``AgentContext`` 的纯函数，而不是直接写在中间件里：这样「选哪份提示词」
    这个决策可以被单独测试，不需要伪造 LangChain 的请求对象——那个对象的构造参数属于框架
    内部契约，会随版本变动，拿它当测试夹具等于把测试绑在框架版本上。

    Args:
        context: 本次运行的上下文；``None`` 表示调用方没传（``create_agent`` 允许）。
        today: 透传给 ``append_current_date``，只为测试注入固定日期。

    Returns:
        自定义提示词（没给、给了空白时用内置默认提示词），末尾一律带上当前日期段。

    Notes:
        纯内存判断，不执行 I/O。不记录提示词内容——自定义提示词属于用户输入。
    """

    if context is not None and context.system_prompt and context.system_prompt.strip():
        return append_current_date(context.system_prompt, today=today)
    return append_current_date(DEFAULT_SYSTEM_PROMPT, today=today)


@dynamic_prompt
def resolve_system_prompt(request: ModelRequest[AgentContext]) -> str:
    """在每次模型调用前把上下文里的提示词取出来交给模型。

    这是「进程级共享一个 agent，但每个请求能用自己的提示词」得以成立的地方：提示词不写死
    在编译期，而是每次模型调用时从 ``request.runtime.context`` 里读，因此换提示词不需要
    重新编译图。

    本函数只负责从请求里取出上下文，真正的选择逻辑在 ``select_system_prompt``。

    Args:
        request: 本次模型调用请求；``request.runtime.context`` 是发起这次运行时传入的
            ``AgentContext``。

    Returns:
        本次模型调用使用的系统提示词。

    Notes:
        纯内存读取，不执行 I/O。
    """

    return select_system_prompt(request.runtime.context)


class UnknownToolGuardMiddleware(AgentMiddleware):
    """模型声称要调用一个没注册的工具时，把它变成一个明确的分类失败。

    **为什么需要它**：LangGraph 遇到未注册的工具名时会回一条 ``status="error"`` 的
    ``ToolMessage`` 让模型自己纠正。模型通常纠正不了——它会说「我的环境里没有提供
    search_news」，然后凭空作答或者干脆沉默。用户看到的是「查了资料但不回答」，而日志里
    只有一条工具错误，看不出问题出在工具名上。

    这个场景不是假设：``LLM_MODEL`` 曾被配成 ``auto``（某些中转站的自动路由开关，按每次
    HTTP 调用挑上游），于是一次运行里的某一步被路由到一个自带工具集的端点，模型发出了
    ``Bash(command, description)`` 调用——本项目只有 ``search_news`` 和 ``read_document``。
    有了这道守卫，那次排查会从第一条日志就指向工具名，而不是从前端和流式管道查起。

    **为什么是拦下而不是让模型重试**：重试同样的提示词大概率再犯（见
    ``ModelResponseInvalidError`` 的说明）。宁可一次明确失败，也不要让用户等一轮空转。

    放在流水线最外层（``resolve_system_prompt`` 之后）：它要在工具真正执行之前判断，而且
    抛出的异常必须能穿过 ``ToolErrorMiddleware`` 那层兜底——所以它不能排在兜底的内层。
    """

    def __init__(self, tool_names: frozenset[str]) -> None:
        """记下本次装配注册了哪些工具名。

        Args:
            tool_names: 已注册的工具名集合，由 ``build_agent_middleware`` 从工具列表算出。
        """

        super().__init__()
        self._tool_names = tool_names

    async def awrap_tool_call(self, request: ToolCallRequest, handler):  # type: ignore[no-untyped-def]
        """未注册的工具名直接抛异常，其余原样交给下一层。

        Args:
            request: 本次工具调用请求；只读工具名，不读参数——参数里有用户 query。
            handler: 下一层处理函数。

        Returns:
            下一层的返回值（``ToolMessage`` 或 ``Command``）。

        Raises:
            ModelResponseInvalidError: 工具名不在注册集合里。

        Notes:
            纯内存判断，未注册时不执行任何工具 I/O。日志只记工具名和已注册数量——工具名
            是模型生成的标识符，不含用户提问；已注册的名字不列出来，那份清单在代码里。
        """

        name = request.tool_call["name"]
        if name not in self._tool_names:
            logger.error(
                "模型请求了未注册的工具 tool=%s registered_count=%d",
                name,
                len(self._tool_names),
            )
            raise ModelResponseInvalidError(
                f"模型请求调用未注册的工具 {name}。"
            )
        return await handler(request)


def sanitize_tool_error(exc: Exception, request: ToolCallRequest) -> str:
    """把工具抛出的异常翻成一句安全中文，交回模型继续对话。

    工具失败不该终止整段运行：模型拿到这句话之后可以自己决定是换个检索词再试，还是直接
    告诉用户暂时查不了。所以这里总是返回文案，不返回 ``None``——返回 ``None`` 会让异常
    继续上抛并中断运行。

    Args:
        exc: 工具（经内层 ``ToolRetryMiddleware`` 重试耗尽后）抛出的异常。
        request: 本次工具调用请求；只读其中的工具名，不读参数——参数里有用户 query。

    Returns:
        写进 ``ToolMessage`` 的安全文案。

    Notes:
        只查 ``AGENT_TOOL_ERROR_RULES``，不读 ``str(exc)``、不读异常属性，因此上游细节
        （数据库 URL、API Key、第三方原始响应）不可能顺着模型的回答泄漏给用户。日志同样
        只记异常类型名和错误码。
    """

    rule = resolve_error_contract(exc, AGENT_TOOL_ERROR_RULES)
    logger.warning(
        "Agent 工具调用失败 tool=%s error_type=%s code=%s",
        request.tool_call["name"],
        type(exc).__name__,
        rule.code,
    )
    return f"工具调用失败：{rule.detail}"


def build_agent_middleware(
    *,
    fallback_model: BaseChatModel,
    summarization_model: BaseChatModel,
    tool_names: frozenset[str] = frozenset(),
    retry_initial_delay: float = RETRY_INITIAL_DELAY_SECONDS,
) -> list[AgentMiddleware]:
    """按 ADR 0005 固定的顺序组装中间件流水线。

    Args:
        fallback_model: 主模型重试耗尽后降级使用的客户端。
        summarization_model: 压缩历史消息时使用的客户端；通常与主模型同配置，单独传入
            是为了让测试能只替换其中一个。
        tool_names: 本次装配注册的工具名集合，交给 ``UnknownToolGuardMiddleware``。
            默认空集合意味着「任何工具调用都算未注册」——不给这个参数的调用方本来就没挂
            工具，所以空集合是安全的默认值，而不是「不检查」。
        retry_initial_delay: 重试的首次退避秒数，默认取 ``RETRY_INITIAL_DELAY_SECONDS``。
            留这个口子只为让测试传 0：退避是真的 ``sleep``，而验证「重试了几次、顺序对不对」
            根本不需要等——按默认值跑，一条「主备模型都失败」的用例要白等 6 秒。生产不要传，
            默认值才是安全的那个。

    Returns:
        可直接交给 ``create_agent(middleware=...)`` 的列表，顺序即语义。

    Notes:
        只构造对象，不调用模型、不执行 I/O。两个 retry 中间件的 ``on_failure="error"``
        和列表顺序同属 ADR 0005 的决策：默认值 ``"continue"`` 会让 retry 自己造一条消息
        返回，异常同样到不了外层的降级与兜底。
    """

    return [
        # 1、决定本次用哪份系统提示词。放最外层：它只改写请求，不处理异常。
        resolve_system_prompt,
        # 2、拦住「模型要调一个没注册的工具」。必须在 ToolErrorMiddleware 的**外层**：
        #    排到内层去，它抛的异常会被兜底翻成安全文案交回模型，于是又变成「模型自己
        #    纠正」那条路——那正是它要替换掉的行为。
        UnknownToolGuardMiddleware(tool_names),
        # 3、主模型彻底失败后降级到备用模型。必须在 retry 外层——先让 retry 试完再降级，
        #    反过来会变成「第一次失败就换模型」。
        ModelFallbackMiddleware(fallback_model),
        # 4、模型调用重试。on_failure="error" 才能在重试耗尽时把异常交给外层降级。
        ModelRetryMiddleware(
            max_retries=MODEL_RETRY_MAX,
            initial_delay=retry_initial_delay,
            on_failure="error",
        ),
        # 5、历史过长时压缩成摘要。按消息条数触发而非 token：token 计数依赖分词器，
        #    而中转站背后用哪个分词器我们并不掌握，条数是此处唯一确定的量。
        #    summary_prompt 传中文版，否则默认英文提示词会把对话语言带偏。
        SummarizationMiddleware(
            model=summarization_model,
            trigger=("messages", SUMMARIZATION_TRIGGER_MESSAGES),
            keep=("messages", SUMMARIZATION_KEEP_MESSAGES),
            summary_prompt=SUMMARY_PROMPT,
        ),
        # 6、一次运行内的模型调用次数上限。exit_behavior="end" 表示到顶就收尾，
        #    用户至少拿到已经生成的内容，而不是一个错误。
        ModelCallLimitMiddleware(
            run_limit=MODEL_CALL_RUN_LIMIT,
            exit_behavior="end",
        ),
        # 7、一次运行内的工具调用次数上限。exit_behavior="continue" 表示到顶后不再执行
        #    工具，但模型可以带着已有材料继续作答。
        ToolCallLimitMiddleware(
            run_limit=TOOL_CALL_RUN_LIMIT,
            exit_behavior="continue",
        ),
        # 8、工具异常兜底（外层）。内层重试耗尽后才轮到它，把异常翻成安全文案。
        ToolErrorMiddleware(sanitize_tool_error),
        # 9、工具重试（内层，最先执行）。必须排在兜底之后：反过来兜底会先把异常吞成
        #    ToolMessage，这里永远收不到异常、重试成为死代码。见 ADR 0005。
        ToolRetryMiddleware(
            max_retries=TOOL_RETRY_MAX,
            initial_delay=retry_initial_delay,
            on_failure="error",
        ),
    ]


__all__ = [
    "UnknownToolGuardMiddleware",
    "append_current_date",
    "build_agent_middleware",
    "resolve_system_prompt",
    "sanitize_tool_error",
    "select_system_prompt",
]
