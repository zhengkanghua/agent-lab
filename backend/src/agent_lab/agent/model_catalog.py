"""启动时向上游拉一次模型列表，校验配置的模型名真的存在。

**为什么需要它**：``build_chat_model`` 只构造客户端、不验证模型名（那要等第一次调用才知道），
所以模型名写错在启动阶段完全没有信号。而写错的表现极具误导性——曾经把 ``LLM_MODEL`` 配成
``auto``（那不是模型名，是某些中转站的自动路由开关，按每次 HTTP 调用挑上游），症状是
「查完资料不给回答」和「模型声称要调用一个我们根本没注册的工具」，排查方向被带到前端和
流式管道上，而根因只是一行配置。本模块把这类问题提前到启动时说出来。

**为什么校验失败只关掉 Agent、不拖垮进程**：检索、阅读和流水线都不需要生成式模型。一个配错的
模型名不该让整个只读系统下线，所以本模块的异常由 ``main.py`` 的 lifespan 接住，结果是
``/agent/*`` 返回 503，其余接口照常。

**「拉不到列表」和「列表里没有这个模型」是两回事**，本模块刻意区别对待：
- 拉不到（网络不通、端点不支持、被拦、超时、响应结构不认识）一律**放过**，只记一条 warning。
  可观测性检查不该成为新的故障源，而且此时我们并没有证据说配置错了。
- 拉到了非空列表、而配置的模型名不在里面，才抛 ``LlmModelNotListedError``。这时证据是确凿的。

本模块只发一次 HTTP GET（列模型，不产生任何 token 消耗），不写数据库、不碰 Qdrant，
也不读环境变量（配置由调用方传入）。
"""

import logging
from typing import Any

import httpx

from agent_lab.agent.limits import MODEL_CATALOG_TIMEOUT_SECONDS
from agent_lab.agent.chat_model import build_user_agent_headers
from agent_lab.config.llm import LlmProvider, LlmSettings


logger = logging.getLogger(__name__)


class LlmModelNotListedError(RuntimeError):
    """上游给出了模型列表，但配置的模型名不在其中。

    区别于 ``LlmConfigurationError``（字段组合没意义，比如缺 Key）：这个是「字段值和上游
    的实际情况对不上」，只有问过上游才知道。重试无用，必须改 ``.env``。

    异常消息里只放模型名，不放 base_url 或凭据——模型名来自本地配置且本就写在日志里，
    而另两个不是。
    """


async def fetch_model_names(settings: LlmSettings) -> frozenset[str]:
    """向上游拉一次可用模型名列表。

    Args:
        settings: 进程级 LLM 配置；只读 provider、base_url、凭据和 User-Agent。

    Returns:
        上游报出的模型名集合；拉不到或读不懂响应时返回空集合，**不抛异常**——调用方靠
        「空集合」判断「这次没有证据」，而不是靠捕获异常。

    Notes:
        执行一次 HTTP GET，超时 ``MODEL_CATALOG_TIMEOUT_SECONDS``。不发起模型推理，
        因此不消耗 token 额度。失败只记异常类型名：响应体可能带上游的原始报错正文。
    """

    url, headers = _catalog_request(settings)
    try:
        async with httpx.AsyncClient(timeout=MODEL_CATALOG_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        # 拉不到就是拉不到。不记 str(exc)：响应体里可能有上游的原始错误正文。
        logger.warning(
            "未能读取上游模型列表，跳过模型名校验 provider=%s error_type=%s",
            settings.provider.value,
            type(exc).__name__,
        )
        return frozenset()
    return _parse_model_names(settings.provider, payload)


def _catalog_request(settings: LlmSettings) -> tuple[str, dict[str, str]]:
    """按 provider 决定列模型的 URL 和请求头。

    Args:
        settings: 进程级 LLM 配置。

    Returns:
        ``(URL, 请求头)``。

    Notes:
        纯字符串处理，不发请求。两个 provider 的端点不同：OpenAI 兼容侧是 ``GET /models``
        （``base_url`` 通常已带 ``/v1``），Ollama 原生侧是 ``GET /api/tags``。

        User-Agent 沿用 ``build_user_agent_headers``，与真正调模型时发的一致。这不是细节：
        部分中转站按 User-Agent 拦流量，用别的标识去问等于问了一个不同的身份，拿到的
        结论对不上真实调用。
    """

    base = str(settings.base_url).rstrip("/")
    headers = build_user_agent_headers(settings) or {}
    secret = settings.api_key.get_secret_value().strip()
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    if settings.provider is LlmProvider.OPENAI_COMPATIBLE:
        return f"{base}/models", headers
    return f"{base}/api/tags", headers


def _parse_model_names(provider: LlmProvider, payload: Any) -> frozenset[str]:
    """从列模型响应里抽出模型名集合。

    Args:
        provider: 决定读哪个字段。
        payload: 已解析的 JSON。

    Returns:
        模型名集合；结构不符合预期时返回空集合，语义同「没拉到」。

    Notes:
        纯内存解析，不执行 I/O。刻意写得宽容：中转站的响应结构五花八门，读不懂时返回空集合
        （于是放过校验）比抛异常好——我们要防的是配错模型名，不是替上游校验响应格式。
    """

    if not isinstance(payload, dict):
        return frozenset()
    if provider is LlmProvider.OPENAI_COMPATIBLE:
        entries = payload.get("data")
        key = "id"
    else:
        entries = payload.get("models")
        key = "name"
    if not isinstance(entries, list):
        return frozenset()
    names = {
        entry[key]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get(key), str) and entry[key]
    }
    return frozenset(names)


async def verify_configured_models(settings: LlmSettings) -> None:
    """校验 ``LLM_MODEL`` 与 ``LLM_FALLBACK_MODEL`` 出现在上游的模型列表里。

    Args:
        settings: 进程级 LLM 配置。

    Raises:
        LlmModelNotListedError: 上游给出了非空列表，而主模型或备用模型不在其中。

    Notes:
        执行一次 HTTP GET（见 ``fetch_model_names``），不发起推理、不消耗 token。
        拉不到列表时静默放过——判据是「有没有证据说配置错了」，而不是「上游健不健康」。

        主备一起校验：备用模型只在主模型失败时才被调用，配错的话平时完全看不出来，
        真到降级那一刻反而多一层故障。这正是最不该出问题的时候。

        校验通过不记日志。启动日志已经够长，而「配置正确」是常态，只有异常值得占一行。
    """

    available = await fetch_model_names(settings)
    if not available:
        return
    configured = {settings.model, settings.fallback_model}
    missing = sorted(name for name in configured if name not in available)
    if not missing:
        return
    # 只记模型名和列表规模，不记完整列表：中转站可能报出上千个模型，刷屏且无助于定位。
    logger.error(
        "配置的模型不在上游模型列表里 provider=%s missing=%s available_count=%d",
        settings.provider.value,
        ",".join(missing),
        len(available),
    )
    raise LlmModelNotListedError(
        f"上游模型列表中没有以下模型：{'、'.join(missing)}。"
        "请检查 LLM_MODEL 与 LLM_FALLBACK_MODEL；注意 auto 之类的路由开关不是模型名。"
    )


__all__ = [
    "LlmModelNotListedError",
    "fetch_model_names",
    "verify_configured_models",
]
