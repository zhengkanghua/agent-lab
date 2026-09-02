"""按配置构造生成式模型客户端，是全项目唯一的 provider 分叉点。

本模块位于 Agent 层最底部：上层只拿到 LangChain 的 ``BaseChatModel`` 抽象，不知道背后是
OpenAI 兼容中转站还是本地 Ollama。新增第三种接入方式只改本文件，中间件、工具、路由和
前端都不动——这是把 provider 差异收敛在一个函数里的全部意义。

本模块只构造客户端对象，不发起请求、不校验模型是否真的存在（那要等第一次调用才知道），
也不读取环境变量（配置由调用方传入）。
"""

from langchain_core.language_models import BaseChatModel

from agent_lab.config.llm import LlmProvider, LlmSettings


class LlmConfigurationError(RuntimeError):
    """LLM 配置在语义上不可用，构造客户端前就能判定。

    区别于 ``pydantic.ValidationError``：那个管「字段格式对不对」，本异常管「字段之间
    的组合有没有意义」，比如选了 OpenAI 兼容中转站却没给 API Key。这类错误重试无用，
    必须改配置，所以在错误契约里映射成不可重试。
    """


def build_chat_model(settings: LlmSettings, *, model: str | None = None) -> BaseChatModel:
    """按配置构造一个生成式模型客户端。

    Args:
        settings: 已完成校验的进程级 LLM 配置。
        model: 可选的模型名覆盖；``None`` 时用 ``settings.model``。备用模型走同一个
            函数，只换这个参数——这样主备两个客户端的超时、温度、认证方式必然一致，
            不会出现「主模型 60 秒超时、备用模型用了库默认值」这种难查的不对称。

    Returns:
        可直接交给 ``create_agent`` 的 ``BaseChatModel``；已绑定超时与采样温度。

    Raises:
        LlmConfigurationError: provider 要求 API Key 但配置为空。

    Notes:
        只构造对象，不发起任何 HTTP 请求，因此模型名错误、地址不可达、Key 无效都不会在
        这里暴露，而是在第一次模型调用时以上游异常的形式出现，由错误契约层分类。
    """

    if settings.provider is LlmProvider.OPENAI_COMPATIBLE:
        return _build_openai_compatible_model(settings, model or settings.model)
    return _build_ollama_model(settings, model or settings.model)


def _build_openai_compatible_model(settings: LlmSettings, model: str) -> BaseChatModel:
    """构造 OpenAI 兼容接口的客户端（官方 API、中转站、以及各类兼容网关）。

    Args:
        settings: 进程级 LLM 配置。
        model: 本次要绑定的模型名。

    Returns:
        绑定 base_url、凭据、超时和温度的 ``ChatOpenAI``。

    Raises:
        LlmConfigurationError: ``LLM_API_KEY`` 为空。OpenAI 兼容端点一律要求凭据，
            空 Key 的失败会推迟到第一次调用才以 401 出现，不如在启动时就说清楚。

    Notes:
        ``api_key`` 的明文在这里读取一次并交给客户端，不写日志、不进异常消息。
    """

    # 1、在函数里 import 而不是模块顶部：langchain_openai 会连带 import openai SDK，
    #    只用 Ollama 分支的部署没必要为此付启动开销。
    from langchain_openai import ChatOpenAI

    # 2、凭据必须非空。空 Key 放过去的话，失败会推迟到第一次调用时以 401 出现。
    secret = settings.api_key.get_secret_value().strip()
    if not secret:
        raise LlmConfigurationError(
            "provider 为 openai_compatible 时必须提供 LLM_API_KEY。"
        )

    # 3、组装客户端。
    return ChatOpenAI(
        model=model,
        base_url=str(settings.base_url),
        api_key=settings.api_key,
        temperature=settings.temperature,
        timeout=settings.request_timeout_seconds,
        default_headers=build_user_agent_headers(settings),
        # 关掉客户端自带重试：重试统一由 ModelRetryMiddleware 负责，两层都开会让实际
        # 请求次数变成乘积（2×3=6），超时和额度都不可预期。
        max_retries=0,
    )


def _build_ollama_model(settings: LlmSettings, model: str) -> BaseChatModel:
    """构造本地/自托管 Ollama 的客户端。

    Args:
        settings: 进程级 LLM 配置。
        model: 本次要绑定的模型名。

    Returns:
        绑定 base_url、超时和温度的 ``ChatOllama``。

    Notes:
        Ollama 原生 API 不要求 API Key，所以这里不校验凭据；反向代理需要认证时由
        ``LLM_API_KEY`` 以 Bearer header 形式带上，为空则不带。此处与
        ``config.ollama_embedding.build_ollama_headers`` 是同一套约定，但两者服务的是
        不同的模型（生成 vs Embedding），所以不共用配置对象。
    """

    from langchain_ollama import ChatOllama

    # 1、Ollama 原生 API 不要求凭据，但反向代理可能要，所以 Key 有值就带成 Bearer 头，
    #    为空就不带——不像 OpenAI 分支那样报错。
    secret = settings.api_key.get_secret_value().strip()
    headers = build_user_agent_headers(settings) or {}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    # 2、组装客户端。headers 为空时不传这个键，让 SDK 用自己的默认值。
    return ChatOllama(
        model=model,
        base_url=str(settings.base_url),
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
        ``agent.model_catalog`` 也用它——启动时问「有哪些模型」必须和真正调模型时报同一个
        身份，否则某些按 User-Agent 拦流量的中转站会让两者得出不同结论。
        为什么要能改 User-Agent 见 ``LlmSettings.user_agent`` 的字段说明。
    """

    user_agent = settings.user_agent.strip()
    return {"User-Agent": user_agent} if user_agent else None


__all__ = ["LlmConfigurationError", "build_chat_model", "build_user_agent_headers"]
