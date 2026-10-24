"""删除会话级与账号级两处系统提示词列。

**只删这两列，不碰别的列、不碰索引。** 系统提示词此后只有内置的那一份（见 ADR 0050），运行期
不再有任何会变化的提示词来源，两处存量列因此都没有读者了：``agent_threads.system_prompt``
原是建会话时从账号偏好拍下的快照，``user_preferences.system_prompt`` 原是用户在设置页写的自定义
提示词。

**代价是不可逆地丢值**：`downgrade` 只把两列结构加回来，原来存过的提示词正文恢复不了。

**两列合成一条迁移**：它们是同一个决策的两半（去掉提示词的用户来源与会话快照），分两条的话
中间那个状态里「会话快照还在、账号偏好已经没了」没有任何意义。

**列说明只在这里写一次**：ORM 上这两列已经删掉，所以回滚要写回的说明只能来自建表那次迁移
（``e2c8f14b7a30``）的原文，逐字照抄，由
``tests/test_drop_system_prompt_columns_migration.py`` 钉住。

Revision ID: a3c8e5d719bf
Revises: c5d8e2a47f91
"""

# 这行标记让部署脚本把这条迁移推迟到旧容器退出之后再执行。
POST_DEPLOY = True

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "a3c8e5d719bf"
down_revision: str | Sequence[str] | None = "c5d8e2a47f91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 回滚目标：建表那次迁移写下的原文，与当时 ORM 上的注解逐字一致。
PREFERENCE_PROMPT_COMMENT = "自定义系统提示词；为空表示使用服务端内置默认提示词。"
THREAD_PROMPT_COMMENT = (
    "会话建立时从该账号个人偏好拍下的系统提示词快照；为空表示用服务端内置默认提示词。"
)

# 升级按这个顺序删、回滚按相反顺序加，两边都由测试钉住。
TARGETS = (
    ("agent_threads", "system_prompt"),
    ("user_preferences", "system_prompt"),
)


def upgrade() -> None:
    """删掉两处提示词列。"""

    for table, name in TARGETS:
        op.drop_column(table, name)


def downgrade() -> None:
    """把两列结构加回来，值不恢复。

    回滚后的库对旧代码是完整可用的：旧代码新建会话时会重新拍快照、设置页会重新写入偏好，
    存量行只是空着 —— 空值的含义正是「用内置默认提示词」，与回滚后的行为一致。
    """

    op.add_column(
        "user_preferences",
        sa.Column(
            "system_prompt",
            sa.Text(),
            nullable=True,
            comment=PREFERENCE_PROMPT_COMMENT,
        ),
    )
    op.add_column(
        "agent_threads",
        sa.Column(
            "system_prompt",
            sa.Text(),
            nullable=True,
            comment=THREAD_PROMPT_COMMENT,
        ),
    )
