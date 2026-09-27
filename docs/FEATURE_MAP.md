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
| 账号自助（看自己信息、改自己密码） | `/settings/account`（`/account` 重定向并入） | `POST /auth/me/password` | `api/account.py` → `services/account_service.py`；前端 `api/account.ts`、`pages/SettingsPage.vue`、`features/settings/` | `tests/test_account.py`、`src/pages/SettingsPage.spec.ts` |
| 多知识库语义检索（按 Document 分组，检索流） | `/` | `POST /document-search` | [检索链路](flows/one-search.md)；`api/document_search.py` → `services/vector_search_service.py`、`knowledge/scope.py`；前端 `api/document-search.ts`、`features/semantic-search/`、`shared/ui/KnowledgeBaseScopePicker.vue` | `tests/test_document_search.py`、`tests/test_knowledge_search_scope.py`、`src/api/document-search.spec.ts`、`src/features/semantic-search/tests/useSearchStream.spec.ts`、`src/pages/SearchPage.spec.ts` |
| 读取单篇文档与当前原文对照 | `/`、`/agent`、`/admin/files` 的阅读器 | `GET /documents/{document_id}` | `api/documents.py` → `repositories/document_repository.py`；前端 `api/documents.ts`、`features/semantic-search/composables/useDocumentReader.ts`、`shared/ui/SafeMarkdown.vue` | `tests/test_documents_api.py`、`src/api/documents.spec.ts`、`src/features/semantic-search/tests/DocumentReader.spec.ts`、`src/pages/AgentChatPage.spec.ts` |
| 文件上传、按 ID 替换和完整删除 | `/admin/files` | `/file-documents`、`/{document_id}/file`、`/{document_id}` | [文档生命周期](flows/file-document-lifecycle.md)；`api/file_documents.py` → `knowledge/file_application.py`、`knowledge/adapters/files.py`；前端 `features/file-documents/`、`api/file-documents.ts` | `tests/test_file_documents.py`、`tests/test_file_documents_integration.py`、`src/features/file-documents/FileDocumentDirectory.spec.ts` |
| 统一审核、编辑正文、结构与 Chunk 预览、采用和拒绝 | `/admin/documents` | `/document-management`、`/{document_id}/draft`、`/{document_id}/use-latest-source`、`/candidates/{processing_id}/*` | [文档生命周期](flows/file-document-lifecycle.md)；`api/document_review.py` → `knowledge/processing/`、`knowledge/adapters/review.py`；前端 `features/document-review/`、`api/document-review.ts` | `tests/test_document_review_api.py`、`tests/test_document_review_integration.py`、`src/pages/DocumentManagementPage.spec.ts`、`src/features/document-review/DocumentPreview.spec.ts` |
| 下载原件、查看已采用历史和审核结论 | `/admin/documents` | `/document-management/candidates/{processing_id}/original`、`/{document_id}/versions`、`/{document_id}/reviews` | `api/document_review.py`、`knowledge/storage.py`；前端 `DocumentOriginal.vue`、`DocumentReviewHistory.vue` | `tests/test_document_storage.py`、`tests/test_document_review_api.py`、`src/pages/DocumentManagementPage.spec.ts` |
| 用户管理（建号、启停、注销、改密、踢会话；可显示已注销账号） | `/admin/users` | `GET /admin/users`、`POST /admin/users`、`PATCH /admin/users/{user_id}`、`DELETE /admin/users/{user_id}`、`POST /admin/users/{user_id}/password`、`DELETE /admin/users/{user_id}/sessions` | `api/user_admin.py` → `services/user_admin_service.py`；前端 `api/user-admin.ts`、`pages/UserAdminPage.vue` | `tests/test_user_admin.py`、`tests/test_agent_thread_ownership_integration.py`（注销后归属与偏好保留，真实依赖默认跳过）、`src/api/user-admin.spec.ts`、`src/features/user-admin/tests/`、`src/pages/UserAdminPage.spec.ts` |
| KnowledgeBase 配置管理（创建、编辑、启停） | `/admin/knowledge-bases` | `GET /knowledge-bases`、`POST /knowledge-bases`、`PATCH /knowledge-bases/{knowledge_base_id}` | `api/knowledge_bases.py` → `knowledge/`；前端 `api/knowledge-bases.ts`、`features/knowledge-bases/`、`pages/KnowledgeBasesPage.vue` | `tests/test_knowledge_bases.py`、`src/api/knowledge-bases.spec.ts`、`src/pages/KnowledgeBasesPage.spec.ts` |
| 来源管理与 KnowledgeBase 绑定（列表、绑定、解绑） | `/admin/sources` | `GET /sources`、`PATCH /sources/{source_id}/knowledge-base` | `api/sources.py` → `services/source_binding_service.py`；前端 `api/sources.ts`、`features/sources/`、`pages/SourcesPage.vue` | `tests/test_source_binding.py`、`tests/test_freshrss_incremental_sync.py`、`src/api/sources.spec.ts`、`src/pages/SourcesPage.spec.ts` |
| 手动提交同步加处理批次 | `/admin/scheduled-jobs`（任务执行视图） | `POST /pipeline/run-once`、`GET /task-runs/{run_id}` | `api/pipeline.py` → `tasks/service.py`、`services/scheduled_tasks.py`；前端 `PipelineSubmissionForm.vue` | `tests/test_pipeline_api.py`、`tests/test_news_pipeline_execution.py`、`src/pages/ScheduledJobsPage.spec.ts` |
| 周期配置与立即执行 | `/admin/scheduled-jobs`（周期配置视图） | `/scheduled-jobs`、`/scheduled-jobs/task-types`、`/scheduled-jobs/validate-cron`、`/scheduled-jobs/{job_id}/trigger` | [执行链路](flows/scheduled-job-execution.md)；`services/scheduled_job_service.py`、`tasks/beat.py`、`tasks/cron.py`、`services/scheduled_task_registry.py` | `test_scheduled_jobs_api.py`、`test_scheduler_runner.py`、`test_scheduler_postgres_integration.py`（真实依赖默认跳过）；`src/api/scheduled-jobs.spec.ts` |
| 全部任务执行、独立详情、取消与人工重试 | `/admin/scheduled-jobs?view=executions` | `/task-runs`、`/task-runs/{run_id}`、`/{run_id}/cancel`、`/{run_id}/retry` | `api/task_runs.py`、`tasks/`、`task_assembly.py`；前端 `api/tasks.ts`、`features/scheduled-jobs/` | `test_task_api.py`、`test_task_execution.py`、`test_task_queue_integration.py`（默认跳过）；`src/pages/ScheduledJobsPage.spec.ts` |
| 默认重试与任务历史策略 | 任务管理的策略对话框 | `/task-policy`、`/task-policy/changes` | `api/task_runs.py`、`tasks/service.py`、`tasks/repository.py`；前端 `TaskPolicyPanel.vue` | `test_task_api.py`、`test_task_execution.py`、`src/api/tasks.spec.ts` |
| 文档批次交接与清理恢复 | 文件／文档管理与任务执行详情 | 文档管理写入口、`GET /task-runs/{run_id}` | [文档生命周期](flows/file-document-lifecycle.md)；`knowledge/task_intake.py`、`knowledge/adapters/pending_work.py`、`services/write_coordination.py`、`services/document_retention_service.py` | `test_news_pipeline_execution.py`、`test_task_execution.py`、`test_task_cross_storage_integration.py`、`test_task_migration_postgres_integration.py`（真实依赖默认跳过） |
| Agent 对话（模型自己调检索工具再作答，SSE 流式；所有登录账号可用） | `/agent` | `POST /agent/chat` | `api/agent_chat.py` → `agent/runtime.py`、`agent/streaming.py`、`agent/tools/`；前端 `api/agent-chat.ts`、`features/agent-chat/`、`pages/AgentChatPage.vue` | `tests/test_agent_chat_api.py`、`tests/test_agent_streaming.py`、`tests/test_agent_tools.py`、`tests/test_agent_middleware.py`、`src/api/agent-chat.spec.ts`、`src/features/agent-chat/tests/`、`src/pages/AgentChatPage.spec.ts` |
| Agent 会话知识库选择与证据引用 | `/agent`、`/agent/:threadId` | `PATCH /agent/threads/{thread_id}/scope`、`POST /agent/chat`、`GET /agent/threads/{thread_id}/messages` | [回答与引用链路](flows/agent-answer-evidence.md)；`agent/context.py`、`agent/evidence.py`、`agent/replay.py`、`agent/middleware.py`；前端 `useAgentChat.ts`、`AgentTurnCard.vue` | `tests/test_agent_evidence_scope.py`、`src/api/agent-threads.spec.ts`、`src/features/agent-chat/tests/useAgentChat.spec.ts`、`src/pages/AgentChatPage.spec.ts` |
| 检索偏好（数量参数的默认值，改动即生效，存在账号上） | `/settings/search`（检索输入条有直达入口） | `GET`/`PUT /auth/me/preferences` | `api/account.py` → `services/user_preference_service.py`；前端 `api/preferences.ts`、`features/settings/`、`pages/SettingsPage.vue` | `tests/test_account.py`、`src/api/preferences.spec.ts`、`src/features/settings/tests/`、`src/pages/SettingsPage.spec.ts` |
| Agent 偏好（自定义系统提示词，作为新会话的初始值） | `/settings/agent`（输入条徽章直达） | `GET`/`PUT /auth/me/preferences` | `api/account.py` → `services/user_preference_service.py`；前端 `api/preferences.ts`、`features/settings/`、`pages/SettingsPage.vue` | `tests/test_account.py`、`src/api/preferences.spec.ts`、`src/features/settings/tests/`、`src/pages/SettingsPage.spec.ts` |
| 读取 Agent 默认系统提示词 | `/settings/agent`（提示词编辑器内） | `GET /agent/default-prompt` | `api/agent_chat.py` → `agent/prompts.py`；前端 `features/settings/composables/useDefaultAgentPrompt.ts` | `tests/test_agent_chat_api.py`、`src/features/settings/tests/useDefaultAgentPrompt.spec.ts` |
| 会话记录（列出自己的会话、点进去看历史并接着聊、删除） | `/agent`（侧栏）、`/agent/:threadId` | `GET /agent/threads`、`GET /agent/threads/{thread_id}/messages`、`DELETE /agent/threads/{thread_id}` | `api/agent_threads.py` → `services/agent_thread_service.py`、`agent/replay.py`、`models/agent_thread.py`；前端 `api/agent-threads.ts`、`features/agent-chat/composables/useThreadList.ts`、`components/ThreadSidebar.vue` | `tests/test_agent_threads_api.py`、`tests/test_agent_thread_service.py`、`tests/test_agent_replay.py`、`tests/test_agent_thread_ownership_integration.py`（真库，默认跳过）、`src/api/agent-threads.spec.ts`、`src/features/agent-chat/tests/useThreadList.spec.ts` |
| 读取自己的模型调用用量（明细与汇总，按时间范围与模型筛选） | `/settings/usage` | `GET /usage/records`、`GET /usage/summary`、`GET /usage/models` | `api/usage.py` → `usage/`（契约、采集器、仓储、用料表与它自己的迁移环境）、`agent/usage_recording.py`；前端 `api/usage.ts`、`features/settings/components/UsageSection.vue`、`composables/useUsage.ts` | `tests/test_usage_api.py`、`tests/test_usage_queue.py`、`tests/test_usage_persistence.py`、`tests/test_usage_postgres_integration.py`（真库，默认跳过）；`src/api/usage.spec.ts`、`src/features/settings/tests/UsageSection.spec.ts`、`src/pages/SettingsPage.spec.ts` |
| 健康检查 | 无 | `GET /health` | `api/health.py` | 无（真连 PostgreSQL，离线测试跑不了） |

`/vector-search`、`/document-search`、`/documents`、`/auth/me/*`、`/agent/*` 要求普通启用账号；
`/pipeline`、`/admin/users`、`/scheduled-jobs`、`/task-runs`、`/task-policy`、
`/file-documents`、`/document-management`、`/sources` 要求超级用户。
`/knowledge-bases` 两开：`GET` 列表对所有启用账号开放（默认只返回启用库，非超管传
`include_inactive` 会被拒），创建与编辑要求超级用户。挂载点在
`backend/src/agent_lab/main.py` 的 `include_router` 处，多数 router 的权限依赖也挂在那里；
文件资料与文档审核两个 router 把超级用户依赖写在 `APIRouter` 自身，`/knowledge-bases` 的写接口
写在路由装饰器上，`/auth/me/*` 写在处理函数签名里。Agent 对话与检索同级开放，
照旧只读；放开后跨账号隔离靠会话归属，它按 `user_id` 判断、与角色无关
（[ADR 0030](adr/0030-agent-open-to-all-accounts.md)）。

公共任务的持久受理、Celery/Redis 进程形态、写资源协调及恢复决策见 [ADR 0019](adr/0019-scheduled-execution-and-write-coordination.md)。

检索页重构后去掉了「按片段」模式，前端只走 `POST /document-search`（按 Document 分组）并在页内做
多轮累积（检索流）；后端 `/vector-search` 接口与后端单测仍保留，只是前端不再调用它，因此
不再占「对外能力」一行。

Agent 那几行的能力边界（只读）、会话数据为什么分两处存、归属校验为什么在流开始前，分别见
[`adr/0003`](adr/0003-agent-v1-is-read-only.md)、[`adr/0004`](adr/0004-checkpointer-tables-outside-alembic.md)、
[`adr/0009`](adr/0009-agent-thread-ownership-in-own-table.md)、[`adr/0010`](adr/0010-sse-routes-use-short-lived-db-sessions.md)；
实现上的说明在 `backend/docs/architecture.md` 的「Agent 对话」与「数据库表」两节，本文不复述。

用户管理这一行前后端两列写的都是 `/admin/users`，不是抄错：前端页面路由和后端 API 前缀刚好同名，
浏览器实际请求 `/api/admin/users`。后端路由的 `tags=["user-admin"]` 只是 OpenAPI 分组标签，不是路径。
后台在前端只有一条路由 `/admin/:section?`（users=账号管理、knowledge-bases=知识库、sources=来源管理、files=文件资料、documents=文档审核、scheduled-jobs=任务管理），
各地址是同一条路由的分区，注册表见 `pages/AdminPage.vue`。

## 命令行能力

| 命令 | 做什么 | 主要代码 | 测试 |
| --- | --- | --- | --- |
| `create-user` | 建账号 | `cli.py` → `services/user_admin_service.py` | `tests/test_cli.py` |
| `sync-news` | 从 FreshRSS 按 Source 绑定导入 Document | `cli.py` → `knowledge/composition.py`、`knowledge/importing.py`、`knowledge/adapters/freshrss.py` | `tests/test_cli.py`、`tests/test_freshrss_incremental_sync.py` |
| `index-pending` | 消费解析、采用及索引回收待办 | `cli.py` → `knowledge/processing/batch.py`、`knowledge/composition.py` | `tests/test_cli.py`、`tests/test_processing_application.py`、`tests/test_candidate_index.py` |
| `rebuild-index` | 从已采用快照重建新 generation，核验后发布 Alias | `cli.py` → `knowledge/rebuilding.py`、`knowledge/adapters/rebuilding.py`、`qdrant/rebuilding.py` | `tests/test_index_rebuild.py`、`tests/test_document_rebuild_integration.py` |
| `recover-index-rebuild` | 核对已准备目标与当前 Alias，恢复发布结果 | `cli.py` → `knowledge/rebuilding.py`、`knowledge/adapters/rebuilding.py` | `tests/test_document_rebuild_integration.py` |
| `run-once` | 同步加索引跑一轮 | `cli.py` → `services/news_pipeline_execution_service.py` | `tests/test_cli.py`、`tests/test_news_pipeline_execution.py` |
| `init-checkpointer` | 建 Agent 会话历史表（部署一次，幂等） | `cli.py` → `agent/checkpointer.py` | `tests/test_agent_checkpointer.py` |
| `prune-orphan-threads` | 清掉没有归属记录的会话历史（**默认只预演**，加 `--yes` 才删，不可恢复） | `cli.py` → `agent/checkpointer.py`、`services/agent_thread_service.py` | `tests/test_cli.py` |
| `prune-old-threads` | 清掉最后活跃时间早于 N 天的会话（**默认只预演**，加 `--yes` 才删，不可恢复） | `cli.py` → `agent/checkpointer.py`、`services/agent_thread_service.py` | `tests/test_cli.py` |

入口在 `backend/src/agent_lab/cli.py` 的 `build_parser`，参数以 `--help` 为准。

## 支撑模块

不直接对应用户能力，但被上面多处共用。

| 模块 | 位置 | 说明 |
| --- | --- | --- |
| 配置 | `backend/src/agent_lab/config/` | 各外部依赖一个 settings 文件 |
| 结构解析与 Chunk | `backend/src/agent_lab/knowledge/processing/`、`knowledge/adapters/docling_*.py` | 项目契约与可替换解析、切分适配器 |
| 原件存储 | `backend/src/agent_lab/knowledge/storage.py` | 对象引用、字节核验与 MinIO/S3 适配器 |
| 已采用版本可见性 | `backend/src/agent_lab/knowledge/visibility.py`、`knowledge/adapters/visibility.py` | 检索过滤、状态核验与有界重查 |
| 运行时装配 | `backend/src/agent_lab/runtime.py`、`qdrant/runtime.py`、`pipeline/write_runtime.py`、`agent/runtime.py` | 进程级资源的构造与复用 |
| 错误契约 | `backend/src/agent_lab/api/error_contract.py` | 异常到 `code`/`status`/`retryable` 的映射规则，按链路各一张有序表，清单见 `backend/docs/architecture.md` 的「集中的错误契约」 |
| 错误文案收敛 | `frontend/src/api/error-copy.ts` | 查表机制；文案表在各领域的 `model/*-error.ts` 里 |
| 个人偏好 | `frontend/src/features/settings/composables/usePreferences.ts` | 应用级单例，读写后端 `user_preferences`；为什么入库见 `docs/adr/0027-user-level-preferences-in-database.md`，读失败与未加载完时的退化行为见该文件的头注释 |
| OpenAPI 类型 | `frontend/src/api/generated/openapi.ts` | 由后端 `/openapi.json` 生成，命令见 `frontend/README.md` |
