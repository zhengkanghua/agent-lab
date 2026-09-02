"""错误契约的跨表不变量与全仓库 detail 文案守护。

`code` 是对外契约，`detail` 是给人读的中文文案。本文件同时守两件事：
规则表内部的一致性，以及表**之外**那些直接写在 `raise` 处的 detail 字面量——
后者没有集中结构可断言，只能扫源码，否则漂移不会被任何测试拦住。
"""

import ast
import pathlib

import pytest

from agent_lab.api.dependencies import VectorSearchRuntimeUnavailableError
from agent_lab.api.error_contract import (
    INVALID_REQUEST_RULE,
    PIPELINE_ERROR_RULES,
    SEARCH_UPSTREAM_EXCEPTIONS,
    UNCLASSIFIED_ERROR_RULE,
    USER_ADMIN_ERROR_RULES,
    VECTOR_SEARCH_ERROR_RULES,
    ErrorContractRule,
    resolve_error_contract,
)

SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "agent_lab"


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
        UNCLASSIFIED_ERROR_RULE,
        INVALID_REQUEST_RULE,
    )


# 签名为 (code, detail) 的领域错误：detail 走第二个位置实参，没有关键字名可认。
# 每新增一个这种形状的领域错误类都要登记进来，否则它抛出的 detail 字面量不受本文件约束。
POSITIONAL_DETAIL_CALLS = {"UserAdminDomainError": 1, "AccountDomainError": 1}


def called_name(node: ast.Call) -> str:
    """取被调用者的名字，`a.b.C(...)` 取 `C`。"""

    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def literal_details() -> list[tuple[str, int, str]]:
    """收集全部源码里写死的 detail 文案，返回 (相对路径, 行号, 文案)。

    覆盖三种写法：`detail="..."` 关键字实参（含 HTTPException）、模块级 `*_DETAIL`
    字符串常量，以及 `POSITIONAL_DETAIL_CALLS` 里按位置传 detail 的领域错误。
    非字面量（变量、f-string）跳过——它们的取值由别处的常量决定，会在那一处被扫到。
    """

    found: list[tuple[str, int, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg != "detail":
                        continue
                    if isinstance(keyword.value, ast.Constant) and isinstance(
                        keyword.value.value, str
                    ):
                        found.append((relative, keyword.value.lineno, keyword.value.value))
                index = POSITIONAL_DETAIL_CALLS.get(called_name(node))
                if index is not None and len(node.args) > index:
                    argument = node.args[index]
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        found.append((relative, argument.lineno, argument.value))
            elif isinstance(node, ast.Assign):
                names = [
                    target.id for target in node.targets if isinstance(target, ast.Name)
                ]
                if not any(name.endswith("_DETAIL") for name in names):
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    found.append((relative, node.lineno, node.value.value))
    return found


def test_error_detail_is_chinese_and_never_carries_exception_text() -> None:
    """错误表里的 detail 必须是预写中文常量，不允许残留英文文案。"""

    for rule in all_error_rules():
        assert is_chinese_sentence(rule.detail), rule.code


def test_literal_details_outside_the_rule_tables_follow_the_same_wording_rule() -> None:
    """表外直接写在 raise 处的 detail 也必须是中文句子。

    `documents.py` / `health.py` 用裸 HTTPException，`user_admin_service.py` 的领域
    错误按 code 而非异常类型分支，都进不了类型键控的规则表。历史上
    `health.py` 的「数据库不可用」就漏过了句尾句号，靠人眼没看住。
    """

    details = literal_details()
    # 扫描本身必须有效：一旦重构把所有 detail 都改成非字面量，断言会空转。
    assert len(details) >= 10, f"扫到的 detail 太少，扫描逻辑可能已失效：{details}"

    offenders = [
        f"{path}:{line} -> {text!r}"
        for path, line, text in details
        if not is_chinese_sentence(text)
    ]
    assert offenders == [], "detail 必须是以「。」收尾的中文句子：" + "; ".join(offenders)


def test_same_error_code_always_maps_to_the_same_detail() -> None:
    """code 是对外契约，同一个 code 在任何表里都必须给出同一句 detail。"""

    detail_by_code: dict[str, str] = {}
    for rule in all_error_rules():
        existing = detail_by_code.setdefault(rule.code, rule.detail)
        assert existing == rule.detail, rule.code


@pytest.mark.parametrize(
    "rules",
    [VECTOR_SEARCH_ERROR_RULES, PIPELINE_ERROR_RULES, USER_ADMIN_ERROR_RULES],
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
