"""上游渠道表迁移的离线结构校验。

它证明三件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移接在上一个 head 上、并且挂在迁移链里——否则部署的 ``alembic upgrade head`` 不会跑到它。
   （「它就是最新 head」那半条只看最新那一条迁移：后来又加了会话历史消息表，这个文件已经不是
   链尾，所以那半条断言已经随新迁移移到了 ``test_agent_thread_messages_migration``。）
2. 升级只建 ``llm_providers`` 一张表，列名、可空性、说明与表说明与 ORM **逐字一致**（两边分叉
   就是下一个要修的病，所以断言连着模型读，不在测试里再抄一遍字符串）。
3. 回滚只删同一张表。

**真库上那一步靠 ``alembic check``**：迁移应用之后，它会逐列比对库与模型（包括说明），干净就
说明这张表真的对齐了——那才是这条迁移的验收。
"""

from typing import Any

import sqlalchemy as sa

from agent_lab.models.llm_provider import LlmProviderRecord
from tests.migration_helpers import VERSIONS_DIR, load_migration, migration_chain


MIGRATION_FILE = VERSIONS_DIR / "c4a8f1d6b2e7_新增上游渠道表_add_llm_providers.py"


def test_the_migration_is_wired_after_the_previous_head() -> None:
    """它接在改动前的 head 上，而且在迁移链里——否则部署的 ``alembic upgrade head`` 不会跑到它。

    这条以前还包括「它就是最新 head」。新增迁移（会话历史消息表）之后不再成立，所以那半条断言
    挪到了新迁移自己的测试里（与 ``test_thread_drain_migration`` 当初的处理同一做法）；这里只守
    「挂在链上、``down_revision`` 对」。
    """

    module = load_migration(MIGRATION_FILE)

    assert module.revision in migration_chain()
    assert module.down_revision == "f2b9c7d41a08"


def test_upgrade_creates_the_table_with_the_orm_columns_and_comments(
    monkeypatch: Any,
) -> None:
    """升级只建 ``llm_providers``，每一列与表说明都和 ORM 上的注解逐字一致。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, list[Any], dict[str, Any]]] = []
    monkeypatch.setattr(
        module.op,
        "create_table",
        lambda name, *items, **kwargs: calls.append((name, list(items), kwargs)),
    )

    module.upgrade()

    assert [name for name, _, _ in calls] == ["llm_providers"]
    _name, items, kwargs = calls[0]
    columns = [item for item in items if isinstance(item, sa.Column)]
    table = LlmProviderRecord.__table__

    assert [column.name for column in columns] == [column.name for column in table.columns]
    for column in columns:
        assert column.comment == table.c[column.name].comment, column.name
        assert column.nullable == table.c[column.name].nullable, column.name
        # 正面那一半：说明不为空。上面那条在「模型注解也是 None」时会空转成通过。
        assert column.comment, column.name

    # 三个有服务端默认值的列：少了它们，不走 ORM 的 INSERT 会直接失败。
    for name in ("enabled", "created_at", "updated_at"):
        assert next(column for column in columns if column.name == name).server_default is not None

    assert kwargs["comment"] == table.comment
    assert kwargs["comment"]
    # 主键约束两边都是无名约束（模型没声明名字），名字由数据库生成。
    assert [
        item.name for item in items if isinstance(item, sa.PrimaryKeyConstraint)
    ] == [table.primary_key.name]


def test_upgrade_adds_no_other_object(monkeypatch: Any) -> None:
    """本迁移只建表：不建索引、不写种子数据、不动别的表。"""

    module = load_migration(MIGRATION_FILE)
    touched: list[str] = []
    for name in (
        "create_table",
        "create_index",
        "add_column",
        "drop_table",
        "execute",
        "alter_column",
    ):
        monkeypatch.setattr(
            module.op, name, lambda *args, _name=name, **kwargs: touched.append(_name)
        )

    module.upgrade()

    assert touched == ["create_table"]


def test_downgrade_drops_only_that_table(monkeypatch: Any) -> None:
    """回滚只删同一张表。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[str] = []
    monkeypatch.setattr(module.op, "drop_table", lambda name: calls.append(name))

    module.downgrade()

    assert calls == ["llm_providers"]
