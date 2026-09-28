"""同步三处列说明那条迁移的离线结构校验。

它证明两件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移只碰那三列，且写入的说明文字与 ORM 上的注解**逐字一致**——两边分叉就是这次要修的病，所以断言
   必须连着模型读，不能把字符串在测试里再抄一遍（抄一遍等于只证明「文件里写了什么」，证明不了「和代码一致」）。
2. 回滚只碰同一组列（不整表刷新），且把两列改回「没有说明」、第三列改回旧措辞。

**真库上那一步靠 ``alembic check``**：迁移随部署的 ``alembic upgrade head`` 应用之后，``alembic check``
会逐列比对库与模型（包括说明），干净就说明这三处真的对齐了——那才是这条迁移的验收。
"""

from typing import Any

from agent_lab.models.user import UserRecord
from agent_lab.models.user_preference import UserPreferenceRecord
from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = VERSIONS_DIR / "e1a7c4b93d62_同步三处与代码对不上的列说明_sync_column_comments.py"

# 迁移要碰的三列。顺序与 ``upgrade`` 里的调用顺序一致。
TARGETS = (
    ("user_preferences", "created_at"),
    ("user_preferences", "updated_at"),
    ("users", "is_environment_admin"),
)


def columns_of(table: str) -> Any:
    """按表名取 ORM 上的列集合。"""

    return {
        "user_preferences": UserPreferenceRecord.__table__.c,
        "users": UserRecord.__table__.c,
    }[table]


def test_the_migration_is_wired_after_the_previous_head() -> None:
    """它接在原来的 head 上，并且是最新的 head——否则部署时不会被跑到。"""

    module = load_migration(MIGRATION_FILE)

    assert migration_chain()[0] == module.revision
    assert module.down_revision == "d5f8a2c7b9e1"


def test_upgrade_writes_the_orm_comments_verbatim(monkeypatch) -> None:
    """升级写入的说明与 ORM 上的注解逐字一致，且只碰这三列。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(
        module.op,
        "alter_column",
        lambda table, column, **kwargs: calls.append((table, column, kwargs.get("comment"))),
    )

    module.upgrade()

    assert [(table, column) for table, column, _ in calls] == list(TARGETS)
    expected = tuple(columns_of(table)[column].comment for table, column in TARGETS)
    assert tuple(comment for _, _, comment in calls) == expected
    # 正面那一半：三句都不为空。上面那条断言在「模型注解也是 None」时会空转成通过。
    assert all(comment for comment in expected)


def test_downgrade_only_touches_the_same_columns(monkeypatch) -> None:
    """回滚只碰同一组列：两列清掉说明，第三列改回旧措辞，不动别的列。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(
        module.op,
        "alter_column",
        lambda table, column, **kwargs: calls.append((table, column, kwargs.get("comment"))),
    )

    module.downgrade()

    assert [(table, column) for table, column, _ in calls] == list(TARGETS)
    comments = [comment for _, _, comment in calls]
    # ``None`` 在 alembic 里表示「清掉说明」（``False`` 才是「不改」），所以这两列必须显式给 None。
    assert comments[:2] == [None, None]
    assert comments[2] == module.PREVIOUS_ENVIRONMENT_ADMIN_COMMENT
    assert comments[2] != module.ENVIRONMENT_ADMIN_COMMENT
