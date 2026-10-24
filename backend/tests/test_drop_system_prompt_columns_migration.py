"""删除两处提示词列那条迁移的离线结构校验。

它证明三件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移接在上一个 head（``c5d8e2a47f91``）上，并且真的在迁移链里——否则部署的 ``alembic upgrade
   head`` 不会跑到它。「它就是当前 head」那半条随下一个删列迁移（删排空标记列）一起接到了
   ``tests/test_thread_drain_column_drop_migration.py`` 里，与 ``test_thread_drain_migration``
   当初的处理同一做法，两处不各说一遍。
2. 升级只删这两列（各一次 ``drop_column``），别的列与索引一个不动。
3. 回滚只把这两列加回来：可空，且说明与建表那次迁移写下的原文逐字一致。

**真库上那一步靠 ``alembic check``**：迁移应用之后，它会逐列比对库与模型（包括说明），干净就说明
这两列真的没了；``downgrade -1`` 之后仍无差异，说明回滚写回的说明与建表时逐字一致。
"""

from typing import Any

from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = (
    VERSIONS_DIR / "a3c8e5d719bf_删除会话级与账号级提示词列_drop_system_prompt_columns.py"
)

# 本迁移要碰的两列，顺序与迁移里的写法一致。
THREAD_TARGET = ("agent_threads", "system_prompt")
PREFERENCE_TARGET = ("user_preferences", "system_prompt")

# 建表那次迁移（``e2c8f14b7a30``）写下的原文。刻意在测试里再抄一遍而不是从迁移常量读——回滚要与
# 库里原有那两句逐字一致，跟着常量算出来的期望值等于没断言（常量被顺手改掉时看不出来）。
THREAD_ORIGINAL_COMMENT = (
    "会话建立时从该账号个人偏好拍下的系统提示词快照；为空表示用服务端内置默认提示词。"
)
PREFERENCE_ORIGINAL_COMMENT = "自定义系统提示词；为空表示使用服务端内置默认提示词。"


def test_the_migration_is_wired_after_the_previous_head() -> None:
    """它接在改动前的 head 上，并且在迁移链里。"""

    module = load_migration(MIGRATION_FILE)

    assert module.down_revision == "c5d8e2a47f91"
    assert module.revision in migration_chain()


def test_upgrade_drops_both_columns_and_nothing_else(monkeypatch: Any) -> None:
    """升级各删这两列，不删别的列、不碰索引、不改类型。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module.op,
        "drop_column",
        lambda table, name: calls.append((table, name)),
    )

    module.upgrade()

    assert calls == [THREAD_TARGET, PREFERENCE_TARGET]


def test_downgrade_adds_both_columns_back_with_the_original_comments(
    monkeypatch: Any,
) -> None:
    """回滚把两列都加回来：可空，说明与建表时逐字一致。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        module.op,
        "add_column",
        lambda table, column: calls.append((table, column)),
    )

    module.downgrade()

    assert [(table, column.name) for table, column in calls] == [
        PREFERENCE_TARGET,
        THREAD_TARGET,
    ]
    added = {table: column for table, column in calls}
    thread_column = added["agent_threads"]
    preference_column = added["user_preferences"]
    assert thread_column.nullable is True
    assert preference_column.nullable is True
    assert thread_column.comment == THREAD_ORIGINAL_COMMENT
    assert preference_column.comment == PREFERENCE_ORIGINAL_COMMENT
    # 与迁移里那两份常量同一句：两边分叉的话，真库 `alembic check` 只在回滚之后才红。
    assert module.THREAD_PROMPT_COMMENT == THREAD_ORIGINAL_COMMENT
    assert module.PREFERENCE_PROMPT_COMMENT == PREFERENCE_ORIGINAL_COMMENT
