"""删除压缩分界标记列那条迁移的离线结构校验。

它证明三件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移接在上一个 head（``b7d4e1a9c3f2``）上，并且真的在迁移链里——否则部署的 ``alembic upgrade
   head`` 不会跑到它。
2. 升级只删这一列，别的列与索引一个不动。
3. 回滚只把这一列加回来，可空，并带回建表那次迁移写下的原说明。

它不再是链尾，所以「它就是当前 head」那半条断言随之移到了后面的迁移自己的测试
（先到 ``tests/test_drop_system_prompt_columns_migration.py``，现由
``tests/test_thread_drain_column_drop_migration.py`` 持有），与 ``test_thread_drain_migration``
当初的处理同一做法。

**真库上那一步靠 ``alembic check``**：迁移应用之后，它会逐列比对库与模型（包括说明），干净就说明
这一列真的没了；``downgrade -1`` 之后仍无差异，说明回滚写回的说明与建表时逐字一致。
"""

from typing import Any

from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = VERSIONS_DIR / "c5d8e2a47f91_删除压缩分界标记列_drop_memory_boundary_column.py"

# 本迁移要碰的那一列。
TARGET = ("agent_thread_messages", "memory_boundary_run_id")

# 建表那次迁移写下的原文。刻意在测试里再抄一遍而不是从迁移常量读——回滚要与库里原有那一句逐字
# 一致，跟着常量算出来的期望值等于没断言（常量被顺手改掉时看不出来）。
ORIGINAL_COMMENT = (
    "压缩边界标记：只有摘要行有，记的是被折掉那一段里最后一次提问的运行 id"
    "（「这一轮及其之前的轮次，模型只剩摘要」）。"
)


def test_the_migration_is_wired_after_the_previous_head() -> None:
    """它接在改动前的 head 上，并且在迁移链里。"""

    module = load_migration(MIGRATION_FILE)

    assert module.down_revision == "b7d4e1a9c3f2"
    assert module.revision in migration_chain()


def test_upgrade_drops_only_that_column(monkeypatch: Any) -> None:
    """升级只删这一列，不删别的列、不碰索引、不改类型。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module.op,
        "drop_column",
        lambda table, name: calls.append((table, name)),
    )

    module.upgrade()

    assert calls == [TARGET]


def test_downgrade_adds_the_column_back_with_the_original_comment(monkeypatch: Any) -> None:
    """回滚只加回这一列：可空，说明与建表时逐字一致。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        module.op,
        "add_column",
        lambda table, column: calls.append((table, column)),
    )

    module.downgrade()

    assert len(calls) == 1
    table, column = calls[0]
    assert (table, column.name) == TARGET
    assert column.nullable is True
    assert column.comment == ORIGINAL_COMMENT
    # 与迁移里那份常量同一句：两边分叉的话，真库 `alembic check` 只在回滚之后才红。
    assert module.MEMORY_BOUNDARY_COMMENT == ORIGINAL_COMMENT
