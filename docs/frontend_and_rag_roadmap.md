# 前端工作台与 RAG 演进方案（讨论留档）

> 状态：Signal Desk 已完成文档级分组、原始 Chunk 双模式搜索与全文懒加载；生成式 RAG
> 仍未实施  
> 记录日期：2026-08-16  
> 用途：在切换到新的父目录工作区后，为开发者和新 Codex 会话提供自包含的项目背景、
> 已确定决策、风险边界和实施顺序。

## 1. 项目目标

当前项目是新闻知识入库和语义检索基础设施，不在当前阶段接入生成式 LLM。系统已经
完成 FreshRSS、PostgreSQL、LangChain Document/Chunk、Ollama Embedding、Qdrant 和
`POST /vector-search` 搜索链路。

当前阶段要增加一个面向公网演示的“新闻研究工作台”前端，让访问者输入文本并查看
语义相关的 Chunk。项目主要用于学习和面试展示，因此代码结构、交互完整性和视觉质量
都应达到可讲解、可维护的水平，但第一版不追求复杂业务能力。

未来可能增加生成式 LLM，形成完整 RAG 流程。因此当前实现要保留清晰的 API 和模块
边界，但不提前创建没有真实行为的 RAG、Chat、Prompt 或流式输出空模块。

## 2. 当前阶段已经确认的范围

### 2.1 第一版用户流程

1. 访问者打开页面后直接看到搜索工作台，不经过营销型 Landing Page。
2. 访问者输入一段文本并点击搜索。
3. 默认“按新闻”调用 `POST /document-search`，后端由 Qdrant grouped query 返回不同
   新闻；用户也可切换到“按片段”，调用 `POST /vector-search` 查看原始 Chunk 命中。
4. 文档模式按每篇新闻的最高 Qdrant score 展示结果组，最高分片段默认可见，其他相关
   片段可在组内展开；Chunk 模式保留后端顺序和重复 document，不去重或重排。
5. 两种模式下，用户点击“阅读全文”时，前端才调用 `GET /documents/{document_id}` 从
   PostgreSQL 读取当前完整纯正文；搜索和全文错误状态互不覆盖。
6. 页面完整处理加载、成功、空结果、请求校验失败和上游服务失败状态。

原始 `POST /vector-search` 仍允许同一篇新闻的多个 Chunk 分别出现，作为兼容的底层
Chunk 契约；Signal Desk 的“按片段”模式会直接消费它，但不会拿有限 top-k 在前端伪装
文档聚合。当前仍不做关键词高亮、相邻 Chunk 自动扩展或文章全部物理 Chunk 接口。

### 2.2 当前明确不做

- 不调用生成式 LLM，不生成自然语言答案。
- 不做聊天界面、聊天记录、会话管理或 WebSocket/SSE。
- 不做公开注册、用户资料或通用角色系统；只保留内部账号 Cookie 登录和超级用户边界。
- 不在页面提供新闻同步、索引或 `POST /pipeline/run-once` 操作按钮。
- 不修改或删除后端现有 `POST /vector-search` API。
- 不为了未来 RAG 提前建立只有接口、没有第二种实现的抽象层。
- 不在前端对有限的 Chunk top-k 做文档聚合；聚合必须由 Qdrant grouped query 完成。

## 3. 平台目录与仓库组织

建议创建一个父项目，以单个 Git 仓库管理两个运行时独立的项目：

```text
news-rag-platform/
├── backend/                 # 当前 news-vector-service-LangChain 项目
│   ├── pyproject.toml
│   ├── src/
│   ├── tests/
│   └── AGENTS.md
├── frontend/                # 新建 Vue 项目
│   ├── package.json
│   ├── src/
│   └── tests/
├── infra/
│   └── nginx/               # 同域静态资源与 /api 反向代理配置
├── docs/                    # 平台级架构、部署和决策文档
├── compose.yaml             # 后续按实际部署需要增加
├── AGENTS.md                # 前后端通用规则；各子项目可继续有局部规则
└── README.md                # 面试展示入口、架构图和本地启动说明
```

这里的“单仓库”不代表前后端实现混合。必须保持以下边界：

- `backend` 和 `frontend` 分别拥有自己的依赖、构建、测试和运行命令。
- 前端只能通过 HTTP/OpenAPI 契约访问后端，不能导入 Python 模块。
- 后端不能导入前端模块，也不在 FastAPI 业务层拼装页面。
- 前端不能直接连接 PostgreSQL、Ollama 或 Qdrant，也不能持有这些服务的凭据。
- 根目录只负责编排、部署、平台文档和统一开发入口。

用户创建并打开 `news-rag-platform` 父目录后，Codex 才能稳定地把前后端作为同一个
工作区理解。当前文档随原项目移动后会暂时位于
`backend/docs/frontend_and_rag_roadmap.md`；完成重组时应把它复制或移动到平台根目录
的 `docs/` 下，并保留后端 README 对自身能力的说明。

## 4. 前后端运行架构

### 4.1 当前语义检索

```text
Browser
    -> Frontend Search Feature
    -> 按新闻：POST /api/document-search
       或按片段：POST /api/vector-search
    -> Nginx reverse proxy
    -> FastAPI 对应只读搜索路由
    -> OllamaEmbeddingProvider.embed_query()
    -> Qdrant current Alias grouped query / raw query
    -> DocumentSearchResult[] / VectorSearchResult[]
    -> Frontend 新闻结果组 / 原始 Chunk 列表
    -> 用户点击阅读全文
    -> GET /api/documents/{document_id}
    -> PostgreSQL documents.content_text
    -> 阅读面板/移动全屏层
```

### 4.2 未来完整 RAG

```text
Browser
    -> POST /api/rag-answer
    -> FastAPI RAG Service
        -> 复用现有 VectorSearchService
        -> 选择并限制相关 Chunk 上下文
        -> 组装 Prompt
        -> 调用 Generative LLM
        -> 返回 Answer + Sources
```

未来应新增独立的 RAG API 和 Service，不把生成逻辑塞入
`VectorSearchService.search()`，也不改变 `/vector-search` 返回原始 Chunk 的语义。
是否支持流式回答、对话历史和具体 LLM Provider，留到真正开始 RAG 阶段再决定。

## 5. API 边界

### 5.1 当前前端消费的只读业务 API

前端接入不得删除、重命名或改变后端现有 API；所有现有接口继续保留。第一版页面只
消费下面三个只读接口：

前端消费：

```text
POST /document-search
POST /vector-search
GET  /documents/{document_id}
```

生产环境由 Nginx 对外暴露为：

```text
POST /api/document-search
POST /api/vector-search
GET  /api/documents/{document_id}
```

Nginx 可以在转发时去掉 `/api` 前缀，因此第一版不需要为了前端修改后端路由。

文档分组请求示例：

```json
{
  "query": "央行近期是否调整利率？",
  "document_limit": 10,
  "matches_per_document": 3
}
```

后端默认值负责 `document_limit`、`matches_per_document` 和空过滤条件。原始
`top_k` 只属于兼容的 `/vector-search`，在“按片段”模式控制原始 Chunk 数量；
`document_limit` 和 `top_k` 的前端可选下限均为 1、默认均为 10。普通访问者不需要理解
Embedding 或 Qdrant 即可使用默认文档模式，原始 Cosine score 在两种模式都不显示为概率。

### 5.2 文档级结果展示字段

主要展示：

- `title`：新闻标题；
- `best_match.page_content`：最高分命中的 Chunk 正文；
- `additional_matches`：同一新闻本次返回的其他相关片段（有限集合，不是全部 Chunk）；
- `source_name`：来源名称；
- `published_at`：可空的新闻发布时间；
- `url`：原文链接；
- `labels`：可空列表形式的新闻标签；
- `best_score`：相似度排序分数，明确它不是概率或百分比；
- `chunk_index` / `chunk_count`：当前 Chunk 在父文档中的位置，可采用低强调度展示。

`chunk_id`、`document_id`、`content_hash` 等字段保留在响应契约中；全文接口返回当前
`revision` 和 `content_hash`，前端用 hash 检测搜索索引与业务正文版本差异。原始
`VectorSearchResult` 字段仍保留给兼容的底层 Chunk API。

### 5.3 契约管理

- FastAPI 的 `/openapi.json` 是前端 API 类型的唯一事实来源。
- 使用 `openapi-typescript` 生成 TypeScript 类型，生成文件不得手工修改。
- 可以使用 `openapi-fetch` 或等价的轻量客户端消费生成类型。
- 后端契约改变时，应重新生成类型并让 TypeScript 编译暴露不兼容变化。
- 前端的展示模型可以由 API DTO 显式映射而来，避免页面组件依赖全部后端字段。

## 6. 前端技术选型

已经确定的基础框架：

- Vue 3；
- TypeScript；
- Vite。

建议按实际需要采用以下成熟工具，避免重复实现通用能力：

- Vue Router：管理搜索页、未来 RAG 页或访问入口；
- `@tanstack/vue-query`：管理服务端请求、加载、错误、取消和缓存状态；
- Tailwind CSS：建立可控的响应式设计和 Design Token；
- shadcn-vue / Reka UI：按需使用无障碍交互原语，不整包引入不需要的组件；
- `@lucide/vue`：统一图标来源；不要恢复已经弃用的 `lucide-vue-next`；
- `openapi-typescript` + `openapi-fetch`：生成并消费 FastAPI 类型契约；
- Vitest + Vue Test Utils：组件和组合式函数测试；
- Playwright：真实桌面和移动视口的关键流程验证；
- ESLint、Prettier、`vue-tsc`：静态检查、格式化和严格类型检查。

第一版没有跨页面复杂客户端状态，不默认引入 Pinia。只有出现真实的跨路由共享状态后
再增加状态库。`@tanstack/vue-query` 管理的是远程服务状态，不能把 API 结果重复复制到
全局 Store。

具体依赖版本应在创建前端项目时选择当时的稳定兼容版本，本留档不固定可能过期的版本号。

## 7. 前端模块边界

建议的初始结构：

```text
frontend/src/
├── app/                     # 应用创建、Router、全局 Provider 和错误边界
├── api/
│   ├── generated/           # OpenAPI 生成类型，只生成不手改
│   ├── client.ts            # base URL、公共 header 和错误归一化
│   ├── document-search.ts   # /document-search 的类型化调用
│   ├── documents.ts         # /documents/{document_id} 的类型化调用
│   └── vector-search.ts     # 兼容的 /vector-search 类型化调用
├── components/
│   └── ui/                  # 通用且无业务含义的 UI 原语
├── features/
│   └── semantic-search/
│       ├── components/      # 搜索框、状态、文档结果组和全文阅读层
│       ├── composables/     # 搜索与全文 Query 行为编排
│       ├── model/           # 文档展示模型和 API DTO 映射
│       └── tests/
├── pages/                   # 路由级页面，只组合 Feature，不实现 API 细节
├── styles/                  # Design Token、全局样式和 Tailwind 入口
└── main.ts                  # 极薄的应用启动入口
```

约束：

- 页面组件不直接散落原生 `fetch()`；统一通过 `api/` 和 Feature composable 调用。
- `components/ui` 不依赖新闻、Chunk 或 Qdrant 等业务概念。
- `features/semantic-search` 不依赖未来尚未存在的 RAG Feature。
- 不创建笼统的 `helpers` 或 `common` 大杂烩；可复用代码按真实职责归属。
- 不提前创建空的 `features/rag`，开始实现生成式流程时再增加。

## 8. 页面和视觉方向

产品方向是“新闻研究工作台”，不是聊天机器人，也不是营销 Landing Page。

第一屏直接提供可操作的搜索体验：

- 清楚但不过度夸张的产品名称；
- 主搜索输入框和明确的搜索命令；
- 稳定、不会因加载和结果数量跳动的结果区域；
- 适合扫描和阅读的紧凑信息层级；
- 加载骨架、空结果、请求错误、上游不可用等完整状态；
- 桌面和移动视口都不发生文字截断、控件重叠或横向溢出；
- 原文链接、来源和发布时间具有清晰但克制的视觉权重。

设计应简单、有辨识度、以新闻内容为中心。结果组内使用分隔线列表而不是卡片嵌套；
避免聊天气泡、装饰性渐变球、
大面积营销 Hero 和只使用单一色系的模板化页面。图标优先使用 Lucide，按钮和筛选控件
遵守熟悉的交互模式。

第一版不需要为了“显得像 AI”加入模型动画、流式打字或不可解释的相关度可视化。

## 9. VPS 与同域部署

当前优先部署目标是用户自己的 VPS，域名由 Cloudflare 管理：

```text
Internet
    -> Cloudflare DNS / Proxy / TLS
    -> VPS Nginx
        -> /               前端构建后的静态文件
        -> /api/*          FastAPI 内部地址
    -> FastAPI
        -> PostgreSQL / Ollama / Qdrant
```

部署约束：

- 浏览器始终访问同一域名，前端 API base URL 使用相对路径 `/api`。
- FastAPI、PostgreSQL、Ollama 和 Qdrant 不直接暴露到公网，只让 Nginx 或内部网络访问。
- TLS 在 Cloudflare/Nginx 边界正确配置；不能因为使用 Cloudflare 就跳过源站访问控制。
- 密钥只通过 VPS 环境变量或 Secret 管理，不写入 Git、镜像层、前端构建变量或日志。
- `VITE_*` 环境变量会进入浏览器产物，绝不能存放服务端 Secret。

本地使用 Vite dev proxy 对接 `127.0.0.1:8000`；公网部署按
[`docs/vps_deployment.md`](vps_deployment.md) 落实 Nginx、systemd 和 Cloudflare。是否
使用 Docker Compose 仍根据现有 PostgreSQL、Ollama 和 Qdrant 的实际托管方式决定。

## 10. 内部账号与公网安全边界

### 10.1 已确认的产品约束

- 不开放公开注册、找回密码、用户资料或通用 RBAC；只提供受超级用户保护的窄范围账号
  管理页。
- `.env`/部署 Secret 只托管一个保底超级管理员，不能把所有用户序列化进环境变量。
- 保底管理员登录后通过 `/admin/users` 创建和管理后续账号；CLI 仅作恢复工具。
- FastAPI 必须保护真实 API，不能只依赖 Vue Router 隐藏页面。
- 普通有效账号只访问搜索与全文；超级用户才可调用 Pipeline。
- 前端不提供 Pipeline 操作入口，限流继续由生产网关配置。

### 10.2 推荐的分阶段实现

当前已经实现本地账号密码和网页账号管理：

```text
AUTH_ADMIN_EMAIL + AUTH_ADMIN_PASSWORD
    -> 服务启动同步唯一环境托管超级用户
    -> POST /api/auth/login 校验邮箱和密码
    -> PostgreSQL access_tokens 保存随机短期 Token
    -> 浏览器只获得 HttpOnly + Secure + SameSite=Strict Cookie
    -> 搜索/全文要求 active user，Pipeline 要求 superuser
    -> 超级用户打开 /admin/users 创建/停用/授权/重置/撤销其他账号
    -> POST /api/auth/logout 删除数据库 Token
```

项目不使用共享 `X-API-Key` 或 Local Storage JWT，也不把账号密码编译进前端。
`AUTH_COOKIE_SECURE` 在生产必须为 true；本地 HTTP 开发才显式设为 false。Session 默认
8 小时，账号被禁用后即使数据库 Token 尚未过期也不能继续访问。

### 10.3 必须明确接受的风险

账号密码登录不能替代 HTTPS、源站访问控制、登录限流和数据库备份。Cloudflare 可以提供
DDoS、防火墙、Bot 和限流能力，但都需要显式配置；仅托管域名不等于已经防止撞库或接口
滥用。生产网关仍应限制 `/api/auth/login` 频率，并可选择不向公网路由
`/api/pipeline/*`。未来接入高成本 LLM 后还要重新评估每日额度和滥用检测。

## 11. 分阶段实施顺序

### 阶段 A：工作区重组与留档迁移

- 创建 `news-rag-platform` 父目录；
- 把当前项目完整移动到 `backend/`；
- 创建空的 `frontend/` 位置，但不手工拼装框架文件；
- 把本留档迁移到平台根 `docs/`；
- 从父目录重新打开 IDE/Codex 工作区；
- 确认后端原有测试和命令仍从 `backend/` 内执行。

### 阶段 B：前端脚手架与架构骨架

- 使用官方 Vite Vue + TypeScript 模板创建项目；
- 配置严格 TypeScript、Lint、格式化和测试；
- 建立 `app/api/components/features/pages/styles` 边界；
- 接入 Tailwind、Lucide 和按需 UI 原语；
- 从后端 OpenAPI 生成类型；
- 建立开发代理 `/api -> http://127.0.0.1:8000`。

### 阶段 C：语义搜索 MVP

- 实现查询输入和显式搜索命令；
- 调用 `/api/vector-search`；
- 展示原始 Chunk 命中；
- 完成 loading、empty、validation error 和 upstream error 状态；
- 验证重复 Chunk、长标题、长正文、无发布时间和大量标签等边界；
- 增加组件测试和 Playwright 桌面/移动流程。

### 阶段 D：视觉完善与真实后端联调

- 使用真实 Ollama/Qdrant 数据验证相关性和展示字段；
- 调整工作台信息密度、排版、响应式布局和无障碍行为；
- 保持 API 契约不因纯展示需求随意膨胀；
- 记录必要的后端契约改进建议，但不混入前端实现。

### 阶段 E：VPS 公网发布（已落地基础方案）

- 构建前端静态产物；
- 配置 Nginx 同域 `/api` 代理；
- 使用 FastAPI Users DatabaseStrategy 的短期 Cookie Session；
- 用 `.env`/systemd Secret 同步唯一保底管理员，网页管理其他账号；
- 保证 Pipeline 写入口只接受后端超级用户依赖，不能由前端展示状态直接授权；
- 配置 Cloudflare、TLS、Secret 和最小日志；
- 在公网域名上执行桌面、移动和错误场景验收。

### 阶段 F：未来完整 RAG（当前不实施）

- 选择生成式 LLM Provider；
- 新增 RAG Service 和 `/rag-answer`；
- 复用检索 Service，不复制 Qdrant 查询逻辑；
- 设计上下文预算、引用、无证据回答和 Prompt Injection 防护；
- 决定是否需要流式响应和会话历史；
- 前端新增独立 RAG Feature，不破坏语义检索模式。

## 12. 第一版验收标准

- 用户无需理解向量数据库即可完成一次文本搜索。
- 有效请求能够展示按文档最高 score 排序的结果组和相关片段。
- 重复 document 的 Chunk 只产生一个新闻结果组。
- 全文只在用户点击后读取，hash 不一致时给出版本提示。
- 空结果显示明确的空状态，不伪造成异常。
- 422、502、503、504 等后端错误具有可理解且不泄露敏感内容的页面状态。
- 重复快速提交不会出现旧请求覆盖新请求的竞态；必要时取消旧请求。
- 页面刷新和直接访问能够正常加载。
- 桌面与移动端不存在布局重叠、横向溢出或不可操作控件。
- 前端构建不包含后端、Ollama、Qdrant 或账号/Access Key Secret。
- 前端类型检查、单元测试、生产构建和关键 Playwright 流程通过。
- 后端现有测试继续通过，现有 API 行为不因前端接入发生回归。

## 13. 新工作区/新会话接手说明

切换到父目录后，新会话应先完整阅读：

1. 平台根 `AGENTS.md`；
2. `backend/AGENTS.md`；
3. `backend/README.md`；
4. 本文件 `docs/frontend_and_rag_roadmap.md`；
5. 后端 `schemas/vector_search.py`、`api/vector_search.py` 和对应 API 测试。

接手时不得假定要实现 LLM。当前 Signal Desk 默认使用 `POST /document-search` 的文档
分组搜索，也提供直接消费兼容 `POST /vector-search` 的原始 Chunk 模式，并通过
`GET /documents/{document_id}` 懒加载 PostgreSQL 最新全文。账号管理和 VPS 部署基础方案
已按上述约束实施；完整 RAG 仍单独规划，每次改变后端行为时同步更新 README、类型契约和
测试。
