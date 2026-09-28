"""按路径加载 Alembic 迁移文件并走迁移链，供几个迁移结构测试共用。

**为什么要按路径加载而不是 import**：迁移文件名带中文说明（仓库约定），`import_module` 拼不出那样的模块名；
而且它们是脚本而不是包的一部分，没有 `__init__.py` 可依赖。
"""

import importlib.util
from pathlib import Path
from typing import Any


VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def load_migration(path: Path) -> Any:
    """加载一个迁移模块，返回它本身（可读 ``revision`` / ``down_revision`` / 两个函数）。

    Args:
        path: 迁移文件路径。

    Returns:
        已执行的模块对象。

    Notes:
        只执行模块级代码：迁移模块顶层只有常量与两个函数定义，不连接数据库、不执行 DDL。
    """

    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migration_chain() -> list[str]:
    """从 head 沿 ``down_revision`` 一路走到底，返回 revision 列表。

    Returns:
        首个元素是当前 head。

    Raises:
        AssertionError: 迁移图有多个 head（说明有人漏写了 ``down_revision``）。
    """

    by_revision: dict[str, Any] = {}
    referenced: set[str] = set()
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.name.startswith("__"):
            continue
        module = load_migration(path)
        by_revision[module.revision] = module
        if module.down_revision is not None:
            referenced.add(module.down_revision)
    heads = set(by_revision) - referenced
    assert len(heads) == 1, f"迁移图有多个 head：{sorted(heads)}"

    chain: list[str] = []
    cursor: str | None = heads.pop()
    while cursor is not None:
        chain.append(cursor)
        cursor = by_revision[cursor].down_revision
    return chain


__all__ = ["VERSIONS_DIR", "load_migration", "migration_chain"]
