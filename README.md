# News RAG Platform

该工作区把新闻向量服务与浏览器工作台作为两个独立运行时维护：

```text
news-rag-platform/
├── backend/   # FastAPI、FreshRSS、Ollama Embedding、Qdrant
├── frontend/  # Vue 3 + TypeScript + Vite 的 Signal Desk
└── docs/      # 平台级路线图与决策记录
```

当前产品提供只读新闻语义检索，不调用生成式 LLM。浏览器默认使用相对路径
`POST /api/document-search` 获取按新闻分组的相关片段，也可切换到“按片段”模式，通过
`POST /api/vector-search` 原样查看 Qdrant 返回的 Chunk 命中；该模式不会去重或重排。
两种模式都只在用户打开阅读视图时调用 `GET /api/documents/{document_id}` 读取
PostgreSQL 完整正文。开发环境由 Vite 去掉 `/api` 前缀后代理到
`http://127.0.0.1:8000` 的对应 FastAPI 路由。浏览器访问搜索前必须使用内部账号登录；
后端使用 PostgreSQL 可撤销 Token 和 HttpOnly Cookie，不开放注册。部署 Secret 或
`backend/.env` 托管唯一保底超级管理员，服务启动时自动创建/同步；该管理员登录后可在
`/admin/users` 创建和管理其他账号。普通账号只能读取，超级用户额外拥有账号管理与
手动 Pipeline 权限，CLI 只保留为恢复入口。

## 本地启动

先启动后端：

```powershell
cd backend
uv sync
Copy-Item .env.example .env
# 编辑 .env：同时配置 AUTH_ADMIN_EMAIL、AUTH_ADMIN_PASSWORD，
# 并在本地 HTTP 环境设置 AUTH_COOKIE_SECURE=false。
uv run alembic upgrade head
uv run uvicorn news_vector_service.main:app --reload --host 127.0.0.1 --port 8000 `
  --loop news_vector_service.runtime:selector_loop_factory
```

再启动前端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器访问 <http://127.0.0.1:5173>，使用 `.env` 中的保底管理员登录，再从顶部账号管理
入口添加其他用户。详细前端命令见 `frontend/README.md`，生产发布步骤见
`docs/vps_deployment.md`，平台范围见 `docs/frontend_and_rag_roadmap.md`。
