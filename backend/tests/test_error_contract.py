"""错误码、HTTP 状态、重试提示、脱敏响应及规则表的跨表不变量。"""

import pytest

from agent_lab.api.dependencies import VectorSearchRuntimeUnavailableError
from agent_lab.api.error_contract import (
    INVALID_REQUEST_RULE,
    KNOWLEDGE_BASE_ERROR_RULES,
    PIPELINE_ERROR_RULES,
    SEARCH_UPSTREAM_EXCEPTIONS,
    UNCLASSIFIED_ERROR_RULE,
    USER_ADMIN_ERROR_RULES,
    VECTOR_SEARCH_ERROR_RULES,
    ErrorContractRule,
    VectorSearchErrorResponse,
    build_vector_search_error_response,
    resolve_error_contract,
)
from agent_lab.pipeline.ollama_embedding_provider import (
    EmbeddingResponseError,
    OllamaAuthenticationError,
    OllamaConnectionError,
    OllamaEmbeddingError,
    OllamaModelNotFoundError,
    OllamaServiceError,
    OllamaTimeoutError,
)
from agent_lab.qdrant.search import (
    QdrantSearchAuthenticationError,
    QdrantSearchConfigurationError,
    QdrantSearchConnectionError,
    QdrantSearchResponseError,
    QdrantSearchServiceError,
    QdrantSearchTargetNotFoundError,
    QdrantSearchTimeoutError,
)
from agent_lab.services.vector_search_service import QueryVectorValidationError

def test_search_upstream_mapping_preserves_status_code_retryability_and_privacy() -> None:
    """逐项保护对外错误契约，无须为相同响应构造器反复启动完整应用。"""
    cases = [
        (OllamaAuthenticationError, 502, "embedding_authentication_failed", False),
        (OllamaConnectionError, 503, "embedding_unavailable", True),
        (OllamaTimeoutError, 504, "embedding_timeout", True),
        (OllamaModelNotFoundError, 503, "embedding_model_not_found", False),
        (EmbeddingResponseError, 502, "embedding_response_invalid", False),
        (OllamaServiceError, 502, "embedding_unavailable", True),
        (OllamaEmbeddingError, 502, "embedding_unavailable", True),
        (QdrantSearchAuthenticationError, 502, "qdrant_authentication_failed", False),
        (QdrantSearchConnectionError, 503, "qdrant_unavailable", True),
        (QdrantSearchTimeoutError, 504, "qdrant_timeout", True),
        (QdrantSearchTargetNotFoundError, 503, "qdrant_target_missing", False),
        (QdrantSearchConfigurationError, 503, "qdrant_configuration_invalid", False),
        (QdrantSearchResponseError, 502, "qdrant_response_invalid", False),
        (QdrantSearchServiceError, 502, "qdrant_service_error", True),
        (QueryVectorValidationError, 502, "embedding_response_invalid", False),
    ]
    private_detail = "synthetic-private-query-and-upstream-response"
    for exception, status_code, code, retryable in cases:
        response = build_vector_search_error_response(exception(private_detail))
        parsed = VectorSearchErrorResponse.model_validate_json(response.body)
        assert (response.status_code, parsed.code, parsed.retryable) == (
            status_code, code, retryable
        ), exception.__name__
        assert private_detail.encode() not in response.body, exception.__name__


def is_chinese_sentence(text: str) -> bool:
    """判定 detail 是否为以句号收尾的中文句子。"""

    # 至少含一个 CJK 字符即可判定为中文；FreshRSS、Embedding 等专有名词保留英文。
    return text.endswith("。") and any("一" <= char <= "鿿" for char in text)


def all_error_rules() -> tuple[ErrorContractRule, ...]:
    """汇总三条链路的错误表加唯一兜底，用于校验跨表不变量。"""

    return (
        *VECTOR_SEARCH_ERROR_RULES,
        *PIPELINE_ERROR_RULES,
        *USER_ADMIN_ERROR_RULES,
        *KNOWLEDGE_BASE_ERROR_RULES,
        UNCLASSIFIED_ERROR_RULE,
        INVALID_REQUEST_RULE,
    )


def test_error_table_details_are_chinese_sentences() -> None:
    """错误表里的 detail 包含中文并以中文句号收尾。"""

    for rule in all_error_rules():
        assert is_chinese_sentence(rule.detail), rule.code


def test_same_error_code_always_maps_to_the_same_detail() -> None:
    """code 是对外契约，同一个 code 在任何表里都必须给出同一句 detail。"""

    detail_by_code: dict[str, str] = {}
    for rule in all_error_rules():
        existing = detail_by_code.setdefault(rule.code, rule.detail)
        assert existing == rule.detail, rule.code


@pytest.mark.parametrize(
    "rules",
    [VECTOR_SEARCH_ERROR_RULES, PIPELINE_ERROR_RULES, USER_ADMIN_ERROR_RULES, KNOWLEDGE_BASE_ERROR_RULES],
)
def test_specific_rules_are_never_shadowed_by_earlier_base_exception(
    rules: tuple[ErrorContractRule, ...],
) -> None:
    """表的顺序必须保持「具体子类先于基础异常」，否则后面的规则永远命中不到。"""

    for index, later in enumerate(rules):
        for earlier in rules[:index]:
            for candidate in later.exceptions:
                assert not issubclass(candidate, earlier.exceptions), (
                    f"{candidate.__name__} 会被更靠前的 {earlier.code} 提前吞掉"
                )


def test_unknown_exception_has_exactly_one_fallback_rule() -> None:
    """未分类异常的兜底只允许存在一处，各构造器不再各留一个防御分支。"""

    catch_all = [
        rule
        for rule in (*VECTOR_SEARCH_ERROR_RULES, *PIPELINE_ERROR_RULES, *USER_ADMIN_ERROR_RULES)
        if rule.exceptions in ((BaseException,), (Exception,))
    ]
    assert catch_all == []
    assert UNCLASSIFIED_ERROR_RULE.status_code == 500
    assert resolve_error_contract(RuntimeError(), PIPELINE_ERROR_RULES) is (
        UNCLASSIFIED_ERROR_RULE
    )


def test_search_rules_cover_every_caught_upstream_base_exception() -> None:
    """搜索表必须覆盖 endpoint 捕获的全部基类，否则会落到不在搜索契约里的 500。"""

    covered = {
        exception for rule in VECTOR_SEARCH_ERROR_RULES for exception in rule.exceptions
    }
    for base in (*SEARCH_UPSTREAM_EXCEPTIONS, VectorSearchRuntimeUnavailableError):
        assert base in covered, base.__name__
