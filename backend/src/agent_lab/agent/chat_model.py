"""按一行渠道配置构造生成式模型客户端，是全项目唯一的 provider 分叉点。

本模块位于 Agent 层最底部：上层只拿到 LangChain 的 ``BaseChatModel`` 抽象，不知道背后是
OpenAI 兼容中转站还是本地 Ollama。新增第三种接入方式只改本文件，中间件、工具、路由和
前端都不动——这是把 provider 差异收敛在一个函数里的全部意义。

**接入类型、地址、凭据、模型名都来自模型目录里的一行渠道**（``llm_providers`` 与
``llm_models``），不再从环境变量读：那四项属于「某一个模型」，多个模型各有各的一份。进程级
参数（温度、超时、User-Agent）仍从 ``config.llm`` 传入，因为它们对所有模型相同。

本模块只构造客户端对象，不发起请求、不校验模型是否真的存在（那要等第一次调用才知道），
也不读环境变量（渠道配置与进程级参数都由调用方传入）。
"""

from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from agent_lab.config.llm import LlmProvider, LlmSettings


class LlmConfigurationError(RuntimeError):
    """渠道配置在语义上不可用，构造客户端前就能判定。

    区别于 ``pydantic.ValidationError``：那个管「字段格式对不对」，本异常管「字段之间的
    组合有没有意义」，比如选了 OpenAI 兼容中转站却没给凭据。这类错误重试无用，必须改配置，
    所以在错误契约里映射成不可重试。
    """


def build_chat_model(
    settings: LlmSettings,
    *,
    provider: LlmProvider,
    base_url: str,
    credential: str,
    model: str,
) -> BaseChatModel:
    """按一行渠道配置构造一个生成式模型客户端。

    Args:
        settings: 进程级 LLM 配置；只取温度、超时与 User-Agent 这三项（它们不属于「某一个
            模型」）。
        provider: 渠道的接入类型，决定用哪个客户端类。
        base_url: 渠道的 API 根地址。
        credential: 渠道凭据明文；接入类型要求凭据时不能为空，不需要凭据时可以为空串。
        model: 上游模型名，直接交给客户端。

    Returns:
        可直接交给 ``create_agent`` 的 ``BaseChatModel``；已绑定超时与采样温度。

    Raises:
        LlmConfigurationError: 接入类型要求凭据但凭据为空。

    Notes:
        只构造对象，不发起任何 HTTP 请求，因此模型名错误、地址不可达、凭据无效都不会在
        这里暴露，而是在第一次模型调用时以上游异常的形式出现，由错误契约层分类。

        ``credential`` 的明文只在这里交给客户端，不写日志、不进异常消息。
    """

    if provider is LlmProvider.OPENAI_COMPATIBLE:
        return _build_openai_compatible_model(
            settings, base_url=base_url, credential=credential, model=model
        )
    return _build_ollama_model(settings, base_url=base_url, credential=credential, model=model)


def _build_openai_compatible_model(
    settings: LlmSettings, *, base_url: str, credential: str, model: str
) -> BaseChatModel:
    """构造 OpenAI 兼容接口的客户端（官方 API、中转站、以及各类兼容网关）。

    Returns:
        绑定 base_url、凭据、超时和温度的 ``ChatOpenAI``。

    Raises:
        LlmConfigurationError: 凭据为空。OpenAI 兼容端点一律要求凭据，空凭据的失败会推迟到
            第一次调用才以 401 出现，而且那时已经是一次真实的用户提问了。
    """

    # 1、在函数里 import 而不是模块顶部：langchain_openai 会连带 import openai SDK，
    #    只用 Ollama 分支的部署没必要为此付启动开销。
    from langchain_openai import ChatOpenAI

    # 2、凭据必须非空。空凭据放过去的话，失败会推迟到第一次调用时以 401 出现。
    secret = credential.strip()
    if not secret:
        raise LlmConfigurationError("接入类型为 openai_compatible 的渠道必须配置凭据。")

    # 3、组装客户端。
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=SecretStr(secret),
        temperature=settings.temperature,
        timeout=settings.request_timeout_seconds,
        default_headers=build_user_agent_headers(settings),
        # 关掉客户端自带重试：重试统一由 ModelRetryMiddleware 负责，两层都开会让实际
        # 请求次数变成乘积（2×3=6），超时和额度都不可预期。
        max_retries=0,
        # 显式打开「流式响应里回传用量」。ChatOpenAI 只在 base_url 与客户端都是默认值时才
        # 默认打开它，而本项目走的是自建 base_url（中转站），于是默认关闭；偏偏 Agent 的
        # 生产入口本来就是流式调用（graph.astream 的 messages 模式会挂上流式回调处理器），
        # 按 OpenAI 官方契约上游此时就不回 usage，用量会被静默记成 0。这里把它变成本项目
        # 的主动契约，而不是依赖上游“宽容”。Ollama 分支没有这个参数，用量来自响应里的计数。
        stream_usage=True,
    )


def _build_ollama_model(
    settings: LlmSettings, *, base_url: str, credential: str, model: str
) -> BaseChatModel:
    """构造本地/自托管 Ollama 的客户端。

    Returns:
        绑定 base_url、超时和温度的 ``ChatOllama``。

    Notes:
        Ollama 原生 API 不要求凭据，所以这里不校验；反向代理需要认证时把渠道凭据当 Bearer
        header 带上，为空则不带。此处与 ``config.ollama_embedding.build_ollama_headers`` 是
        同一套约定，但两者服务的是不同的模型（生成 vs Embedding），所以不共用配置对象。
    """

    from langchain_ollama import ChatOllama

    # 1、Ollama 原生 API 不要求凭据，但反向代理可能要，所以凭据有值就带成 Bearer 头，
    #    为空就不带——不像 OpenAI 分支那样报错。
    secret = credential.strip()
    headers = build_user_agent_headers(settings) or {}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    # 2、组装客户端。headers 为空时不传这个键，让 SDK 用自己的默认值。
    return ChatOllama(
        model=model,
        base_url=base_url,
        temperature=settings.temperature,
        client_kwargs={"headers": headers, "timeout": settings.request_timeout_seconds}
        if headers
        else {"timeout": settings.request_timeout_seconds},
    )


def build_user_agent_headers(settings: LlmSettings) -> dict[str, str] | None:
    """把 ``LLM_USER_AGENT`` 配置转成可直接交给客户端的请求头。

    Args:
        settings: 进程级 LLM 配置。

    Returns:
        含单个 ``User-Agent`` 头的字典；配置留空时返回 ``None``，表示不覆盖 SDK 默认值。

    Notes:
        两个 provider 分支共用本函数，以免出现「换个 provider 就少发一个头」的不对称。
        为什么要能改 User-Agent 见 ``LlmSettings.user_agent`` 的字段说明。
    """

    user_agent = settings.user_agent.strip()
    return {"User-Agent": user_agent} if user_agent else None


__all__ = ["LlmConfigurationError", "build_chat_model", "build_user_agent_headers"]
