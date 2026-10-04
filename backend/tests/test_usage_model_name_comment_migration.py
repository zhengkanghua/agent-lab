"""用量表 ``model_name`` 列说明同步那条迁移的离线结构校验。

它证明两件事，都是「不跑真库也能确定对错」的那部分：

1. 迁移写入的说明与 ORM 上的注解**逐字一致**——两边分叉就是这条迁移要修的病，所以断言
   必须连着模型读，不能把字符串在测试里再抄一遍（抄一遍等于只证明「文件里写了什么」，证明
   不了「和代码一致」）。
2. 回滚只碰这一列，且把说明改回旧措辞。

**真库上那一步靠 ``alembic -c alembic_usage.ini check``**：迁移随部署的
``alembic -c alembic_usage.ini upgrade head`` 应用之后，它会逐列比对库与模型（包括说明），
干净就说明这一处真的对齐了——那才是这条迁移的验收。本文件不连任何数据库。
"""

from pathlib import Path
from typing import Any

from agent_lab.usage.models import UsageRecordRow
from tests.migration_helpers import load_migration


# 用量库有自己的版本目录（``migration_helpers`` 里那个常量指向业务库），所以这里自己拼路径。
MIGRATION_FILE = (
    Path(__file__).resolve().parents[1]
    / "usage_migrations"
    / "versions"
    / "b3e7d1a94c58_同步用量表模型名列说明_sync_usage_model_name_comment.py"
)


def test_comment_sync_touches_only_model_name_and_matches_the_orm(monkeypatch) -> None:
    """升级写入的说明与 ORM 注解逐字一致，回滚只碰 ``model_name`` 这一列。"""

    module = load_migration(MIGRATION_FILE)
    calls: list[tuple[str, str, Any]] = []
    monkeypatch.setattr(
        module.op,
        "alter_column",
        lambda table, column, **kwargs: calls.append((table, column, kwargs.get("comment"))),
    )

    module.upgrade()

    assert calls == [("usage_records", "model_name", UsageRecordRow.__table__.c.model_name.comment)]

    calls.clear()
    module.downgrade()

    assert [(table, column) for table, column, _ in calls] == [("usage_records", "model_name")]
    assert calls[0][2] == module.PREVIOUS_MODEL_NAME_COMMENT
    # 旧措辞与新说明必须不同：相同的话这条迁移就是空操作，下面那条断言也就失去了意义。
    assert calls[0][2] != UsageRecordRow.__table__.c.model_name.comment
