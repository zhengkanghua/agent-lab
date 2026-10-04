"""会话「当前选择的模型」这一列迁移的离线结构校验。

它证明三件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移接在上一个 head 上，并且就是当前 head——否则部署的 ``alembic upgrade head`` 不会跑到它。
2. 升级只加一列、可空，且写入的说明与 ORM 上的注解**逐字一致**（两边分叉就是下一个要修的病，
   所以断言连着模型读，不在测试里再抄一遍字符串）。
3. 回滚只删同一列。

**真库上那一步靠 ``alembic check``**：迁移应用之后，它会逐列比对库与模型（包括说明），
干净就说明这一列真的对齐了。
"""

from typing import Any

from agent_lab.models.agent_thread import AgentThreadRecord
from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = VERSIONS_DIR / "a3f7c2e9b5d4_会话选择模型_thread_selected_llm_model.py"


def test_the_migration_is_wired_after_the_previous_head_and_is_the_head() -> None:
    """它接在改动前的 head 上，而且是链尾——否则部署跑不到它。"""

    module = load_migration(MIGRATION_FILE)

    assert module.down_revision == "d2f5a8c31b76"
    assert migration_chain()[0] == module.revision
    assert module.revision in migration_chain()


def test_the_column_is_nullable_so_an_untouched_session_can_say_it_chose_nothing() -> None:
    """可空，而且没有库上默认值：存量行读出来就是「没选过模型」，用默认模型。"""

    column = AgentThreadRecord.__table__.c["llm_model_id"]

    assert column.nullable is True
    assert column.server_default is None


def test_upgrade_adds_the_column_with_the_orm_comment(monkeypatch: Any) -> None:
    """升级只加 ``agent_threads.llm_model_id`` 一列，说明与 ORM 注解逐字一致。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, Any, Any]] = []
    monkeypatch.setattr(
        module.op,
        "add_column",
        lambda table, column: calls.append((table, column.name, column.nullable, column.comment)),
    )

    module.upgrade()

    assert [(table, name) for table, name, _, _ in calls] == [("agent_threads", "llm_model_id")]
    _table, _name, nullable, comment = calls[0]
    assert nullable is True
    assert comment == AgentThreadRecord.__table__.c["llm_model_id"].comment
    # 正面那一半：注解不为空。上面那条在「模型注解也是 None」时会空转成通过。
    assert comment
    # 逻辑外键这件事必须写在列说明里：库上没有 FOREIGN KEY，读 DDL 的人只能靠这句话知道。
    assert "逻辑外键" in comment


def test_downgrade_drops_only_that_column(monkeypatch: Any) -> None:
    """回滚只删同一列。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module.op,
        "drop_column",
        lambda table, name: calls.append((table, name)),
    )

    module.downgrade()

    assert calls == [("agent_threads", "llm_model_id")]
