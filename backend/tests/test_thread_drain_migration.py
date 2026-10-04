"""会话排空标记迁移的离线结构校验。

它证明三件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移接在上一个 head 上，并且就是当前 head——否则部署的 ``alembic upgrade head`` 不会跑到它。
2. 升级只加一列，且写入的说明与 ORM 上的注解**逐字一致**（两边分叉就是下一个要修的病，所以断言
   必须连着模型读，不能在测试里再抄一遍字符串）。
3. 回滚只删同一列。

**真库上那一步靠 ``alembic check``**：迁移应用之后，``alembic check`` 会逐列比对库与模型（包括
说明），干净就说明这一列真的对齐了——那才是这条迁移的验收。
"""

from typing import Any

from agent_lab.models.agent_thread import AgentThreadRecord
from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = VERSIONS_DIR / "f2b9c7d41a08_会话排空标记_thread_drain_marker.py"


def test_the_migration_is_wired_after_the_previous_head() -> None:
    """它接在原来的 head 上，并且真的在迁移链里——否则部署时不会被跑到。

    这条以前还包括「它就是最新 head」。新增迁移（上游渠道表）之后不再成立，所以那半条断言
    移到了新迁移自己的测试里（与 ``test_column_comments_migration`` 当初的处理同一做法）；
    这里只守「挂在链上、down_revision 对」。
    """

    module = load_migration(MIGRATION_FILE)

    assert module.revision in migration_chain()
    assert module.down_revision == "e1a7c4b93d62"


def test_upgrade_adds_the_drained_column_with_the_orm_comment(monkeypatch: Any) -> None:
    """升级只加 ``agent_threads.drained_at`` 一列，说明与 ORM 注解逐字一致。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, Any, Any]] = []
    monkeypatch.setattr(
        module.op,
        "add_column",
        lambda table, column: calls.append((table, column.name, column.nullable, column.comment)),
    )

    module.upgrade()

    assert [(table, name) for table, name, _, _ in calls] == [("agent_threads", "drained_at")]
    _table, _name, nullable, comment = calls[0]
    assert nullable is True
    assert comment == AgentThreadRecord.__table__.c["drained_at"].comment
    # 正面那一半：注解不为空。上面那条在「模型注解也是 None」时会空转成通过。
    assert comment


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

    assert calls == [("agent_threads", "drained_at")]
