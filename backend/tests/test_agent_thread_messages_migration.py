"""会话历史消息表迁移的离线结构校验。

它证明四件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移接在上一个 head 上，且在迁移链里（「它就是当前 head」那半条后来又随新迁移挪到了那个文件的
   测试里：可用模型表建在它之后）。
2. 升级只建 ``agent_thread_messages`` 一张表与它的两个索引，列名、类型、可空性、说明与表说明与 ORM
   **逐字一致**（两边分叉就是下一个要修的病，所以断言连着模型读，不在测试里再抄一遍字符串）。
3. 唯一键只建在（会话，顺序号）上，**不把运行 id 算进来**——顺序号是会话内递增的，读的时候只按它
   排序；把运行 id 加进去等于换了一套语义。
4. 回滚只删同一张表与它的索引。

**真库上那一步靠 ``alembic check``**：迁移应用之后，它会逐列比对库与模型（包括说明），干净就说明
这张表真的对齐了——那才是这条迁移的验收。
"""

from typing import Any

import sqlalchemy as sa

from agent_lab.models.agent_thread_message import AgentThreadMessageRecord
from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = (
    VERSIONS_DIR / "e9b3c7a41d58_新增会话历史消息表_add_agent_thread_messages.py"
)

TABLE_NAME = "agent_thread_messages"


def test_the_migration_is_wired_after_the_previous_head() -> None:
    """它接在改动前的 head 上，且在迁移链里。

    「自己就是最新 head」那半条曾经在这里，后来又加了「可用模型」表，于是随新迁移挪到了
    ``test_llm_models_migration``（与上游渠道表当初挪到本文件是同一种做法）：一条迁移是不是链尾，
    只有最新那一条的测试说得准，放在旧的测试里只会随新迁移变红。
    """

    module = load_migration(MIGRATION_FILE)

    assert module.down_revision == "c4a8f1d6b2e7"
    assert module.revision in migration_chain()


def _created_table(module: Any, monkeypatch: Any) -> tuple[list[Any], dict[str, Any]]:
    """跑一次 ``upgrade``，返回建表时给出的列与关键字参数。"""

    calls: list[tuple[str, list[Any], dict[str, Any]]] = []
    monkeypatch.setattr(
        module.op,
        "create_table",
        lambda name, *items, **kwargs: calls.append((name, list(items), kwargs)),
    )
    monkeypatch.setattr(module.op, "create_index", lambda *args, **kwargs: None)
    module.upgrade()
    assert [name for name, _, _ in calls] == [TABLE_NAME]
    _name, items, kwargs = calls[0]
    return items, kwargs


def test_upgrade_creates_the_table_with_the_orm_columns_and_comments(
    monkeypatch: Any,
) -> None:
    """升级只建这张表，每列的类型、可空性与说明都和 ORM 上的注解逐字一致。"""

    module = load_migration(MIGRATION_FILE)
    items, kwargs = _created_table(module, monkeypatch)
    columns = [item for item in items if isinstance(item, sa.Column)]
    table = AgentThreadMessageRecord.__table__

    assert [column.name for column in columns] == [column.name for column in table.columns]
    for column in columns:
        expected = table.c[column.name]
        assert str(column.type) == str(expected.type), column.name
        assert column.nullable == expected.nullable, column.name
        assert column.comment == expected.comment, column.name
        # 正面那一半：说明不为空。上面那条在「模型注解也是 None」时会空转成通过。
        assert column.comment, column.name

    assert kwargs["comment"] == table.comment
    assert kwargs["comment"]
    # 主键约束两边都是无名约束（模型没声明名字），名字由数据库生成。
    assert [
        item.name for item in items if isinstance(item, sa.PrimaryKeyConstraint)
    ] == [table.primary_key.name]


def test_the_unique_key_covers_the_session_and_the_sequence_only(monkeypatch: Any) -> None:
    """唯一键是（会话，顺序号）：兜底防的是撞号，不是「一组内唯一」。"""

    module = load_migration(MIGRATION_FILE)
    items, _kwargs = _created_table(module, monkeypatch)
    # 把建表时给出的那些对象拼成一张表再读：约束的列是在这里才与表关联上的。
    built = sa.Table(TABLE_NAME, sa.MetaData(), *items)
    unique = [item for item in built.constraints if isinstance(item, sa.UniqueConstraint)]

    assert len(unique) == 1
    assert [column.name for column in unique[0].columns] == ["thread_id", "seq"]
    # 模型上声明的那一份必须同名同列：名字或列对不上，``alembic check`` 会把它当成两套。
    modelled = [
        constraint
        for constraint in AgentThreadMessageRecord.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    ]
    assert len(modelled) == 1
    assert unique[0].name == modelled[0].name
    assert [column.name for column in modelled[0].columns] == ["thread_id", "seq"]


def test_upgrade_adds_the_index_that_the_writer_reads_by(monkeypatch: Any) -> None:
    """升级还建一个（会话，运行）索引：写入方每次收尾都按这两列读与删。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, list[str]]] = []
    monkeypatch.setattr(
        module.op,
        "create_table",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        module.op,
        "create_index",
        lambda name, table, columns, **kwargs: calls.append((name, table, list(columns))),
    )
    module.upgrade()

    assert calls == [
        ("ix_agent_thread_messages_thread_id_run_id", TABLE_NAME, ["thread_id", "run_id"])
    ]
    # 模型上声明的索引必须一模一样，否则 ``alembic check`` 会把它当成多余的索引再删一遍。
    modelled = AgentThreadMessageRecord.__table__.indexes
    assert [index.name for index in modelled] == [calls[0][0]]
    assert [column.name for index in modelled for column in index.columns] == calls[0][2]


def test_downgrade_drops_the_index_and_the_table(monkeypatch: Any) -> None:
    """回滚先删索引再删表，而且只碰这一张表。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module.op,
        "drop_index",
        lambda name, table_name: calls.append(("drop_index", f"{table_name}.{name}")),
    )
    monkeypatch.setattr(
        module.op,
        "drop_table",
        lambda name: calls.append(("drop_table", name)),
    )

    module.downgrade()

    assert calls == [
        ("drop_index", f"{TABLE_NAME}.ix_agent_thread_messages_thread_id_run_id"),
        ("drop_table", TABLE_NAME),
    ]
