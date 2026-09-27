"""账号注销：新增 deleted_at 列与两条配套约束。

**只做结构变更，不删任何数据。** 存量账号的 ``deleted_at`` 全为 ``NULL``，含义是「它们都没被
注销」——那正是它们当时的真实情况，不需要回填。旧版本里注销就是真删行，所以库里本来也不会有
「已注销」的账号，这里也就无从迁移。

**两条约束一改一增。** 环境托管那条 ``ck_users_environment_admin_privileges`` 加上
``deleted_at IS NULL``，让「环境托管账号被注销」在数据层写不进去；新增的
``ck_users_deleted_at_implies_inactive`` 只拦「已注销却仍可登录」这**一种**字段组合，它不拦
任何正常写入（启动同步建号与拉回活跃、接口建号、CLI 建号、停用、停用后改回活跃、注销全部
通过），防的是 ``is_active`` 的某个写者漏掉与 ``deleted_at`` 配对时那条静默的越权登录——
表现是「列表显示已注销、这个人照常登录」，而没有任何报错。

**为什么用时间戳而不是布尔。** ``deleted_at`` 记下注销发生的时刻，前台能显示注销时间，将来
若做保留期清理也有起点。决策与代价见 ``docs/adr/0034-account-deletion-is-soft-delete.md``。

**顺带刷新三条逻辑外键的 DDL 注释。** 它们写于注销还是硬删除的年代：「删账号时由业务层清理
归属记录」「删账号时由业务层撤销 Token」「删账号时由业务层清理偏好」。注销落地之后前两句、还有
偏好表那两句都成了假话（会话归属与偏好现在**保留**）。这里把它们改成注销语义——这样已迁移的库
与新库看到的是同一份文案，不留下一个「看库的人和看代码的人读到不同事实」的缝。

Revision ID: c1f4a7d92e60
Revises: e2c8f14b7a30
"""

from alembic import op
import sqlalchemy as sa


revision = "c1f4a7d92e60"
down_revision = "e2c8f14b7a30"
branch_labels = None
depends_on = None

DELETED_AT_COMMENT = "账号注销时间；为空表示账号未被注销。注销时与 is_active=false 成对写入。"

# 环境托管那条约束改写前后的文本。回滚要还原成逐字一致的原句，所以两份都写在这里，
# 不在 upgrade / downgrade 里各拼一半。
ENVIRONMENT_ADMIN_CHECK_OLD = (
    "NOT is_environment_admin OR (is_active AND is_superuser AND is_verified)"
)
ENVIRONMENT_ADMIN_CHECK_NEW = (
    "NOT is_environment_admin OR "
    "(is_active AND is_superuser AND is_verified AND deleted_at IS NULL)"
)
PAIRED_CHECK = "NOT (deleted_at IS NOT NULL AND is_active)"

# 与建表/拆约束那几条迁移里更新过的 DDL 注释逐字一致：被刷新的库与新建的库收敛到同一文案。
COMMENT_REFRESHES = (
    (
        "agent_threads",
        "user_id",
        "该会话所属的 users.id；业务层维护的逻辑外键，库上无约束。注销账号时归属记录保留。",
    ),
    (
        "access_tokens",
        "user_id",
        "该登录 Token 所属的 users.id；业务层维护的逻辑外键，库上无约束。注销账号时由业务层撤销。",
    ),
    (
        "user_preferences",
        "user_id",
        "该偏好所属的 users.id；业务层维护的逻辑外键，库上无约束。注销账号时这一行保留。",
    ),
)
PREFERENCE_TABLE_COMMENT = "账号级个人偏好；一行一个账号，注销账号时这一行保留。"


def upgrade() -> None:
    # 1、加注销时间列。可空：为 NULL 表示未注销，这是存量账号的真实语义。
    op.add_column(
        "users",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=DELETED_AT_COMMENT,
        ),
    )
    # 2、改写环境托管那条约束：补上「未注销」条件。
    op.drop_constraint("ck_users_environment_admin_privileges", "users", type_="check")
    op.create_check_constraint(
        "ck_users_environment_admin_privileges",
        "users",
        ENVIRONMENT_ADMIN_CHECK_NEW,
    )
    # 3、新增配对约束，拦住「已注销却仍可登录」。
    op.create_check_constraint(
        "ck_users_deleted_at_implies_inactive",
        "users",
        PAIRED_CHECK,
    )
    # 4、把几条写着「删账号时清理」的 DDL 注释刷新成注销语义。已迁移的库停在旧文案上，
    #    新库直接拿建表/拆约束那几条迁移里更新过的文案；这一条把两者对齐。
    for table, column, comment in COMMENT_REFRESHES:
        op.alter_column(table, column, comment=comment)
    op.execute(f"COMMENT ON TABLE user_preferences IS '{PREFERENCE_TABLE_COMMENT}'")


def downgrade() -> None:
    """删列、还原环境托管约束、删掉配对约束，不动数据。

    与本仓库既有惯例一致：结构回滚不替业务决定数据去留。回滚之后已注销的账号会退化成
    「不可登录的停用账号」，而它们的会话归属、个人偏好、换版决策留痕本来就都还在，
    不需要也**不允许**在这里清掉。

    顺序不能换：两条约束都引用 ``deleted_at``，必须先拆约束再删列。

    偏好表与那两条逻辑外键的 DDL 注释不回退：它们是说明文字，而那份说明（注销保留归属、偏好与
    决策留痕）在回退前后都成立，重改回去只会让下一个升级周期再把它们刷一遍。
    """

    op.drop_constraint("ck_users_deleted_at_implies_inactive", "users", type_="check")
    op.drop_constraint("ck_users_environment_admin_privileges", "users", type_="check")
    op.create_check_constraint(
        "ck_users_environment_admin_privileges",
        "users",
        ENVIRONMENT_ADMIN_CHECK_OLD,
    )
    op.drop_column("users", "deleted_at")
