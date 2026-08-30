"""Checkpointer 表名、连接串转换和 Alembic 排除规则的测试。

本文件守护 ADR 0004 的两个机械后果：

1. **Alembic 必须看不见那四张表**：它们不在 ``Base.metadata`` 里，所以 autogenerate 会把
   它们当成「库里多出来的表」，生成 ``op.drop_table('checkpoints')``。那条迁移一旦执行，
   全部会话历史消失。这个 bug 的可怕之处在于它不会在 review 时显眼——它长得就像一条正常
   的清理迁移。
2. **连接串前缀必须被剥掉**：SQLAlchemy 的 ``postgresql+psycopg://`` 前缀 psycopg 不认，
   带着它建连会直接失败。

本文件不连接数据库：``setup_checkpointer_tables`` 属于数据库结构写入，由部署时的
``agent-lab init-checkpointer`` 显式执行，不在测试里跑。
"""

from psycopg.conninfo import conninfo_to_dict

from agent_lab.agent.checkpointer import (
    CHECKPOINTER_TABLE_NAMES,
    include_object,
    to_psycopg_conninfo,
)
from agent_lab.db.base import Base


def test_the_four_checkpointer_tables_are_named() -> None:
    """表名集合必须正好是 LangGraph 建的那四张。

    少一张的后果是那张会被 autogenerate 当成待删表。多一张（比如误加业务表）的后果更隐蔽：
    那张业务表会从此不进迁移，结构漂移无人察觉。
    """

    assert CHECKPOINTER_TABLE_NAMES == {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }


def test_checkpointer_tables_are_not_orm_tables() -> None:
    """这四张表不能同时出现在 ORM metadata 里。

    真出现了就说明有人给它们建了 ORM 模型——那样两套机制会同时管同一张表，
    LangGraph 的 ``setup()`` 和 Alembic 迁移会互相覆盖。
    """

    assert CHECKPOINTER_TABLE_NAMES.isdisjoint(Base.metadata.tables.keys())


def test_alembic_skips_every_checkpointer_table() -> None:
    """四张表在 autogenerate 比较里全部被排除。"""

    for name in CHECKPOINTER_TABLE_NAMES:
        assert include_object(None, name, "table", True, None) is False


def test_alembic_still_compares_business_tables() -> None:
    """业务表必须照常参与比较，排除规则不能扩大。

    写成 ``name.startswith("checkpoint")`` 就会顺手排掉将来某张以 checkpoint 开头的业务表。
    这条断言用一个真实业务表名钉住边界。
    """

    assert include_object(None, "documents", "table", True, None) is True
    assert include_object(None, "sources", "table", True, None) is True


def test_alembic_only_skips_tables_not_columns() -> None:
    """排除只作用于表这一层，不影响列和索引。

    按名字排除却不看 ``type_`` 的话，某张业务表上恰好叫 ``checkpoints`` 的列也会被跳过，
    那一列就永远不进迁移。
    """

    assert include_object(None, "checkpoints", "column", True, None) is True
    assert include_object(None, "checkpoints", "index", True, None) is True


def test_the_sqlalchemy_driver_prefix_does_not_reach_psycopg() -> None:
    """driver 前缀不能进 psycopg，且各字段要落到正确的键上。

    断言解析后的字典而不是字符串字面量：``make_conninfo`` 的键顺序和加引号方式属于它的
    实现细节，锁死字面量会让一次无害的库升级变成测试失败。
    """

    result = conninfo_to_dict(
        to_psycopg_conninfo("postgresql+psycopg://user:pw@db.example.com:5432/news")
    )

    assert result["host"] == "db.example.com"
    assert result["port"] == "5432"
    assert result["user"] == "user"
    assert result["password"] == "pw"
    assert result["dbname"] == "news"


def test_a_plain_psycopg_url_is_also_accepted() -> None:
    """已经是 psycopg 格式的 URL 也要能转，调用方不必先判断格式。"""

    result = conninfo_to_dict(
        to_psycopg_conninfo("postgresql://user:pw@db.example.com:5432/news")
    )

    assert result["host"] == "db.example.com"
    assert result["user"] == "user"
    assert result["dbname"] == "news"


def test_query_parameters_survive_the_conversion() -> None:
    """连接串里的查询参数必须保留。

    ``?sslmode=require`` 这类参数被吃掉的后果是生产环境降级成非加密连接——而且不会报错。
    """

    result = conninfo_to_dict(
        to_psycopg_conninfo("postgresql+psycopg://u:p@h:5432/db?sslmode=require")
    )

    assert result["sslmode"] == "require"


def test_a_literal_percent_in_the_password_survives() -> None:
    """密码里字面的 ``%`` 必须原样传给 psycopg。

    这是一个真实故障的回归测试：早先的实现把 driver 前缀砍掉、剩下的当 URL 交给 psycopg，
    而 psycopg 会对 URL 的 userinfo 做百分号解码，于是密码里一个 ``%`` 就成了非法转义，
    报 ``invalid percent-encoded token`` 且把密码原文写进异常消息。SQLAlchemy 不做这层
    解码，所以当时的表现是业务查询正常、只有会话记忆连不上。
    """

    result = conninfo_to_dict(
        to_psycopg_conninfo("postgresql+psycopg://u:pa%O5s!d*C@h.example.com:5432/db")
    )

    assert result["password"] == "pa%O5s!d*C"


def test_a_missing_port_does_not_become_an_empty_value() -> None:
    """URL 没写端口时不能产出 ``port=``，那会让 libpq 拿到空值而不是用默认端口。"""

    result = conninfo_to_dict(to_psycopg_conninfo("postgresql+psycopg://u:p@h/db"))

    assert "port" not in result
    assert result["host"] == "h"
    assert result["dbname"] == "db"
