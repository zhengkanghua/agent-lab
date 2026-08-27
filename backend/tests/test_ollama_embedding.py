"""Ollama Embedding 配置、批处理、响应校验和错误映射的离线测试。"""

import asyncio
import math
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from langchain_core.documents import Document
from ollama import ResponseError
from pydantic import SecretStr, ValidationError

from agent_lab.config.ollama_embedding import (
    OllamaEmbeddingSettings,
    build_ollama_headers,
)
from agent_lab.pipeline.ollama_embedding_provider import (
    EmbeddingResponseError,
    OllamaAuthenticationError,
    OllamaConnectionError,
    OllamaEmbeddingProvider,
    OllamaModelNotFoundError,
    OllamaTimeoutError,
)


class FakeEmbeddings:
    """按预设响应模拟 LangChain Embeddings，并记录远程调用形状。"""

    def __init__(
        self,
        *,
        document_responses: Sequence[Any] = (),
        query_response: Any = None,
    ) -> None:
        self.document_responses = list(document_responses)
        self.query_response = query_response
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """返回下一项预设批量响应，或抛出该项预设异常。"""

        self.document_calls.append(list(texts))
        response = self.document_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aembed_query(self, text: str) -> list[float]:
        """返回预设 query 响应，或抛出预设异常。"""

        self.query_calls.append(text)
        if isinstance(self.query_response, Exception):
            raise self.query_response
        return self.query_response


def settings(*, batch_size: int = 16, api_key: str = "") -> OllamaEmbeddingSettings:
    """创建不读取项目 ``.env`` 的确定性测试配置。"""

    return OllamaEmbeddingSettings(
        _env_file=None,
        base_url="https://ollama.example.test",
        embedding_model="test-embedding-model",
        api_key=SecretStr(api_key),
        embedding_request_timeout_seconds=3,
        embedding_batch_size=batch_size,
    )


def run(coroutine: Any) -> Any:
    """在不增加异步 pytest 插件的情况下执行一个测试协程。"""

    return asyncio.run(coroutine)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OLLAMA_BASE_URL",
        "OLLAMA_EMBEDDING_MODEL",
        "OLLAMA_API_KEY",
        "OLLAMA_EMBEDDING_REQUEST_TIMEOUT_SECONDS",
        "OLLAMA_EMBEDDING_BATCH_SIZE",
    ):
        monkeypatch.delenv(name, raising=False)

    config = OllamaEmbeddingSettings(_env_file=None)

    assert str(config.base_url) == "https://ollama.example.com/"
    assert config.embedding_model == "bge-m3:567m"
    assert config.api_key.get_secret_value() == ""
    assert config.embedding_request_timeout_seconds == 120
    assert config.embedding_batch_size == 16


def test_settings_parse_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.example.test/api")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", " custom-model ")
    monkeypatch.setenv("OLLAMA_API_KEY", "private-test-key")
    monkeypatch.setenv("OLLAMA_EMBEDDING_REQUEST_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("OLLAMA_EMBEDDING_BATCH_SIZE", "3")

    config = OllamaEmbeddingSettings(_env_file=None)

    assert str(config.base_url) == "https://ollama.example.test/api"
    assert config.embedding_model == "custom-model"
    assert config.api_key.get_secret_value() == "private-test-key"
    assert config.embedding_request_timeout_seconds == 7.5
    assert config.embedding_batch_size == 3


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"base_url": "not-a-url"}, "base_url"),
        ({"embedding_model": "  "}, "embedding_model"),
        ({"embedding_request_timeout_seconds": 0}, "embedding_request_timeout_seconds"),
        ({"embedding_batch_size": 0}, "embedding_batch_size"),
    ],
)
def test_settings_reject_invalid_values(
    overrides: dict[str, object], field: str
) -> None:
    values: dict[str, object] = {
        "_env_file": None,
        "base_url": "https://ollama.example.test",
        "embedding_model": "model",
        "embedding_request_timeout_seconds": 1,
        "embedding_batch_size": 1,
    }
    values.update(overrides)

    with pytest.raises(ValidationError) as exc_info:
        OllamaEmbeddingSettings(**values)  # type: ignore[arg-type]

    assert field in str(exc_info.value)


def test_secret_does_not_leak_from_repr_or_headers_for_empty_key() -> None:
    secret = "private-test-key"
    config = settings(api_key=secret)

    assert secret not in repr(config)
    assert secret not in str(config)
    assert build_ollama_headers(SecretStr("")) == {}
    assert build_ollama_headers(config.api_key) == {
        "Authorization": f"Bearer {secret}"
    }


def test_official_embedding_client_repr_does_not_leak_secret() -> None:
    secret = "never-repr-this-key"
    provider = OllamaEmbeddingProvider(settings(api_key=secret))

    assert secret not in repr(provider)
    assert secret not in repr(provider._embeddings)  # noqa: SLF001


def test_embed_query_returns_valid_vector() -> None:
    fake = FakeEmbeddings(query_response=[0.1, 0.2, 0.3])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    vector = run(provider.embed_query("查询文本"))

    assert vector == [0.1, 0.2, 0.3]
    assert fake.query_calls == ["查询文本"]
    assert provider.dimension == 3


def test_embed_documents_preserves_order() -> None:
    fake = FakeEmbeddings(document_responses=[[[1, 0], [2, 0], [3, 0]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    vectors = run(provider.embed_documents(["甲", "乙", "丙"]))

    assert vectors == [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]
    assert fake.document_calls == [["甲", "乙", "丙"]]


@pytest.mark.parametrize("text", ["", " \n\t "])
def test_embed_query_rejects_empty_or_whitespace_text(text: str) -> None:
    fake = FakeEmbeddings(query_response=[1.0])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="非空白字符"):
        run(provider.embed_query(text))

    assert fake.query_calls == []


def test_embed_documents_rejects_empty_item_before_remote_call() -> None:
    fake = FakeEmbeddings(document_responses=[[[1.0]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=r"document\[1\]"):
        run(provider.embed_documents(["有效", " "]))

    assert fake.document_calls == []


def test_empty_document_list_returns_without_remote_call() -> None:
    fake = FakeEmbeddings()
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    assert run(provider.embed_documents([])) == []
    assert fake.document_calls == []


def test_batch_size_splits_calls_and_merges_in_input_order() -> None:
    fake = FakeEmbeddings(
        document_responses=[
            [[1, 0], [2, 0]],
            [[3, 0], [4, 0]],
            [[5, 0]],
        ]
    )
    provider = OllamaEmbeddingProvider(
        settings(batch_size=2), embeddings=fake  # type: ignore[arg-type]
    )

    vectors = run(provider.embed_documents(["一", "二", "三", "四", "五"]))

    assert fake.document_calls == [["一", "二"], ["三", "四"], ["五"]]
    assert vectors == [
        [1.0, 0.0],
        [2.0, 0.0],
        [3.0, 0.0],
        [4.0, 0.0],
        [5.0, 0.0],
    ]


def test_rejects_vector_count_mismatch() -> None:
    fake = FakeEmbeddings(document_responses=[[[1.0]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    with pytest.raises(EmbeddingResponseError, match="数量"):
        run(provider.embed_documents(["甲", "乙"]))


def test_rejects_empty_vector() -> None:
    fake = FakeEmbeddings(document_responses=[[[]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    with pytest.raises(EmbeddingResponseError, match="空嵌入向量"):
        run(provider.embed_documents(["甲"]))


def test_rejects_zero_norm_vector_for_cosine_space() -> None:
    fake = FakeEmbeddings(document_responses=[[[0.0, 0.0]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    with pytest.raises(EmbeddingResponseError, match="零范数"):
        run(provider.embed_documents(["甲"]))


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-number", "非数值"),
        (float("nan"), "非有限"),
        (float("inf"), "非有限"),
        (float("-inf"), "非有限"),
    ],
)
def test_rejects_invalid_vector_values(value: object, message: str) -> None:
    fake = FakeEmbeddings(document_responses=[[[value]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    with pytest.raises(EmbeddingResponseError, match=message):
        run(provider.embed_documents(["甲"]))


def test_rejects_inconsistent_dimensions_within_batch() -> None:
    fake = FakeEmbeddings(document_responses=[[[1.0, 2.0], [3.0]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    with pytest.raises(EmbeddingResponseError, match="同一批次"):
        run(provider.embed_documents(["甲", "乙"]))


def test_rejects_inconsistent_dimensions_across_batches() -> None:
    fake = FakeEmbeddings(
        document_responses=[[[1.0, 2.0]], [[3.0, 4.0, 5.0]]]
    )
    provider = OllamaEmbeddingProvider(
        settings(batch_size=1), embeddings=fake  # type: ignore[arg-type]
    )

    with pytest.raises(EmbeddingResponseError, match="不同批次"):
        run(provider.embed_documents(["甲", "乙"]))


def test_provider_rejects_dimension_change_across_calls() -> None:
    fake = FakeEmbeddings(
        document_responses=[[[1.0, 2.0]], [[1.0, 2.0, 3.0]]]
    )
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    assert run(provider.embed_documents(["第一次"])) == [[1.0, 2.0]]
    with pytest.raises(EmbeddingResponseError, match="生命周期"):
        run(provider.embed_documents(["第二次"]))


def test_probe_dimension_uses_real_response_length() -> None:
    fake = FakeEmbeddings(query_response=[0.0, 0.1, 0.2, 0.3])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    assert run(provider.probe_dimension("维度探测")) == 4
    assert provider.dimension == 4


@pytest.mark.parametrize(
    ("error", "expected_exception"),
    [
        (httpx.ReadTimeout("slow response"), OllamaTimeoutError),
        (httpx.ConnectError("connection refused"), OllamaConnectionError),
        (ConnectionError("ollama client connection error"), OllamaConnectionError),
        (ResponseError("missing model", 404), OllamaModelNotFoundError),
    ],
)
def test_maps_remote_errors(
    error: Exception, expected_exception: type[Exception]
) -> None:
    fake = FakeEmbeddings(query_response=error)
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    with pytest.raises(expected_exception):
        run(provider.embed_query("安全测试文本"))


def test_authentication_error_does_not_leak_key() -> None:
    secret = "never-expose-this-key"
    fake = FakeEmbeddings(
        query_response=ResponseError(f"proxy rejected {secret}", 401)
    )
    provider = OllamaEmbeddingProvider(
        settings(api_key=secret), embeddings=fake  # type: ignore[arg-type]
    )

    with pytest.raises(OllamaAuthenticationError) as exc_info:
        run(provider.embed_query("安全测试文本"))

    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)


def test_embed_chunks_preserves_chunk_ids_and_uses_only_page_content() -> None:
    chunks = [
        Document(id="chunk-a", page_content="第一段", metadata={"title": "不嵌入"}),
        Document(id="chunk-b", page_content="第二段", metadata={"title": "也不嵌入"}),
    ]
    fake = FakeEmbeddings(document_responses=[[[1.0, 0.0], [0.0, 1.0]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    results = run(provider.embed_chunks(chunks))

    assert [result.chunk_id for result in results] == ["chunk-a", "chunk-b"]
    assert [result.embedding for result in results] == [[1.0, 0.0], [0.0, 1.0]]
    assert fake.document_calls == [["第一段", "第二段"]]


def test_embed_chunks_requires_id_before_remote_call() -> None:
    fake = FakeEmbeddings(document_responses=[[[1.0]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="必须设置"):
        run(provider.embed_chunks([Document(page_content="正文")]))

    assert fake.document_calls == []


def test_valid_vectors_are_finite() -> None:
    fake = FakeEmbeddings(document_responses=[[[1, 2.5, -3]]])
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    vector = run(provider.embed_documents(["正文"]))[0]

    assert all(math.isfinite(value) for value in vector)


def test_close_does_not_take_ownership_of_injected_embeddings() -> None:
    fake = FakeEmbeddings()
    provider = OllamaEmbeddingProvider(settings(), embeddings=fake)  # type: ignore[arg-type]

    run(provider.close())

    assert provider._owns_embeddings is False  # noqa: SLF001
