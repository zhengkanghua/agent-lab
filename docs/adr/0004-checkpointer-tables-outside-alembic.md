# checkpointer 的表不由 Alembic 管理

LangGraph 会话记忆的四张表（`checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_migrations`）
由 `langgraph-checkpoint-postgres` 自己建自己迁移，Alembic 不生成也不删除它们。为此 `alembic/env.py` 必须有
`include_object` 过滤把它们排除在自动比对之外，部署时用 `agent-lab init-checkpointer` 在
`alembic upgrade head` 之后单独建表。

不加那个过滤会出事，而且是安静地出事：`alembic/env.py` 用 `target_metadata = Base.metadata`，这四张表不在 ORM
元数据里，于是 `alembic revision --autogenerate` 会认为它们是「库里多出来的表」并生成 `op.drop_table('checkpoints')`。
生成的迁移文件读起来没有明显不对，跑下去就把用户的全部对话历史删了。同时 `alembic check` 会永久报漂移，
让这条本该有用的检查失去意义。

## Considered Options

**把四张表反向声明成 SQLAlchemy 模型，纳入 Alembic 统一管理。** 表结构就归一处了。但结构的定义权在
`langgraph-checkpoint-postgres` 手里，它升级时会跑自己的迁移改这些表；我们的模型声明只是一份复制品，
它一变我们就漂移，而且漂移的表现是 `alembic check` 报错、autogenerate 生成一堆 `alter_column`——
我们既不敢应用也没法忽略。跟着上游版本手工同步一份 schema 副本，是长期的、无收益的维护负担。

**跑 `alembic upgrade head` 时顺便建 checkpointer 表（写进某个迁移的 `upgrade()`）。** 部署只剩一条命令，
确实更顺手。但迁移里就得调 `AsyncPostgresSaver.setup()`，等于让 Alembic 迁移依赖一个第三方库的建表逻辑，
而那段逻辑自己带版本管理；迁移文件按约定应当是「写下来就不再变」的历史记录，里面塞一个会随依赖升级改变
行为的调用，就破了这个约定——回滚到某个旧迁移时它的行为已经不是当初那个了。

**做成 HTTP 接口，在网页上点一下建表。** 老板说过后续重点放 API。但这是部署步骤不是用户能力，做成接口等于
把一个能建表的写接口暴露在公网上，比一个只在部署时手工跑的命令危险得多。

## Consequences

部署多一步，且顺序不能反：先 `alembic upgrade head`，再 `agent-lab init-checkpointer`。顺序写进
`backend/README.md` 的部署一节和 `docs/container_deployment.md`。

`include_object` 是一层保护性配置，删掉不会立刻报错，只会让下一次 autogenerate 生成删表迁移。它在
`alembic/env.py` 里必须带注释说明后果，不能只留一个函数名。

四张表的清理策略同样落在 Alembic 之外：清理不是迁移，而是**由操作者手动执行的运维命令**，当前有两条
——`agent-lab prune-orphan-threads`（清没有归属记录的会话历史）与 `agent-lab prune-old-threads
--before-days N`（清最后活跃时间早于 N 天的会话）。两条都**默认只预演**，加 `--yes` 才真删，且删除
不可恢复。

这两条与用户能点到的「删除这个会话」（`DELETE /agent/threads/{thread_id}`）不是一回事：那条按会话归属校验、
一次删一个、只删自己的，属于产品能力；这两条是**批量与孤儿清理的运维命令，没有对应的 HTTP 接口，也不对
普通账号开放**，只有能登服务器跑 CLI 的人用得上。当前**也没有自动清理**——任务类型注册表里没有会调用它们
的定时任务，会话历史在无人干预时保持无界增长。真要接自动清理，是新增一条定时任务，仍然不是迁移。
