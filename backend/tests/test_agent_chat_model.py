"""按一行渠道配置构造生成式模型客户端时的 provider 分叉与请求头约定。

本文件只断言「构造出的客户端带了什么参数」，不发起任何网络请求——``build_chat_model``
的契约就是不碰网络，模型名错误、凭据无效都要等第一次调用才暴露。

接入类型、地址、凭据与模型名来自模型目录里的一行渠道，温度、超时与 User-Agent 来自进程级
配置；所以每一处断言都要能分辨「这一项来自渠道」还是「这一项来自进程」。
"""

import pytest

from agent_lab.agent.chat_model import LlmConfigurationError, build_chat_model
from agent_lab.config.llm import LlmProvider, LlmSettings


def make_settings(**overrides: object) -> LlmSettings:
    """造一份不读 .env 的进程级 LLM 配置。

    显式传齐每个字段，避免测试结果随开发机上 .env 的内容变化。
    """

    defaults: dict[str, object] = {
        "temperature": 0.0,
        "request_timeout_seconds": 60.0,
        "user_agent": "agent-lab",
    }
    return LlmSettings.model_construct(**{**defaults, **overrides})


def build(settings: LlmSettings | None = None, **channel: object):
    """按一行渠道配置构造客户端；渠道项按关键字给，缺省是 OpenAI 兼容那一套。"""

    defaults: dict[str, object] = {
        "provider": LlmProvider.OPENAI_COMPATIBLE,
        "base_url": "https://gateway.example.com/v1",
        "credential": "sk-test",
        "model": "test-model",
    }
    return build_chat_model(make_settings() if settings is None else settings, **{**defaults, **channel})


def test_the_default_user_agent_names_this_project() -> None:
    """默认 User-Agent 如实报出本项目，不伪装成别的客户端。

    需要这个头是因为部分中转站按 User-Agent 拦通用 SDK 流量：openai SDK 默认发的
    ``OpenAI/Python x.y.z`` 会被判 403，同一个 Key 换个标识就能用。修法是如实署名，
    不是冒用其他客户端的标识。
    """

    model = build(make_settings(user_agent="agent-lab"))

    assert model.default_headers == {"User-Agent": "agent-lab"}


def test_an_empty_user_agent_leaves_the_sdk_default_alone() -> None:
    """留空表示不覆盖，交给 SDK 发它自己的 User-Agent。"""

    model = build(make_settings(user_agent="   "))

    assert model.default_headers is None


def test_the_access_type_decides_which_client_class_is_built() -> None:
    """接入类型决定用哪个客户端类，两个类各自带自己那套字段。

    OpenAI 兼容分支的名字在 ``model_name`` 上、地址在 ``openai_api_base`` 上；Ollama 分支的
    名字在 ``model`` 上、地址在 ``base_url`` 上。断言的是构造参数，不发任何请求。
    """

    compatible = build(provider=LlmProvider.OPENAI_COMPATIBLE)
    local = build(provider=LlmProvider.OLLAMA)

    assert type(compatible).__name__ == "ChatOpenAI"
    assert type(local).__name__ == "ChatOllama"
    assert (compatible.model_name, compatible.openai_api_base) == (
        "test-model",
        "https://gateway.example.com/v1",
    )
    assert (local.model, local.base_url) == ("test-model", "https://gateway.example.com/v1")


def test_two_channels_build_clients_with_their_own_address_and_model() -> None:
    """两个渠道构造出两个只差渠道内容的客户端：类、地址、模型名各自独立。

    这条钉的是「模型配置的唯一事实源是目录」：同一份进程级配置下，不同的渠道必须落到不同的
    上游与模型名，否则「选了不同模型」在客户端这一层就分辨不出来。
    """

    settings = make_settings(user_agent="agent-lab", temperature=0.3)
    first = build(
        settings,
        provider=LlmProvider.OPENAI_COMPATIBLE,
        base_url="https://first.example.com/v1",
        model="alpha",
    )
    second = build(
        settings,
        provider=LlmProvider.OLLAMA,
        base_url="http://second.example.com:11434",
        credential="",
        model="beta",
    )

    assert type(first) is not type(second)
    assert (first.model_name, second.model) == ("alpha", "beta")
    assert first.temperature == second.temperature == 0.3, "温度来自进程级配置，两个渠道共用"
    assert first.default_headers == {"User-Agent": "agent-lab"}
    assert second.client_kwargs["headers"] == {"User-Agent": "agent-lab"}


def test_client_retries_are_off_so_middleware_owns_retrying() -> None:
    """客户端自带重试必须关掉，否则和中间件叠成乘积次请求。"""

    assert build().max_retries == 0


def test_the_openai_branch_asks_the_upstream_to_report_usage() -> None:
    """OpenAI 兼容分支必须显式打开「流式响应里回传用量」。

    自建 base_url（本项目的中转站）下 ``stream_usage`` 默认是 False，而 Agent 的生产入口
    本来就是流式调用，按官方契约上游此时不会回 usage，用量会被静默记成 0。真实请求体里是否
    带上这个开关由 ``tests/test_agent_usage_recording.py`` 走生产入口断言。
    """

    assert build().stream_usage is True


def test_the_ollama_branch_has_no_stream_usage_switch() -> None:
    """Ollama 分支没有这个概念，构造参数不能被顺手塞一个。

    它的用量来自响应里的提示与生成计数，不需要请求侧开关；多传一个未知字段会在构造时就炸。
    """

    model = build(provider=LlmProvider.OLLAMA)

    assert not hasattr(model, "stream_usage")


def test_an_empty_credential_fails_before_any_request_is_made() -> None:
    """openai_compatible 渠道缺凭据时立刻报配置错误，不推迟到第一次调用。

    保存渠道时也会拦（见 ``services.llm_provider_service``），这里是第二道：目录是业务数据，
    可能被直接改过。
    """

    with pytest.raises(LlmConfigurationError):
        build(credential="  ")


def test_the_ollama_branch_sends_the_user_agent_alongside_the_bearer_token() -> None:
    """Ollama 分支同时带 User-Agent 和 Bearer，两个头不互相挤掉。

    盯的是一个具体回归：早先版本里 headers 由 Key 是否存在决定，加 User-Agent 时若沿用
    那个三元表达式，Ollama 分支就会在有 Key 时丢掉 User-Agent，或者反过来。
    """

    model = build(
        make_settings(user_agent="agent-lab"),
        provider=LlmProvider.OLLAMA,
        credential="sk-proxy",
    )

    headers = model.client_kwargs["headers"]
    assert headers == {"User-Agent": "agent-lab", "Authorization": "Bearer sk-proxy"}


def test_the_ollama_branch_needs_no_credential() -> None:
    """Ollama 原生接口不要求凭据，空凭据不报错、也不发 Authorization。"""

    model = build(make_settings(user_agent="agent-lab"), provider=LlmProvider.OLLAMA, credential="")

    assert model.client_kwargs["headers"] == {"User-Agent": "agent-lab"}
