# 01: 删掉备用模型这条路

**要交付什么：** 主模型彻底失败时不再自动换一个上游——用户直接看到失败，他可以自己换一个模型再问。代码、配置项与部署文件里都不再有「备用模型」这个概念。

本组与另外两组（会话历史落业务表、上下文策略）一起部署，升级前要清空既有会话——清空动作登记在会话历史那一组里。

**被谁阻塞：** 无（可立刻开工）

**状态：** 已完成

- [x] 主模型连续失败之后，系统不再调用第二个模型（断言底层只被调用了主模型那一个）
- [x] 配置项与部署样例里搜不到备用模型
- [x] 上游失败仍以「错误」收尾——那条既有的分类断言还在，而且不是靠删测试换绿的

## 验收记录

生产侧改动：`agent/middleware.py`（删 `ModelFallbackMiddleware` import、`build_agent_middleware` 形参、列表那一项，后续注释编号顺移）、`agent/runtime.py`（只构造一个客户端）、`config/llm.py`（删 `fallback_model` 字段，validator 只留 `model`）、`agent/chat_model.py` 与 `agent/usage_recording.py`、`usage/contracts.py`、`usage/models.py` 的相应注释、`backend/.env.example` 删 `LLM_FALLBACK_MODEL`。

| 勾选项 | 观察 |
|---|---|
| 不再调第二个模型 | 结构上已无第二个客户端可构造（`middleware.py` 形参表与列表、`runtime.py` 装配处都没有了）；行为断言在 `tests/test_agent_middleware.py::test_model_failure_is_retried_then_ends_with_a_classified_error`：`assert primary.call_count == 1 + MODEL_RETRY_MAX`，全部落在唯一那个客户端上 |
| 配置项与部署样例搜不到 | `grep -rn "fallback_model\|LLM_FALLBACK\|备用模型\|ModelFallback" backend/src backend/tests backend/.env.example backend/docker-compose.yml backend/docker-stack.yml frontend/src backend/README.md` 在源码/配置/样例上**零命中**（只剩 `__pycache__/*.pyc` 编译产物） |
| 上游失败仍以错误收尾 | 新用例断言 `isinstance(events[-1], AgentErrorEvent)` 且 `code == "llm_unavailable"`、`retryable is True`；既有的分类断言未动、仍绿：`tests/test_agent_streaming.py:230`、`tests/test_agent_usage_recording.py:548`（“模型彻底失败时运行以 error 事件收尾”） |

**证伪（我自己做的）**：把 `ModelRetryMiddleware` 的 `on_failure="error"` 改回默认 `"continue"`，新用例立刻红，且报错原文正是 ADR 0046 警告的形态——
`assert isinstance(AgentDoneEvent(event='done', status='completed', ...), AgentErrorEvent)`；还原后 1 passed。

**已跑**：`uv run pytest -q`（完整离线回归）→ **886 passed, 76 skipped**；`uv run pytest -q tests/test_usage_model_name_comment_migration.py tests/test_column_comments_migration.py` → 4 passed；`uv run alembic -c alembic_usage.ini heads` → 仍是单一 head `b3e7d1a94c58`。

**未跑 / 残余**：frontend 未动；用量库迁移与真实库的对照靠 `alembic -c alembic_usage.ini check`（本次未执行，需真库授权）；被环境变量门控的集成测试（`test_usage_postgres_integration` 等）未跑。

## 自行判定的范围延伸（需老板复核）

`usage/models.py` 的 `model_name` 列说明原文是「这次调用实际使用的模型名；主模型与备用模型靠它分辨。NULL 表示取不到。」——它会进 DDL、且逐字写在已应用的建表迁移 `39e5bca95230` 里。按工单「代码、配置项与部署文件里都不再有『备用模型』这个概念」这句，这条注释也在范围内，所以：

- 改为「这次调用实际使用的模型名；NULL 表示取不到。」（去掉已失效的因果）；
- **不改**已应用的 `39e5bca95230`，新增 `usage_migrations/versions/b3e7d1a94c58_同步用量表模型名列说明_sync_usage_model_name_comment.py`（`op.alter_column` 只改这一列的 comment，downgrade 写回旧文），随部署的 `alembic -c alembic_usage.ini upgrade head` 应用；
- 新增离线结构测试 `tests/test_usage_model_name_comment_migration.py`（一个函数：迁移写入的说明与 ORM 注解逐字一致、downgrade 只碰这一列）。

**仍然残留的一处提及（故意不动）**：`docs/adr/0037-cross-process-run-coordination-in-database.md:29` 拿 `ModelFallbackMiddleware` 举过例（论证僵尸阈值不应挂在模型超时上）。ADR 是历史记录、按仓库约定 4 不在实施期修改，且它的**结论**不依赖那个例子。已上报老板决定是否另行处理。
