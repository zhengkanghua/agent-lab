"""删除会话排空标记列那条迁移的离线结构校验。

它证明三件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移接在上一个 head（``a3c8e5d719bf``）上，并且就是当前 head——否则部署的 ``alembic upgrade
   head`` 不会跑到它。这条链尾断言从上一个迁移的测试里挪过来，与 ``test_thread_drain_migration``
   当初的处理同一做法。
2. 升级只删这一列，别的列与索引一个不动。
3. 回滚只把这一列加回来：可空，且说明与加列那次迁移写下的原文逐字一致。

**真库上那一步靠 ``alembic check``**：迁移应用之后，它会逐列比对库与模型（包括说明），干净就说明
这一列真的没了；``downgrade -1`` 之后仍无差异，说明回滚写回的说明与加列时逐字一致。
"""

from typing import Any

from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = (
    VERSIONS_DIR / "d9f1b6c74e08_删除会话排空标记列_drop_thread_drain_marker_column.py"
)
# 加列那次迁移：本迁移回滚要带回去的说明出自它的常量。
ADDING_MIGRATION_FILE = VERSIONS_DIR / "f2b9c7d41a08_会话排空标记_thread_drain_marker.py"

# 本迁移要碰的那一列。
TARGET = ("agent_threads", "drained_at")

# 加列那次迁移写下的原文。刻意在测试里再抄一遍而不是只从迁移常量读——回滚要与库里原有那一句逐字
# 一致，跟着常量算出来的期望值等于没断言（常量被顺手改掉时看不出来）。
ORIGINAL_COMMENT = (
    "被排空的时刻；非空表示这个会话的在途运行已停在可交接的边界、等别的进程接手。业务层"
    "维护的排空标记：被排空的进程写下，接手者在抢占所有权的那次条件写入里消费（置空）；"
    "带标记的会话不接受新提问（占位路径的失活分支排除它），见 ADR 0040。"
)


def test_the_migration_is_wired_after_the_previous_head_and_is_the_head() -> None:
    """它接在改动前的 head 上，而且是链尾。"""

    module = load_migration(MIGRATION_FILE)

    assert module.down_revision == "a3c8e5d719bf"
    assert migration_chain()[0] == module.revision
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
    """回滚只加回这一列：可空，说明与加列时逐字一致。"""

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
    assert module.DRAINED_COMMENT == ORIGINAL_COMMENT
    # 也要与加列那次迁移的常量一致：说明只有一份原文，不该在两条迁移里各写一遍还给岔了。
    assert module.DRAINED_COMMENT == load_migration(ADDING_MIGRATION_FILE).DRAINED_COMMENT
