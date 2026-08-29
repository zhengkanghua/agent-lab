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
| 语义检索（Chunk 级） | `/` | `POST /vector-search` | `api/vector_search.py` → `services/vector_search_service.py` → `qdrant/search.py`；前端 `api/vector-search.ts`、`features/semantic-search/composables/useChunkSearch.ts` | `tests/test_vector_search.py`、`tests/test_vector_search_api.py`、`src/api/vector-search.spec.ts` |
| 文档检索（文档级） | `/` | `POST /document-search` | `api/document_search.py` → `services/vector_search_service.py`；前端 `api/document-search.ts`、`features/semantic-search/composables/useSearchRequest.ts` | `tests/test_document_search.py`、`src/api/document-search.spec.ts` |
| 读取单篇文档 | `/`（结果内展开） | `GET /documents/{document_id}` | `api/documents.py` → `repositories/document_repository.py`；前端 `api/documents.ts`、`features/semantic-search/composables/useDocumentReader.ts` | `tests/test_documents_api.py`、`src/api/documents.spec.ts` |
| 用户管理（增删改、改密、踢会话） | `/admin/users` | `GET /user-admin`、`POST /user-admin`、`PATCH /user-admin/{user_id}`、`POST /user-admin/{user_id}/password`、`DELETE /user-admin/{user_id}/sessions` | `api/user_admin.py` → `services/user_admin_service.py`；前端 `api/user-admin.ts`、`pages/UserAdminPage.vue` | `tests/test_user_admin.py`、`src/api/user-admin.spec.ts`、`src/pages/UserAdminPage.spec.ts` |
| 手工触发同步加索引 | 无页面 | `POST /pipeline/run-once` | `api/pipeline.py` → `services/news_pipeline_execution_service.py` | `tests/test_pipeline_api.py`、`tests/test_news_pipeline_execution.py` |
| 健康检查 | 无 | `GET /health` | `api/health.py` | `tests/test_error_contract.py` |

`/vector-search`、`/document-search`、`/documents` 要求登录；`/pipeline`、`/user-admin` 要求超级用户。
挂载点和依赖在 `backend/src/agent_lab/main.py` 的 `include_router` 处。

## 命令行能力

| 命令 | 做什么 | 主要代码 | 测试 |
| --- | --- | --- | --- |
| `create-user` | 建账号 | `cli.py` → `services/user_admin_service.py` | `tests/test_cli.py` |
| `sync-news` | 从 FreshRSS 拉新闻进库 | `cli.py` → `services/freshrss_import_service.py`、`ingestion/` | `tests/test_cli.py`、`tests/test_freshrss_incremental_sync.py` |
| `index-pending` | 给待处理文档补向量索引 | `cli.py` → `services/document_indexing_service.py`、`pipeline/`、`qdrant/` | `tests/test_cli.py`、`tests/test_document_indexing_service.py` |
| `run-once` | 同步加索引跑一轮 | `cli.py` → `services/news_pipeline_execution_service.py` | `tests/test_cli.py`、`tests/test_news_pipeline_execution.py` |

入口在 `backend/src/agent_lab/cli.py` 的 `build_parser`，参数以 `--help` 为准。

## 支撑模块

不直接对应用户能力，但被上面多处共用。

| 模块 | 位置 | 说明 |
| --- | --- | --- |
| 配置 | `backend/src/agent_lab/config/` | 各外部依赖一个 settings 文件 |
| 运行时装配 | `backend/src/agent_lab/runtime.py`、`qdrant/runtime.py`、`pipeline/write_runtime.py` | 进程级资源的构造与复用 |
| 错误文案收敛 | `frontend/src/api/error-copy.ts` | 后端错误码到中文提示的唯一映射点 |
| OpenAPI 类型 | `frontend/src/api/generated/openapi.ts` | 由后端 `/openapi.json` 生成，命令见 `frontend/README.md` |
