"""启动时模型名校验的测试：拉列表、解析两种响应结构、以及「什么情况下才算证据」。

本文件守护的判据在 ``agent.model_catalog`` 的模块 docstring 里：**拉不到列表一律放过，
只有「拉到了非空列表、而配置的模型不在里面」才算配置错了**。这条边界是本模块唯一容易写反
的地方——写成「拉不到就抛」，上游抖一下就能让 ``/agent/*`` 整段下线，而那正是可观测性检查
不该干的事。

所有用例都用 ``httpx.MockTransport`` 拦在传输层：不连真实上游，也不读 ``.env``。
"""

import logging
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agent_lab.agent import model_catalog
from agent_lab.agent.limits import MODEL_CATALOG_TIMEOUT_SECONDS
from agent_lab.agent.model_catalog import (
    LlmModelNotListedError,
    fetch_model_names,
    verify_configured_models,
)
from agent_lab.config.llm import LlmProvider, LlmSettings
from tests.agent_helpers import run


SECRET_KEY = "sk-secret-must-not-leak"


def build_settings(
    provider: LlmProvider,
    *,
    model: str = "glm-5.2",
    fallback_model: str = "glm-5.2-air",
    api_key: str = SECRET_KEY,
) -> LlmSettings:
    """构造一份不依赖 ``.env`` 的 LLM 配置。

    每个字段都显式给值，因为 ``LlmSettings`` 是 ``BaseSettings``：漏掉的字段会去读真实
    ``.env``，那样测试结果就跟着开发机的配置走了。
    """

    return LlmSettings(
        provider=provider,
        base_url="http://127.0.0.1:9999/v1",
        api_key=SecretStr(api_key),
        model=model,
        fallback_model=fallback_model,
    )


def install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> list[httpx.Request]:
    """把 ``model_catalog`` 用的 ``httpx.AsyncClient`` 换成走 ``MockTransport`` 的版本。

    ``fetch_model_names`` 自己 ``async with httpx.AsyncClient(...)``，没有注入口——这是刻意
    的：它只发一次极简 GET，为此在生产签名上开一个「传 client」的参数，等于让调用方多背一个
    与业务无关的概念。所以测试从传输层拦，而不是改生产代码的形状。

    Args:
        monkeypatch: pytest 的替换夹具。
        handler: ``MockTransport`` 的处理函数，接收 ``httpx.Request`` 返回 ``httpx.Response``。

    Returns:
        实际发出的请求列表，用来断言 URL 和请求头。
    """

    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    original = httpx.AsyncClient

    def build_client(**kwargs: Any) -> httpx.AsyncClient:
        return original(**kwargs, transport=httpx.MockTransport(record))

    monkeypatch.setattr(model_catalog.httpx, "AsyncClient", build_client)
    return seen


def json_response(payload: Any) -> Any:
    """构造一个总是返回同一份 JSON 的处理函数。"""

    return lambda _request: httpx.Response(200, json=payload)


OPENAI_PAYLOAD = {
    "object": "list",
    "data": [
        {"id": "glm-5.2", "object": "model"},
        {"id": "glm-5.2-air", "object": "model"},
        {"id": "deepseek-v4-flash", "object": "model"},
    ],
}

OLLAMA_PAYLOAD = {
    "models": [
        {"name": "glm-5.2", "size": 1},
        {"name": "glm-5.2-air", "size": 2},
    ]
}


# 两个 provider 的列模型端点不同，读的字段也不同。参数化钉住这两组对应关系：写反了会表现为
# 「明明配对了却报模型不存在」，也就是本模块反过来制造它要防的那种误导。
@pytest.mark.parametrize(
    ("provider", "payload", "path"),
    [
        (LlmProvider.OPENAI_COMPATIBLE, OPENAI_PAYLOAD, "/v1/models"),
        (LlmProvider.OLLAMA, OLLAMA_PAYLOAD, "/v1/api/tags"),
    ],
)
def test_each_provider_is_asked_at_its_own_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    provider: LlmProvider,
    payload: Any,
    path: str,
) -> None:
    """按 provider 命中正确的 URL，并从正确的字段里读出模型名。"""

    settings = build_settings(provider)
    seen = install_transport(monkeypatch, json_response(payload))

    names = run(fetch_model_names(settings))

    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == path
    assert "glm-5.2" in names
    assert "glm-5.2-air" in names


def test_the_catalog_request_carries_the_same_identity_as_a_real_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """列模型请求必须带上和真正调模型一致的 User-Agent 和凭据。

    不是可选的礼貌：部分中转站按 User-Agent 拦流量（见 ``LlmSettings.user_agent`` 的说明），
    用别的标识去问，拿到的列表跟真实调用能用的模型对不上，校验结论就是错的。
    """

    settings = build_settings(LlmProvider.OPENAI_COMPATIBLE)
    seen = install_transport(monkeypatch, json_response(OPENAI_PAYLOAD))

    run(fetch_model_names(settings))

    assert seen[0].headers["user-agent"] == settings.user_agent
    assert seen[0].headers["authorization"] == f"Bearer {SECRET_KEY}"


def test_no_authorization_header_when_no_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没配 Key 时不发空的 ``Authorization``——本地 Ollama 不需要它，发个空的反而可能被拒。"""

    settings = build_settings(LlmProvider.OLLAMA, api_key="  ")
    seen = install_transport(monkeypatch, json_response(OLLAMA_PAYLOAD))

    run(fetch_model_names(settings))

    assert "authorization" not in seen[0].headers


def test_the_catalog_request_uses_the_short_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时用启动自检那个短值，而不是「模型思考多久」那个长值。

    钉住它是因为两个超时含义完全不同：借用 ``LLM_REQUEST_TIMEOUT_SECONDS``（默认 60）会让
    上游不健康时的启动多等一分钟，而校验拿不到结果时本来就是放过——等下去换不来别的结论。
    """

    settings = build_settings(LlmProvider.OPENAI_COMPATIBLE)
    timeouts: list[Any] = []
    original = httpx.AsyncClient

    def build_client(**kwargs: Any) -> httpx.AsyncClient:
        timeouts.append(kwargs.get("timeout"))
        return original(
            **kwargs,
            transport=httpx.MockTransport(json_response(OPENAI_PAYLOAD)),
        )

    monkeypatch.setattr(model_catalog.httpx, "AsyncClient", build_client)

    run(fetch_model_names(settings))

    assert timeouts == [MODEL_CATALOG_TIMEOUT_SECONDS]


# 「拉不到」的各种形态都必须收敛成空集合，也就是「这次没有证据」。逐个列出来是因为它们在
# 代码里走的是同一个 except，而在现实里是完全不同的故障：端点不存在、被中转站拦、上游挂了、
# 网络不通、响应不是 JSON。任何一种被漏成异常，都会让上游抖一下就拖掉 /agent/*。
@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(lambda _r: httpx.Response(404), id="端点不存在"),
        pytest.param(lambda _r: httpx.Response(403), id="被拦"),
        pytest.param(lambda _r: httpx.Response(500), id="上游故障"),
        pytest.param(
            lambda _r: (_ for _ in ()).throw(httpx.ConnectError("connection refused")),
            id="连不上",
        ),
        pytest.param(
            lambda _r: (_ for _ in ()).throw(httpx.ReadTimeout("too slow")),
            id="超时",
        ),
        pytest.param(
            lambda _r: httpx.Response(200, text="<html>not json</html>"),
            id="不是 JSON",
        ),
    ],
)
def test_an_unreachable_catalog_yields_no_evidence(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    """拉不到列表时返回空集合而不是抛异常。"""

    settings = build_settings(LlmProvider.OPENAI_COMPATIBLE)
    install_transport(monkeypatch, handler)

    assert run(fetch_model_names(settings)) == frozenset()


# 响应能解析但结构不认识，语义同「没拉到」：我们要防的是配错模型名，不是替上游校验响应格式。
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(["glm-5.2"], id="顶层不是对象"),
        pytest.param({"object": "list"}, id="缺 data 字段"),
        pytest.param({"data": {"glm-5.2": True}}, id="data 不是列表"),
        pytest.param({"data": ["glm-5.2"]}, id="条目不是对象"),
        pytest.param({"data": [{"name": "glm-5.2"}]}, id="条目缺 id"),
        pytest.param({"data": [{"id": ""}]}, id="id 是空串"),
        pytest.param({"data": [{"id": 42}]}, id="id 不是字符串"),
    ],
)
def test_an_unrecognized_payload_yields_no_evidence(
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
) -> None:
    """读不懂响应结构时返回空集合。"""

    settings = build_settings(LlmProvider.OPENAI_COMPATIBLE)
    install_transport(monkeypatch, json_response(payload))

    assert run(fetch_model_names(settings)) == frozenset()


def test_verification_passes_when_both_models_are_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主备模型都在列表里时静默通过，不抛也不记日志。"""

    settings = build_settings(LlmProvider.OPENAI_COMPATIBLE)
    install_transport(monkeypatch, json_response(OPENAI_PAYLOAD))

    assert run(verify_configured_models(settings)) is None


def test_verification_is_skipped_when_the_catalog_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拉不到列表时放过校验，即便配的是 ``auto`` 这种确定不是模型名的值。

    这条正是那条边界本身：拉不到时我们没有证据，哪怕配置看起来很可疑也不能拦。判据是
    「有没有证据说配置错了」，不是「这个值像不像模型名」——后者要靠猜，而猜错的代价是
    把一个能用的系统关掉。
    """

    settings = build_settings(LlmProvider.OPENAI_COMPATIBLE, model="auto")
    install_transport(monkeypatch, lambda _r: httpx.Response(404))

    assert run(verify_configured_models(settings)) is None


# 主备两个模型都要校验，且**任一**缺失就抛。备用模型单独列一条：它只在主模型失败时才被调用，
# 配错平时完全看不出来，真到降级那一刻反而多一层故障——最不该出问题的时候。
@pytest.mark.parametrize(
    ("model", "fallback_model", "missing"),
    [
        pytest.param("auto", "glm-5.2-air", "auto", id="主模型缺失"),
        pytest.param("glm-5.2", "gpt-9", "gpt-9", id="备用模型缺失"),
    ],
)
def test_a_model_outside_the_catalog_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    fallback_model: str,
    missing: str,
) -> None:
    """拉到非空列表、而配置的模型不在里面时抛 ``LlmModelNotListedError``。"""

    settings = build_settings(
        LlmProvider.OPENAI_COMPATIBLE,
        model=model,
        fallback_model=fallback_model,
    )
    install_transport(monkeypatch, json_response(OPENAI_PAYLOAD))

    with pytest.raises(LlmModelNotListedError) as caught:
        run(verify_configured_models(settings))

    # 消息里点名是哪个模型：这是整条链路上唯一能把人直接引到那行配置的信息。
    assert missing in str(caught.value)


def test_neither_the_message_nor_the_log_leaks_the_key_or_upstream_address(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """异常消息和日志都只含模型名，不含 API Key 或 base_url。

    启动日志通常直接进集中日志系统，而 base_url 是中转站地址、Key 是凭据，两者都不该
    因为一次配置检查被写出去。模型名例外：它来自本地配置，且是唯一能定位问题的信息。
    """

    settings = build_settings(LlmProvider.OPENAI_COMPATIBLE, model="auto")
    install_transport(monkeypatch, json_response(OPENAI_PAYLOAD))

    with caplog.at_level(logging.ERROR, logger=model_catalog.logger.name):
        with pytest.raises(LlmModelNotListedError) as caught:
            run(verify_configured_models(settings))

    written = f"{caught.value}{caplog.text}"
    assert SECRET_KEY not in written
    assert "127.0.0.1" not in written
    assert "auto" in written
