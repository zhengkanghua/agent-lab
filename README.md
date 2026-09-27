# Agent Lab

仓库当前的业务领域是知识库语义检索：新闻与上传资料归属不同 KnowledgeBase，共用
Document、Chunk 和同规格向量索引。MinIO/S3 保存原件，PostgreSQL 保存候选、已采用版本和审核记录，
Qdrant 提供语义检索；MD 与 FreshRSS HTML 使用 Docling 解析并按结构生成 Chunk，TXT 保持纯文本语义。

该工作区把知识库服务与浏览器工作台作为两个独立运行时维护：

```text
agent-lab/
├── backend/   # FastAPI、FreshRSS、Ollama Embedding、Qdrant
├── frontend/  # Vue 3 + TypeScript + Vite 的 Signal Desk
└── docs/      # 能力地图、跨模块链路、决策记录与部署手册
```

检索页和 Agent 对话都支持选择所有启用知识库或指定几个知识库。浏览器使用相对路径
`POST /api/document-search` 获取按 Document 分组的相关片段；检索页没有「按片段」模式切换，
取舍见 `docs/adr/0013-search-page-multi-round-record-stream.md`。
阅读视图打开时调用 `GET /api/documents/{document_id}` 读取
PostgreSQL 完整正文。检索链路本身不调用生成式 LLM。

Agent 对话走 `POST /api/agent/chat`，以 SSE 返回模型输出与工具调用轨迹；会话历史由
LangGraph checkpointer 存在 PostgreSQL 的四张 `checkpoint*` 表里。Agent 只有两个只读工具
（检索文档、读取全文），不修改 Document 或 Qdrant——见
[`docs/adr/0003-agent-v1-is-read-only.md`](docs/adr/0003-agent-v1-is-read-only.md)。这条链路
对所有登录账号开放（见 [`docs/adr/0030-agent-open-to-all-accounts.md`](docs/adr/0030-agent-open-to-all-accounts.md)）：
跨账号读对话由会话归属按账号挡住，与角色无关；代价是模型额度对全部登录账号共享。开发环境由
Vite 去掉 `/api` 前缀后代理到
`http://127.0.0.1:8000` 的对应 FastAPI 路由。浏览器访问搜索前必须使用内部账号登录；
后端使用 PostgreSQL 可撤销 Token 和 HttpOnly Cookie，不开放注册。部署 Secret 或
`backend/.env` 托管唯一的恢复用超级用户，服务启动时自动创建/同步，不能从网页改密、停用或注销；该账号登录后可在
`/admin/users` 创建和管理其他账号。检索、Agent 对话与个人偏好对所有登录账号开放，
超级用户额外拥有账号管理、知识库/来源/文件管理与手动 Pipeline 权限。日常操作都在网页上；CLI 是并行的维护入口（同步、索引、重建、会话清理），完整清单见 `docs/FEATURE_MAP.md` 的「命令行能力」。

超级用户在 `/admin/files` 上传 `.txt`、`.md`，指定归属知识库，或按 Document ID 替换、删除。
原件和待办保存成功即返回，Celery Worker 通过已受理的文档处理批次解析并处理采用；正常结果自动索引，异常留待人工处理。
`/admin/documents` 统一管理文件和 FreshRSS 资料，可对照原件、编辑正文、检查标题目录与 Chunk、
采用、拒绝和查看历史。同名上传是独立文档，替换携带正式及管理修订检查并发；新索引准备成功后
才切换，失败保留旧已采用版本。首次采用前，普通全文、检索和 Agent 均不可读取候选。

Agent 为每次提问保存实际范围和可核对的引用。点击引用可对照当时取得的片段与当前原文；
原文更新、删除或知识库停用时明确提示。会话知识库选择另存于 `agent_threads`，较早问答压缩后
不再逐条回看，也不作为新回答的证据。链路见 [文件资料](docs/flows/file-document-lifecycle.md)
和 [Agent 回答与引用](docs/flows/agent-answer-evidence.md)。

`/admin/scheduled-jobs` 是任务管理入口，分周期配置和全部任务执行两个视图。手动 Pipeline、周期触发和文档后台批次共用持久受理与查询；提交后返回执行编号，页面关闭不影响后台工作。PostgreSQL 保存执行事实，Redis 传递消息，单个 Celery Beat 推进周期与补投，Worker 完成业务。调度组件的取舍与恢复边界见 [ADR 0019](docs/adr/0019-scheduled-execution-and-write-coordination.md)，部署形态见 [容器部署文档](docs/container_deployment.md)。

## 本地启动

前置：需要一个可连接的 PostgreSQL（独立 Database `news_vector_lc`，表结构由 Alembic 迁移建），
`DATABASE_URL` 指向它；任务消息使用项目共用的 Redis。Qdrant、Ollama、Redis 和 MinIO/S3 私有桶的配置见 `backend/README.md` 的「外部依赖」。

clone 之后先把 git hook 指到仓库里那份，否则提交前不会校验 ADR 与术语表的格式（这是本机 git 配置，不随仓库走）：

```bash
git config core.hooksPath .githooks
```

先启动后端。完整命令与 `.env` 里必须填的键见 `backend/README.md` 的「本地运行」和「配置」：`uv sync` 之后准备 tokenizer 资源、跑 Alembic 迁移，要用 Agent 对话页再执行一次 `agent-lab init-checkpointer`，然后起 uvicorn；Beat 与 Worker 各占一个终端，Windows 本地把 Worker 换成 `--pool=solo --concurrency=1`。只启动 API 时受理照常持久保存，耗时工作等 Worker 起来再推进。

再启动前端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器访问 <http://127.0.0.1:5173>，使用 `.env` 中配置的那个超级用户登录；普通账号通过设置中心
（侧栏账号区进入，手机先展开导航）自助改密，超级用户在 `/admin/users` 添加和管理其他账号。详细前端命令见 `frontend/README.md`，生产发布步骤见
`docs/container_deployment.md`，当前能力清单见 `docs/FEATURE_MAP.md`。
