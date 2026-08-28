"""在多个搜索请求契约之间共用的 query / score_threshold 字段校验逻辑。

本模块位于 Pydantic Schema 层内部（前缀下划线表示不对外导出），只提供纯函数式的
字段校验器，供 ``vector_search`` 与 ``document_search`` 的请求模型复用。它不定义
模型、不做 I/O，也不参与响应契约。

抽到这里的原因：Chunk 级搜索和文档级搜索是两个独立的请求契约（字段集合不同，不能
合并成一个模型），但它们对 ``query`` 和 ``score_threshold`` 的要求必须完全一致——包括
错误文案，因为 HTTP 422 响应体里的这段文案属于对外契约，有测试直接断言。复制粘贴
两份的话，改文案时漏改一处就会让两个端点的报错不一致。
"""

from numbers import Real
from typing import Any


def require_non_whitespace_query(value: str) -> str:
    """在任何 Embedding 网络调用前拒绝空白 query。

    Args:
        value: 调用方提交的原始 query。

    Returns:
        保留原始有效空白的 query，避免擅自改变模型输入。

    Raises:
        ValueError: query 只包含空白字符。
    """

    if not value.strip():
        raise ValueError("query 不能只包含空白字符")
    return value


def require_numeric_threshold(value: Any) -> Any:
    """拒绝 bool 和字符串等会被宽松转换成浮点数的 threshold。

    以 ``mode="before"`` 使用：必须在 Pydantic 把 ``True`` 或 ``"0.5"`` 悄悄转成
    float 之前拦下来，否则调用方会拿到一个自己没指定过的相似度阈值。

    Args:
        value: 调用方传入的可选 Cosine score threshold。

    Returns:
        ``None`` 或真实数值，随后由 Field 检查有限性与 ``[-1, 1]`` 范围。

    Raises:
        ValueError: 值不是非 bool 的实数。
    """

    if value is not None and (isinstance(value, bool) or not isinstance(value, Real)):
        raise ValueError("score_threshold 必须是数值型 Cosine 分数")
    return value


__all__ = ["require_non_whitespace_query", "require_numeric_threshold"]
