# 能力地图

本文只做导航：从「用户能做的一件事」指到入口、主要代码和测试。

- 不解释能力做什么，也不解释怎么实现——前者看 `README.md`，后者看代码和 docstring。
- 不抄 SQL、枚举、阈值和单模块算法。这些只有代码里那一份是真的。
- 一个能力一行。写不下说明该拆，或者它本来就不是一个独立能力。
- 跨多个模块、光看单个文件拼不出全过程的链路，另外写在 [`flows/`](flows/) 里。

后端路由本身不带 `/api`。`/api` 是前端侧前缀，开发环境由 Vite 代理剥掉（`frontend/vite.config.ts`），
生产环境由反向代理承担。下表「后端」列写真实路由，「前端」列写页面路由。

## 对外能力

| 能力 | 前端 | 后端 | 主要代码 | 测试 |
| --- | --- | --- | --- | --- |
| 账号密码登录、退出 | `/login` | `POST /auth/login`、`POST /auth/logout` | `api/auth.py`（FastAPI Users Cookie backend）、`auth/`；前端 `api/auth.ts`、`features/auth/auth-session.ts` | `tests/test_auth.py`、`src/features/auth/auth-session.spec.ts`、`src/pages/LoginPage.spec.ts` |
| 读取当前登录身份 | 无独立页面，路由守卫用 | `GET /auth/me` | `api/auth.py`、`schemas/auth.py`；前端 `features/auth/auth-session.ts`、`app/router.ts` | `tests/test_auth.py`、`src/features/auth/auth-session.spec.ts` |
| 账号自助（看自己信息、改自己密码） | `/account` | `POST /auth/me/password` | `api/account.py` → `services/account_service.py`；前端 `api/account.ts`、`pages/AccountPage.vue`、`features/account/` | `tests/test_account.py` |
| 语义检索（按新闻分组，检索页多轮累积的检索流） | `/` | `POST /document-search` | `api/document_search.py` → `services/vector_search_service.py`；前端 `api/document-search.ts`、`features/semantic-search/composables/useSearchStream.ts`、`components/SearchComposer.vue`、`components/SearchRecordTurn.vue`、`pages/SearchPage.vue` | `tests/test_document_search.py`、`src/api/document-search.spec.ts`、`src/features/semantic-search/tests/useSearchStream.spec.ts`、`SearchComposer.spec.ts`、`SearchRecordTurn.spec.ts`、`src/pages/SearchPage.spec.ts` |
| 读取单篇文档 | `/`（检索流某条记录内展开） | `GET /documents/{document_id}` | `api/documents.py` → `repositories/document_repository.py`；前端 `api/documents.ts`、`features/semantic-search/composables/useDocumentReader.ts` | `tests/test_documents_api.py`、`src/api/documents.spec.ts` |
| 用户管理（增删改、改密、踢会话） | `/admin/users` | `GET /admin/users`、`POST /admin/users`、`PATCH /admin/users/{user_id}`、`POST /admin/users/{user_id}/password`、`DELETE /admin/users/{user_id}/sessions` | `api/user_admin.py` → `services/user_admin_service.py`；前端 `api/user-admin.ts`、`pages/UserAdminPage.vue` | `tests/test_user_admin.py`、`src/api/user-admin.spec.ts`、`src/pages/UserAdminPage.spec.ts` |
| 手工触发同步加索引 | 无页面 | `POST /pipeline/run-once` | `api/pipeline.py` → `services/news_pipeline_execution_service.py` | `tests/test_pipeline_api.py`、`tests/test_news_pipeline_execution.py` |
| 定时任务管理（配置 cron 与参数、启停、立即执行、看执行历史；cron 到点自动同步/索引） | 无页面（后端先行，前端待接） | `GET /scheduled-jobs`、`POST /scheduled-jobs`、`GET/PATCH/DELETE /scheduled-jobs/{job_id}`、`POST /scheduled-jobs/{job_id}/trigger`、`GET /scheduled-jobs/{job_id}/runs`、`POST /scheduled-jobs/validate-cron` | `api/scheduled_jobs.py` → `services/scheduled_job_service.py`、`services/scheduler_runner.py`、`services/scheduled_task_registry.py`、`repositories/scheduled_job_repository.py` | `tests/test_scheduled_jobs_api.py`、`tests/test_scheduler_runner.py`、`tests/test_scheduled_task_registry.py`、`tests/test_scheduler_postgres_integration.py`（真库真上游，默认跳过） |
| Agent 对话（模型自己调检索工具再作答，SSE 流式） | `/agent` | `POST /agent/chat` | `api/agent_chat.py` → `agent/runtime.py`、`agent/streaming.py`、`agent/tools/`；前端 `api/agent-chat.ts`、`features/agent-chat/`、`pages/AgentChatPage.vue` | `tests/test_agent_chat_api.py`、`tests/test_agent_streaming.py`、`tests/test_agent_tools.py`、`tests/test_agent_middleware.py`、`src/api/agent-chat.spec.ts`、`src/features/agent-chat/tests/`、`src/pages/AgentChatPage.spec.ts` |
| 读取 Agent 默认系统提示词 | `/agent`（提示词编辑器内） | `GET /agent/default-prompt` | `api/agent_chat.py` → `agent/prompts.py`；前端 `features/agent-chat/composables/useAgentDefaultPrompt.ts` | `tests/test_agent_chat_api.py`、`src/features/agent-chat/tests/useAgentDefaultPrompt.spec.ts` |
| 会话记录（列出自己的会话、点进去看历史并接着聊、删除） | `/agent`（侧栏）、`/agent/:threadId` | `GET /agent/threads`、`GET /agent/threads/{thread_id}/messages`、`DELETE /agent/threads/{thread_id}` | `api/agent_threads.py` → `services/agent_thread_service.py`、`agent/replay.py`、`models/agent_thread.py`；前端 `api/agent-threads.ts`、`features/agent-chat/composables/useThreadList.ts`、`components/ThreadSidebar.vue` | `tests/test_agent_threads_api.py`、`tests/test_agent_thread_service.py`、`tests/test_agent_replay.py`、`tests/test_agent_thread_ownership_integration.py`（真库，默认跳过）、`src/api/agent-threads.spec.ts`、`src/features/agent-chat/tests/useThreadList.spec.ts` |
| 健康检查 | 无 | `GET /health` | `api/health.py` | `tests/test_error_contract.py` |

`/vector-search`、`/document-search`、`/documents` 要求登录；`/pipeline`、`/admin/users`、
`/scheduled-jobs`、`/agent` 要求超级用户。挂载点和依赖在 `backend/src/agent_lab/main.py` 的
`include_router` 处。

定时任务到点自动执行受 `SCHEDULER_ENABLED` 总开关控制，默认关闭，生产 `.env` 必须显式开；
任务清单存在 PostgreSQL 的 `scheduled_jobs` 表，是调度器的事实来源。进程内调度、单实例约束
和运行策略见
[`adr/0014-in-process-apscheduler-with-db-as-source-of-truth.md`](adr/0014-in-process-apscheduler-with-db-as-source-of-truth.md)。

检索页重构后去掉了「按片段」模式，前端只走 `POST /document-search`（按新闻分组）并在页内做
多轮累积（检索流）；后端 `/vector-search` 接口与后端单测仍保留，只是前端不再调用它，因此
不再占「对外能力」一行。

Agent 那几行的能力边界见 [`adr/0003-agent-v1-is-read-only.md`](adr/0003-agent-v1-is-read-only.md)：
它只有两个只读工具，不写业务表也不写 Qdrant。会话历史落在 checkpointer 自己的四张表里，
不由 Alembic 管（[`adr/0004`](adr/0004-checkpointer-tables-outside-alembic.md)）；**谁拥有哪个会话**
另记在 Alembic 管的 `agent_threads` 表里（[`adr/0009`](adr/0009-agent-thread-ownership-in-own-table.md)）。
每条 `/agent/*` 路由都先确认会话归属，不属于当前账号就 404——和「不存在」返回同一个码，
避免拿状态码差异枚举会话 id。`POST /agent/chat` 是流式的，所以它的归属校验必须在流开始之前
完成，且不使用请求级数据库 Session（[`adr/0010`](adr/0010-sse-routes-use-short-lived-db-sessions.md)）。

用户管理这一行前后端两列写的都是 `/admin/users`，不是抄错：前端页面路由和后端 API 前缀刚好同名，
浏览器实际请求 `/api/admin/users`。后端路由的 `tags=["user-admin"]` 只是 OpenAPI 分组标签，不是路径。

## 命令行能力

| 命令 | 做什么 | 主要代码 | 测试 |
| --- | --- | --- | --- |
| `create-user` | 建账号 | `cli.py` → `services/user_admin_service.py` | `tests/test_cli.py` |
| `sync-news` | 从 FreshRSS 拉新闻进库 | `cli.py` → `services/freshrss_import_service.py`、`ingestion/` | `tests/test_cli.py`、`tests/test_freshrss_incremental_sync.py` |
| `index-pending` | 给待处理文档补向量索引 | `cli.py` → `services/document_indexing_service.py`、`pipeline/`、`qdrant/` | `tests/test_cli.py`、`tests/test_document_indexing_service.py` |
| `run-once` | 同步加索引跑一轮 | `cli.py` → `services/news_pipeline_execution_service.py` | `tests/test_cli.py`、`tests/test_news_pipeline_execution.py` |
| `init-checkpointer` | 建 Agent 会话历史表（部署一次，幂等） | `cli.py` → `agent/checkpointer.py` | `tests/test_agent_checkpointer.py` |
| `prune-orphan-threads` | 清掉没有归属记录的会话历史（**默认只预演**，加 `--yes` 才删，不可恢复） | `cli.py` → `agent/checkpointer.py`、`services/agent_thread_service.py` | `tests/test_cli.py` |

入口在 `backend/src/agent_lab/cli.py` 的 `build_parser`，参数以 `--help` 为准。

## 支撑模块

不直接对应用户能力，但被上面多处共用。

| 模块 | 位置 | 说明 |
| --- | --- | --- |
| 配置 | `backend/src/agent_lab/config/` | 各外部依赖一个 settings 文件 |
| 运行时装配 | `backend/src/agent_lab/runtime.py`、`qdrant/runtime.py`、`pipeline/write_runtime.py`、`agent/runtime.py` | 进程级资源的构造与复用 |
| 错误契约 | `backend/src/agent_lab/api/error_contract.py` | 异常到 `code`/`status`/`retryable` 的映射规则，检索与 Agent 各一张表 |
| 错误文案收敛 | `frontend/src/api/error-copy.ts` | 查表机制；文案表在各领域的 `model/*-error.ts` 里 |
| OpenAPI 类型 | `frontend/src/api/generated/openapi.ts` | 由后端 `/openapi.json` 生成，命令见 `frontend/README.md` |
