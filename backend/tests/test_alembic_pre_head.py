"""``agent_lab.scripts.alembic_pre_head`` 的边界判定测试。

它守住的是一件很窄但很要紧的事：部署脚本据此决定「旧容器还在排空时能跑到哪一条」，算错了会
出现两种失败——算晚一步，删列就发生在旧代码还在读库的时候；算早一步，本可以提前跑的兼容迁移
被推到后面。所以这里既用临时迁移目录把四种链形穷举一遍，也钉住真实链上那个边界。

真实链那两条断言是给以后的人看的：新增删列（或改名、改语义）的迁移时忘了写 ``POST_DEPLOY``，
它们会红，而不是等到某次发布把旧容器打死才发现。
"""

import ast
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from agent_lab.scripts.alembic_pre_head import from_config, pre_deploy_head
from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


BACKEND_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

# 真实链上「兼容段」的链尾：三条删列迁移之前的那一条。写死在这里是有意的——它跟着需求走，
# 不跟着实现算。
EXPECTED_PRE_DEPLOY_HEAD = "b7d4e1a9c3f2"

# 延迟到旧容器退出之后再跑的迁移（删列一类），它们必须都带标记。
POST_DEPLOY_MIGRATIONS = [
    VERSIONS_DIR / "c5d8e2a47f91_删除压缩分界标记列_drop_memory_boundary_column.py",
    VERSIONS_DIR / "a3c8e5d719bf_删除会话级与账号级提示词列_drop_system_prompt_columns.py",
    VERSIONS_DIR / "d9f1b6c74e08_删除会话排空标记列_drop_thread_drain_marker_column.py",
]


def write_migration(
    root: Path,
    revision: str,
    down_revision: str | None,
    *,
    post_deploy: bool = False,
) -> None:
    """在临时迁移目录里写一个最小可用的假迁移文件。

    Args:
        root: ``ScriptDirectory`` 指向的目录，版本文件写进它的 ``versions/`` 子目录。
        revision: 本迁移的 revision。
        down_revision: 上一个迁移的 revision，``None`` 表示链头。
        post_deploy: 是否写模块级 ``POST_DEPLOY = True``。
    """

    versions = root / "versions"
    versions.mkdir(exist_ok=True)
    marker = "POST_DEPLOY = True\n" if post_deploy else ""
    down = "None" if down_revision is None else f'"{down_revision}"'
    (versions / f"{revision}_fake.py").write_text(
        f'"""临时迁移：只用于验证边界判定。"""\n'
        f"\n"
        f"{marker}"
        f'revision = "{revision}"\n'
        f"down_revision = {down}\n"
        f"\n"
        f"\n"
        f"def upgrade() -> None:\n"
        f"    pass\n"
        f"\n"
        f"\n"
        f"def downgrade() -> None:\n"
        f"    pass\n",
        encoding="utf-8",
    )


def test_chain_without_markers_returns_the_head(tmp_path: Path) -> None:
    """整条链都没有标记：边界就是 head 自己，兼容段一直跑到链尾。"""

    write_migration(tmp_path, "c3", "b2")
    write_migration(tmp_path, "b2", "a1")
    write_migration(tmp_path, "a1", None)

    assert pre_deploy_head(ScriptDirectory(str(tmp_path))) == "c3"


def test_marked_head_returns_its_predecessor(tmp_path: Path) -> None:
    """只有 head 带标记：边界是它的前一条，head 那条留给换容器之后。"""

    write_migration(tmp_path, "c3", "b2", post_deploy=True)
    write_migration(tmp_path, "b2", "a1")
    write_migration(tmp_path, "a1", None)

    assert pre_deploy_head(ScriptDirectory(str(tmp_path))) == "b2"


def test_consecutive_marked_tail_returns_the_boundary_revision(tmp_path: Path) -> None:
    """链尾连续几条都带标记：返回最后一条不带标记的，中间那几条一起推迟。"""

    write_migration(tmp_path, "d4", "c3", post_deploy=True)
    write_migration(tmp_path, "c3", "b2", post_deploy=True)
    write_migration(tmp_path, "b2", "a1")
    write_migration(tmp_path, "a1", None)

    assert pre_deploy_head(ScriptDirectory(str(tmp_path))) == "b2"


def test_everything_marked_returns_none(tmp_path: Path) -> None:
    """整条链都带标记：兼容段为空，换容器之前一条都不该跑。"""

    write_migration(tmp_path, "b2", "a1", post_deploy=True)
    write_migration(tmp_path, "a1", None, post_deploy=True)

    assert pre_deploy_head(ScriptDirectory(str(tmp_path))) is None


def test_mark_below_the_boundary_does_not_move_it(tmp_path: Path) -> None:
    """标记只出现在更早的位置时不影响结果：判定看的是从 head 到边界这一段。"""

    write_migration(tmp_path, "c3", "b2", post_deploy=True)
    write_migration(tmp_path, "b2", "a1")
    write_migration(tmp_path, "a1", None, post_deploy=True)

    assert pre_deploy_head(ScriptDirectory(str(tmp_path))) == "b2"


def test_multiple_heads_raise_system_exit(tmp_path: Path) -> None:
    """多 head 直接报错：多链时「换容器边界」没有唯一答案，猜一个比报错更危险。"""

    write_migration(tmp_path, "b2", "a1")
    write_migration(tmp_path, "c3", "a1")
    write_migration(tmp_path, "a1", None)

    with pytest.raises(SystemExit) as excinfo:
        pre_deploy_head(ScriptDirectory(str(tmp_path)))

    # 消息里必须带上实际的 heads，否则现场只知道「有多个」却不知道是哪两个。
    assert "b2" in str(excinfo.value)
    assert "c3" in str(excinfo.value)


def test_real_chain_boundary_is_the_last_compatible_migration() -> None:
    """真实链上边界正好是三条删列迁移之前的那一条。"""

    assert from_config(str(ALEMBIC_INI)) == EXPECTED_PRE_DEPLOY_HEAD


@pytest.mark.parametrize("migration", POST_DEPLOY_MIGRATIONS, ids=lambda path: path.name)
def test_deferred_migrations_are_marked_in_source(migration: Path) -> None:
    """推迟执行的迁移源码里都有 ``POST_DEPLOY = True``。

    读源码而不是 import 迁移模块：断言的是「文件里写着这一行」，import 会连带执行迁移模块
    顶层，多出一条与本次判定无关的依赖。
    """

    assert migration.exists(), f"迁移文件不见了：{migration}"
    assert "POST_DEPLOY = True" in migration.read_text(encoding="utf-8")


def _destructive_operations(path: Path) -> list[str]:
    """列出迁移文件里那些「会让旧代码读不下去」的操作。

    用 AST 而不是正则：迁移文件开头那段中文说明里很容易出现 ``op.drop_column`` 这样的字面量，
    正则会把说明当成操作。

    Args:
        path: 迁移文件路径。

    Returns:
        命中的操作名，空列表表示这条迁移对旧代码安全。
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "op"
        ):
            continue
        if func.attr in {"drop_column", "drop_table"}:
            found.append(func.attr)
        elif func.attr == "alter_column" and any(
            keyword.arg == "new_column_name" for keyword in node.keywords
        ):
            found.append("alter_column(new_column_name=)")
    return found


def test_destructive_migrations_after_the_boundary_are_marked() -> None:
    """边界之后新写的破坏性迁移，必须自己写明 ``POST_DEPLOY``。

    这是整套机制唯一靠人的地方：忘写不会报任何错，那条迁移会按老办法在旧容器还在排空时
    执行，把它打死。所以这里逐条扫边界之后的迁移源码——说明里提到这些操作不算，只有真的调
    用了才算。

    只扫边界之后：再往前的那些库里已经执行过、永远不会再跑，标记对它们没有意义，全链扫描
    只会制造一片假红。

    ``op.drop_constraint`` 与 ``op.drop_index`` 刻意不算：拆掉约束不会让旧代码崩（拆外键约束
    那次就是它），而它真正的风险是留下脏数据，静态判不出来；把它们算进来会让一批历史迁移
    全部误报。
    """

    chain = migration_chain()
    assert EXPECTED_PRE_DEPLOY_HEAD in chain, (
        "边界 revision 不在链上：改过链之后要同步 EXPECTED_PRE_DEPLOY_HEAD"
    )
    after = chain[: chain.index(EXPECTED_PRE_DEPLOY_HEAD)]
    assert after, "边界之后没有任何迁移，这条断言要跟着链一起改"

    files = {
        load_migration(path).revision: path
        for path in VERSIONS_DIR.glob("*.py")
        if not path.name.startswith("__")
    }
    offenders: list[str] = []
    for revision in after:
        path = files[revision]
        operations = _destructive_operations(path)
        if operations and "POST_DEPLOY = True" not in path.read_text(encoding="utf-8"):
            offenders.append(f"{path.name}（{sorted(set(operations))}）")

    assert not offenders, (
        "这些迁移里有破坏性操作却没写 POST_DEPLOY = True，会在旧容器还在排空时执行：\n"
        + "\n".join(offenders)
    )
