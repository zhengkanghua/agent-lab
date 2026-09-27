# 01: 注销取代删除

**要交付什么：** 超级用户在后台删掉一个账号之后，它像以前一样从列表里消失、也登不进来，但它留下的东西不再被抹掉——会话归属、个人偏好、换版决策里的操作者都还在，只清掉登录凭据；以后能查到它是谁。

**被谁阻塞：** 无（可立刻开工）

**状态：** 已完成

- [x] 注销一个账号后：那一行仍在、带上注销时间、处于不可用状态；登录凭据已清空、会话归属与个人偏好仍在、换版决策记录的操作者未变（一次断言这六项）
- [x] 数据库层拒绝「环境托管账号处于已注销状态」与「已注销却仍可用」这两种数据
- [x] 账号列表默认不再返回已注销账号
- [x] 迁移可进可退：升级后存量账号的可用状态不变、没有账号被标成已注销；回退后新列删掉、原约束文案恢复，且全程不动数据
- [x] 换版决策的操作者字段在注销后**保留**（与改动前「置空」相反），那条只为「删账号时清偏好」写的方法与它的用例已删除

**验收说明：** 第一条要在真 PostgreSQL 上跑——它要查四张不同的表，而现在能观测到这些表的用例默认跳过，能离线跑的那个带假会话的用例只有四个方法、一张表也查不到。另外那份真库迁移用例里钉住迁移目标的版本号常量要跟着新迁移更新，否则那条用例不是断言失败而是直接因「列不存在」崩掉。接口描述、会话归属那一节的后端架构说明、以及那批写着硬删除语义的代码注释，都随本工单一起改。

**施工记录（2026-02）：**

- 新增迁移 `c1f4a7d92e60`（加 `users.deleted_at`、改写 `ck_users_environment_admin_privileges`、新增 `ck_users_deleted_at_implies_inactive`），模型同步改；回退一步在开发库上实测过。
- 反证：把 `backend/src` 临时回退到开工 commit，注销六项那条真库用例在第一项 `assert None is not None` 上失败，回到实现后通过。
- 迁移测试的 `HEAD` 常量更新为 `c1f4a7d92e60`，并新增 `BEFORE_SOFT_DELETE`；存量账号的种子数据改用原生 SQL——旧库上没有 `deleted_at`，用模型的 ORM 插入会报「列不存在」而不是断言失败。
- 顺带把「删账号时由业务层清理归属记录/撤销 Token/清理偏好」这几条已经变成假话的 DDL 注释改成注销语义（模型与建表/拆约束那两条迁移的文案），并在新迁移里刷新已在库里的 COMMENT，使新旧库收敛；这一步超出 spec 点名的范围，经老板确认后执行。

**已运行验证：** `uv run pytest -q`（754 passed, 66 skipped）；`RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST=1 RUN_POSTGRES_AUTH_INTEGRATION_TEST=1 uv run pytest -q --scheduler-configured-services tests/test_foreign_key_drop_migration_postgres_integration.py tests/test_agent_thread_ownership_integration.py tests/test_auth_environment_integration.py`（9 passed）。
