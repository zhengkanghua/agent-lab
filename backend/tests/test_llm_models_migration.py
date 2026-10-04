"""可用模型表与它那条部分唯一索引的离线结构校验。

它证明四件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移接在上一个 head 上、并且挂在迁移链里——否则部署的 ``alembic upgrade head`` 不会
   跑到它。（「它是当前链尾」那半条随着会话选择模型那条迁移落地而移到了它的测试里，与
   ``test_thread_drain_migration`` 当初的处理同一做法。）
2. 升级只建 ``llm_models`` 一张表；列名、可空性、说明与表说明与 ORM **逐字一致**（两边分叉
   就是下一个要修的病，所以断言连着模型读，不在测试里再抄一遍字符串）。
3. 升级另建且只建 ``uq_llm_models_single_default`` 这一条**部分唯一索引**，且它的谓词与 ORM 上
   那一份是同一句原文——「目录里恰好有一个默认」在并发下靠的就是它。**模型与迁移两边都断言**：
   只断言一侧的话，另一侧将来改掉谓词不会被发现，而那个改动会让索引再也兜不住并发。
4. 回滚只删这条索引与同一张表。

**真库上那一步靠 ``alembic check``**：迁移应用之后它会逐列比对库与模型（包括说明），干净就说明
这张表真的对齐了。部分唯一索引的谓词是否真的落到了库上，只有真库（或这条结构断言）说得清。
"""

from typing import Any

import sqlalchemy as sa

from agent_lab.models.llm_model import LlmModelRecord
from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = VERSIONS_DIR / "d2f5a8c31b76_新增可用模型表_add_llm_models.py"
INDEX_NAME = "uq_llm_models_single_default"


def capture_ops(monkeypatch: Any, module: Any) -> dict[str, list[tuple[tuple, dict]]]:
    """把本迁移用到的 op 方法都换成记录器，返回 ``{方法名: [(位置参数, 关键字参数)]}``。

    **一次升级会连调好几个 op 方法**（建表 + 建索引），只替换其中一个是行不通的：没被替换的
    那个会去碰真实的 Alembic 代理，报「proxy object has not yet been established」，看起来
    像迁移写错了。
    """

    calls: dict[str, list[tuple[tuple, dict]]] = {
        name: [] for name in ("create_table", "create_index")
    }
    for op_name in calls:
        monkeypatch.setattr(
            module.op,
            op_name,
            lambda *args, _name=op_name, **kwargs: calls[_name].append((args, kwargs)),
        )
    return calls


def test_the_migration_is_wired_after_the_previous_head() -> None:
    """它接在改动前的 head 上，并且真的在迁移链里——否则部署时不会被跑到。

    后半条（它是当前 head）会随着更新的迁移落地而失效；那时把它留给那条新迁移自己的测试
    断言（与 ``test_thread_drain_migration`` 当初的处理同一做法），这里仍守着
    ``down_revision`` 与本迁移在链上。
    """

    module = load_migration(MIGRATION_FILE)

    assert module.down_revision == "e9b3c7a41d58"
    assert module.revision in migration_chain()


def test_the_model_declares_the_partial_unique_index_over_the_default_flag() -> None:
    """ORM 上那条索引是「唯一 + 只覆盖 is_default 为真的行」。"""

    indexes = {index.name: index for index in LlmModelRecord.__table__.indexes}

    index = indexes[INDEX_NAME]
    assert index.unique is True
    assert [column.name for column in index.columns] == ["is_default"]
    assert str(index.dialect_options["postgresql"]["where"]) == "is_default"


def test_upgrade_creates_the_table_with_the_orm_columns_and_comments(
    monkeypatch: Any,
) -> None:
    """升级只建 ``llm_models``，每一列与表说明都和 ORM 上的注解逐字一致。"""

    module = load_migration(MIGRATION_FILE)
    calls = capture_ops(monkeypatch, module)

    module.upgrade()

    assert [args[0] for args, _kwargs in calls["create_table"]] == ["llm_models"]
    args, kwargs = calls["create_table"][0]
    items = list(args[1:])
    columns = [item for item in items if isinstance(item, sa.Column)]
    table = LlmModelRecord.__table__

    assert [column.name for column in columns] == [column.name for column in table.columns]
    for column in columns:
        assert column.comment == table.c[column.name].comment, column.name
        assert column.nullable == table.c[column.name].nullable, column.name
        # 正面那一半：说明不为空。上面那条在「模型注解也是 None」时会空转成通过。
        assert column.comment, column.name

    # 四个有服务端默认值的列：少了它们，不走 ORM 的 INSERT 会直接失败。
    for name in ("is_default", "enabled", "created_at", "updated_at"):
        assert next(column for column in columns if column.name == name).server_default is not None

    assert kwargs["comment"] == table.comment
    assert kwargs["comment"]
    # 主键约束两边都是无名约束（模型没声明名字），名字由数据库生成。
    assert [
        item.name for item in items if isinstance(item, sa.PrimaryKeyConstraint)
    ] == [table.primary_key.name]
    # 「同一渠道内上游模型名唯一」由表内唯一约束表达，不是另建一条索引；迁移里那个约束对象
    # 在挂到表上之前拿不到已解析的列（列名还是字符串），所以列在 ORM 那一侧断言，两边比名字。
    orm_unique = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    )
    assert [
        item.name for item in items if isinstance(item, sa.UniqueConstraint)
    ] == [orm_unique.name]
    assert [column.name for column in orm_unique.columns] == [
        "provider_id",
        "upstream_model_name",
    ]


def test_upgrade_creates_the_partial_unique_index_with_the_same_predicate(
    monkeypatch: Any,
) -> None:
    """升级另建的那条索引与 ORM 上那一份同名、同列、同谓词。"""

    module = load_migration(MIGRATION_FILE)
    calls = capture_ops(monkeypatch, module)

    module.upgrade()

    assert len(calls["create_index"]) == 1
    args, kwargs = calls["create_index"][0]
    name, table_name, columns = args
    model_index = next(
        index for index in LlmModelRecord.__table__.indexes if index.name == INDEX_NAME
    )
    assert (name, table_name, [column for column in columns]) == (
        INDEX_NAME,
        "llm_models",
        [column.name for column in model_index.columns],
    )
    assert kwargs["unique"] is model_index.unique
    assert str(kwargs["postgresql_where"]) == str(
        model_index.dialect_options["postgresql"]["where"]
    )


def test_upgrade_builds_no_other_object(monkeypatch: Any) -> None:
    """本迁移只建这张表与它那条索引：不写种子数据、不动别的表。"""

    module = load_migration(MIGRATION_FILE)
    touched: list[str] = []
    for name in (
        "create_table",
        "create_index",
        "add_column",
        "drop_table",
        "drop_index",
        "execute",
        "alter_column",
    ):
        monkeypatch.setattr(
            module.op, name, lambda *args, _name=name, **kwargs: touched.append(_name)
        )

    module.upgrade()

    assert touched == ["create_table", "create_index"]


def test_downgrade_drops_the_index_and_only_that_table(monkeypatch: Any) -> None:
    """回滚先删索引再删表，一步都不多。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(
        module.op, "drop_index", lambda name, **kwargs: calls.append(("drop_index", (name, kwargs)))
    )
    monkeypatch.setattr(
        module.op, "drop_table", lambda name: calls.append(("drop_table", (name,)))
    )

    module.downgrade()

    assert calls == [
        ("drop_index", (INDEX_NAME, {"table_name": "llm_models"})),
        ("drop_table", ("llm_models",)),
    ]
