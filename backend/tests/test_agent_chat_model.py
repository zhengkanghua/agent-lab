"""构造生成式模型客户端时的 provider 分叉与请求头约定。

本文件只断言「构造出的客户端带了什么参数」，不发起任何网络请求——``build_chat_model``
的契约就是不碰网络，模型名错误、Key 无效都要等第一次调用才暴露。
"""

import pytest
from pydantic import AnyHttpUrl, SecretStr

from agent_lab.agent.chat_model import LlmConfigurationError, build_chat_model
from agent_lab.config.llm import LlmProvider, LlmSettings


def make_settings(**overrides: object) -> LlmSettings:
    """造一份不读 .env 的 LLM 配置。

    显式传齐每个字段，避免测试结果随开发机上 .env 的内容变化。
    """

    defaults: dict[str, object] = {
        "provider": LlmProvider.OPENAI_COMPATIBLE,
        "base_url": AnyHttpUrl("https://gateway.example.com/v1"),
        "api_key": SecretStr("sk-test"),
        "model": "test-model",
        "fallback_model": "test-fallback",
        "temperature": 0.0,
        "request_timeout_seconds": 60.0,
    }
    return LlmSettings.model_construct(**{**defaults, **overrides})


def test_the_default_user_agent_names_this_project() -> None:
    """默认 User-Agent 如实报出本项目，不伪装成别的客户端。

    需要这个头是因为部分中转站按 User-Agent 拦通用 SDK 流量：openai SDK 默认发的
    ``OpenAI/Python x.y.z`` 会被判 403，同一个 Key 换个标识就能用。修法是如实署名，
    不是冒用其他客户端的标识。
    """

    model = build_chat_model(make_settings(user_agent="agent-lab"))

    assert model.default_headers == {"User-Agent": "agent-lab"}


def test_an_empty_user_agent_leaves_the_sdk_default_alone() -> None:
    """留空表示不覆盖，交给 SDK 发它自己的 User-Agent。"""

    model = build_chat_model(make_settings(user_agent="   "))

    assert model.default_headers is None


def test_the_fallback_model_shares_every_other_setting() -> None:
    """换模型名不改超时、温度和请求头，主备两个客户端只差模型名。"""

    settings = make_settings(user_agent="agent-lab")
    primary = build_chat_model(settings)
    fallback = build_chat_model(settings, model=settings.fallback_model)

    assert (primary.model_name, fallback.model_name) == ("test-model", "test-fallback")
    assert primary.default_headers == fallback.default_headers
    assert primary.request_timeout == fallback.request_timeout
    assert primary.temperature == fallback.temperature


def test_client_retries_are_off_so_middleware_owns_retrying() -> None:
    """客户端自带重试必须关掉，否则和中间件叠成乘积次请求。"""

    assert build_chat_model(make_settings()).max_retries == 0


def test_an_empty_api_key_fails_before_any_request_is_made() -> None:
    """openai_compatible 分支缺 Key 时立刻报配置错误，不推迟到第一次调用。"""

    with pytest.raises(LlmConfigurationError):
        build_chat_model(make_settings(api_key=SecretStr("  ")))


def test_the_ollama_branch_sends_the_user_agent_alongside_the_bearer_token() -> None:
    """Ollama 分支同时带 User-Agent 和 Bearer，两个头不互相挤掉。

    盯的是一个具体回归：早先版本里 headers 由 Key 是否存在决定，加 User-Agent 时若沿用
    那个三元表达式，Ollama 分支就会在有 Key 时丢掉 User-Agent，或者反过来。
    """

    model = build_chat_model(
        make_settings(
            provider=LlmProvider.OLLAMA,
            api_key=SecretStr("sk-proxy"),
            user_agent="agent-lab",
        )
    )

    headers = model.client_kwargs["headers"]
    assert headers == {"User-Agent": "agent-lab", "Authorization": "Bearer sk-proxy"}


def test_the_ollama_branch_needs_no_api_key() -> None:
    """Ollama 原生接口不要求凭据，空 Key 不报错、也不发 Authorization。"""

    model = build_chat_model(
        make_settings(
            provider=LlmProvider.OLLAMA,
            api_key=SecretStr(""),
            user_agent="agent-lab",
        )
    )

    assert model.client_kwargs["headers"] == {"User-Agent": "agent-lab"}
