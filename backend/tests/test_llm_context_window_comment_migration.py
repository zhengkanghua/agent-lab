"""``llm_models.context_window`` 列说明同步那条迁移的离线结构校验。

它证明三件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移接在上一个 head 上，并且就是当前 head——否则部署的 ``alembic upgrade head`` 不会跑到它。
2. 升级只碰这一列，且写入的说明与 ORM 上的注解**逐字一致**——两边分叉正是这次要修的病，所以断言
   连着模型读，不在测试里再抄一遍字符串。
3. 回滚只碰同一列，且改回建表当时的措辞。

**真库上那一步靠 ``alembic check``**：迁移随部署的 ``alembic upgrade head`` 应用之后，它会逐列
比对库与模型（包括说明），干净就说明这一列真的对齐了。
"""

from typing import Any

from agent_lab.models.llm_model import LlmModelRecord
from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = VERSIONS_DIR / "b7d4e1a9c3f2_同步上下文窗口列说明_sync_context_window_comment.py"

# 本迁移要碰的那一列。
TARGET = ("llm_models", "context_window")


def test_the_migration_is_wired_after_the_previous_head_and_is_the_head() -> None:
    """它接在改动前的 head 上，而且是链尾——否则部署跑不到它。"""

    module = load_migration(MIGRATION_FILE)

    assert module.down_revision == "a3f7c2e9b5d4"
    assert migration_chain()[0] == module.revision
    assert module.revision in migration_chain()


def test_upgrade_writes_the_orm_comment_verbatim(monkeypatch: Any) -> None:
    """升级写进库的说明与 ORM 上的注解逐字一致，且只碰这一列。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(
        module.op,
        "alter_column",
        lambda table, column, **kwargs: calls.append((table, column, kwargs.get("comment"))),
    )

    module.upgrade()

    assert [(table, column) for table, column, _ in calls] == [TARGET]
    expected = LlmModelRecord.__table__.c["context_window"].comment
    assert calls[0][2] == expected
    # 正面那一半：注解不为空。上面那条在「模型注解也是 None」时会空转成通过。
    assert expected
    # 被撤掉的那条规则不能从注释里回来：这句话曾经把窗口说成工具输出的长度上限。
    assert "工具输出" not in expected


def test_downgrade_restores_the_previous_wording(monkeypatch: Any) -> None:
    """回滚只碰同一列，改回建表那次迁移的旧措辞，不动别的列。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(
        module.op,
        "alter_column",
        lambda table, column, **kwargs: calls.append((table, column, kwargs.get("comment"))),
    )

    module.downgrade()

    assert [(table, column) for table, column, _ in calls] == [TARGET]
    assert calls[0][2] == module.PREVIOUS_CONTEXT_WINDOW_COMMENT
    assert calls[0][2] != module.CONTEXT_WINDOW_COMMENT
