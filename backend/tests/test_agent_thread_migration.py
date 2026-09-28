"""会话记录新增「在途运行 / 停止请求」两列那条迁移的离线结构校验。

**为什么不用 ``create_all`` 代替**：``create_all`` 按 ORM 模型建表，而「模型里有这两列」与「迁移把
它们加进了已有的库」是两件事。真实的升级路径是 ``alembic upgrade head``，所以这里直接调迁移模块的
``upgrade`` / ``downgrade``，只把 ``alembic.op`` 换成记录器——断言的是「迁移让我们对外承诺的 DDL」，
不需要真库，也不会在开发者的库上留下任何东西。

**真库上那一步由两条用例补**：``test_agent_thread_ownership_integration.py`` 里的归属与串行用例（它要
先 ``alembic upgrade head``）。这里证明的是「迁移写了什么」，那里证明「跑完之后真的按预期生效」。
"""

import sqlalchemy as sa

from agent_lab.models.agent_thread import AgentThreadRecord
from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = (
    VERSIONS_DIR / "d5f8a2c7b9e1_会话记录在途运行与停止请求_thread_run_coordination.py"
)


def test_the_migration_is_wired_onto_the_current_head() -> None:
    """这条迁移真的在 head 回溯链上，否则它的 upgrade 永远不会被跑到。"""

    module = load_migration(MIGRATION_FILE)

    assert module.revision in migration_chain()
    # 它当初接在当时的 head 上——写死这个父节点是为了挡住「顺手改了 down_revision」这类改动：
    # 那种改动会让它（或它的后继）从链上掉下来，而链完整性检查在这里发现不了。
    assert module.down_revision == "c1f4a7d92e60"


def test_upgrade_adds_both_columns_and_refreshes_the_last_active_comment(monkeypatch) -> None:
    """升级新增两列（都可空、带列说明），并把最后活跃时间改成「最后活动时刻」。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        module.op, "add_column", lambda table, column: calls.append(("add", table, column.name))
    )
    monkeypatch.setattr(
        module.op,
        "alter_column",
        lambda table, name, **kwargs: calls.append(("alter", table, name)),
    )

    module.upgrade()

    assert calls == [
        ("add", "agent_threads", "active_run_id"),
        ("add", "agent_threads", "stop_requested_at"),
        ("alter", "agent_threads", "last_active_at"),
    ]


def test_downgrade_removes_the_columns_and_restores_the_previous_comment(monkeypatch) -> None:
    """降级删掉两列，并把列说明改回旧语义——可升可降才叫迁移。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        module.op, "drop_column", lambda table, name: calls.append(("drop", table, name))
    )
    monkeypatch.setattr(
        module.op,
        "alter_column",
        lambda table, name, **kwargs: calls.append(("alter", table, name)),
    )

    module.downgrade()

    assert calls == [
        ("alter", "agent_threads", "last_active_at"),
        ("drop", "agent_threads", "stop_requested_at"),
        ("drop", "agent_threads", "active_run_id"),
    ]


def test_the_migration_and_the_model_describe_the_same_columns() -> None:
    """迁移里那份列说明与 ORM 上的注解必须一致。

    两边分叉的后果不是报错，而是 ``alembic check`` 在下一次迁移时把它当成「说明变了」再改一遍，
    或者更糟——库里的注解停留在旧语义，而读注释的人正是为了弄清那一列什么意思（ADR 0037 明确
    要求把 ``last_active_at`` 的语义改成「最后活动时刻」）。
    """

    module = load_migration(MIGRATION_FILE)
    columns = AgentThreadRecord.__table__.c

    assert columns["last_active_at"].comment == module.LAST_ACTIVE_COMMENT
    assert columns["active_run_id"].comment == module.ACTIVE_RUN_COMMENT
    assert columns["stop_requested_at"].comment == module.STOP_REQUESTED_COMMENT
    # 两列都必须可空：存量行没有「在途运行」可填，而「没有运行在跑」正是用空值表达的。
    assert columns["active_run_id"].nullable is True
    assert columns["stop_requested_at"].nullable is True
    assert isinstance(columns["active_run_id"].type, sa.Uuid)
    assert isinstance(columns["stop_requested_at"].type, sa.DateTime)
    assert columns["stop_requested_at"].type.timezone is True
