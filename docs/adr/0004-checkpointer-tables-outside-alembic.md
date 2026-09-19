---
status: accepted
---

# checkpointer 的表不由 Alembic 管理

LangGraph 会话记忆的四张表由 `langgraph-checkpoint-postgres` 自己建、自己迁移，Alembic 既不生成也不删除它们：`alembic/env.py` 用 `include_object` 把它们排除在自动比对之外，部署时在 `alembic upgrade head` 之后单独跑 `agent-lab init-checkpointer` 建表，顺序不能反。

不排除会安静地出事：这四张表不在 ORM 元数据里，`alembic revision --autogenerate` 会把它们当成「库里多出来的表」生成 `drop_table`，迁移文件读起来没有明显不对，跑下去就把用户的全部对话历史删了；同时 `alembic check` 会永久报漂移，让这条本该有用的检查失去意义。

## Considered Options

**把四张表反向声明成 SQLAlchemy 模型，纳入 Alembic。** 表结构的定义权在上游库手里，它升级时会跑自己的迁移改这些表；我们的声明只是复制品，一变就漂移，跟着上游手工同步 schema 是长期无收益的负担。

**在某个迁移的 `upgrade()` 里顺便建表。** 迁移就得调上游的建表逻辑，而那段逻辑自己带版本管理；迁移文件按约定写下就不再变，塞一个会随依赖升级改变行为的调用就破了这个约定。

**做成 HTTP 接口，在网页上点一下建表。** 这是部署步骤不是用户能力，做成接口等于把一个能建表的写接口暴露在公网上。
