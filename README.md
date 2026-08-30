# Agent Lab

仓库当前只有一个业务领域：新闻语义检索。后端的 Qdrant 与 Ollama 组件目前仍与新闻领域
耦合（Payload 契约、Collection 命名、单份配置），尚未抽离为可被多业务领域、多 Collection
复用的公共层；接入第二个业务领域前需要先完成抽离。

该工作区把新闻向量服务与浏览器工作台作为两个独立运行时维护：

```text
agent-lab/
├── backend/   # FastAPI、FreshRSS、Ollama Embedding、Qdrant
├── frontend/  # Vue 3 + TypeScript + Vite 的 Signal Desk
└── docs/      # 平台级路线图与决策记录
```

当前产品提供两条链路，都只读：只读新闻语义检索，以及一个会自己调用检索工具再作答的
Agent 对话。浏览器默认使用相对路径
`POST /api/document-search` 获取按新闻分组的相关片段，也可切换到“按片段”模式，通过
`POST /api/vector-search` 原样查看 Qdrant 返回的 Chunk 命中；该模式不会去重或重排。
两种模式都只在用户打开阅读视图时调用 `GET /api/documents/{document_id}` 读取
PostgreSQL 完整正文。检索链路本身不调用生成式 LLM。

Agent 对话走 `POST /api/agent/chat`，以 SSE 返回模型输出与工具调用轨迹；会话历史由
LangGraph checkpointer 存在 PostgreSQL 的四张 `checkpoint*` 表里。Agent 只有两个只读工具
（检索新闻、读取全文），不写业务表也不写 Qdrant——见
[`docs/adr/0003-agent-v1-is-read-only.md`](docs/adr/0003-agent-v1-is-read-only.md)。这条链路
只对超级用户开放：每次对话都是真金白银的模型调用，自定义系统提示词等于让调用方直接改
模型行为。开发环境由 Vite 去掉 `/api` 前缀后代理到
`http://127.0.0.1:8000` 的对应 FastAPI 路由。浏览器访问搜索前必须使用内部账号登录；
后端使用 PostgreSQL 可撤销 Token 和 HttpOnly Cookie，不开放注册。部署 Secret 或
`backend/.env` 托管唯一保底超级管理员，服务启动时自动创建/同步；该管理员登录后可在
`/admin/users` 创建和管理其他账号。普通账号只能读取，超级用户额外拥有账号管理、
Agent 对话与手动 Pipeline 权限，CLI 只保留为恢复入口。

## 本地启动

先启动后端：

```powershell
cd backend
uv sync
Copy-Item .env.example .env
# 编辑 .env：同时配置 AUTH_ADMIN_EMAIL、AUTH_ADMIN_PASSWORD，
# 并在本地 HTTP 环境设置 AUTH_COOKIE_SECURE=false。
# 要用 Agent 对话页还需配置 LLM_API_KEY（缺失时只有 /agent/* 返回 503，检索照常）。
uv run alembic upgrade head
uv run agent-lab init-checkpointer   # 只建 Agent 会话历史表，与 Alembic 互不干涉
uv run uvicorn agent_lab.main:app --reload --host 127.0.0.1 --port 8000 `
  --loop agent_lab.runtime:selector_loop_factory
```

再启动前端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器访问 <http://127.0.0.1:5173>，使用 `.env` 中的保底管理员登录，再从顶部账号管理
入口添加其他用户。详细前端命令见 `frontend/README.md`，生产发布步骤见
`docs/vps_deployment.md`，当前能力清单见 `docs/FEATURE_MAP.md`。
