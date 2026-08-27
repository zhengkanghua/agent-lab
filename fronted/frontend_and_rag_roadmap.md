# 前端工作台与 RAG 演进方案（讨论留档）

> 状态：已确认当前阶段方向，尚未开始前端实现  
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
3. 前端调用现有 `POST /vector-search`。
4. 页面展示后端按 Qdrant score 排序返回的相关 Chunk。
5. 页面完整处理加载、成功、空结果、请求校验失败和上游服务失败状态。

第一版允许同一篇新闻的多个 Chunk 同时出现，不做 document 聚合、去重、重排、
关键词高亮或相邻 Chunk 自动扩展。它们可以在获得真实使用反馈后单独演进。

### 2.2 当前明确不做

- 不调用生成式 LLM，不生成自然语言答案。
- 不做聊天界面、聊天记录、会话管理或 WebSocket/SSE。
- 不做用户注册、用户资料、角色或完整用户认证系统。
- 不在页面提供新闻同步、索引或 `POST /pipeline/run-once` 操作按钮。
- 不修改或删除后端现有 API。
- 不为了未来 RAG 提前建立只有接口、没有第二种实现的抽象层。
- 第一版不处理同一新闻多个 Chunk 的结果聚合。

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
    -> POST /api/vector-search
    -> Nginx reverse proxy
    -> FastAPI POST /vector-search
    -> OllamaEmbeddingProvider.embed_query()
    -> Qdrant current Alias query
    -> VectorSearchResult[]
    -> Frontend Chunk Result List
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

### 5.1 第一版唯一消费的业务 API

前端接入不得删除、重命名或改变后端现有 API；所有现有接口继续保留。第一版页面只
消费下面这个只读搜索接口：

前端只消费：

```text
POST /vector-search
```

生产环境由 Nginx 对外暴露为：

```text
POST /api/vector-search
```

Nginx 可以在转发时去掉 `/api` 前缀，因此第一版不需要为了前端修改后端路由。

最小请求示例：

```json
{
  "query": "央行近期是否调整利率？"
}
```

后端现有默认值负责 `top_k` 和空过滤条件。第一版可以把 Top-K 作为高级设置按需暴露，
但不应要求普通访问者理解 Embedding、Cosine 或 Qdrant 才能完成搜索。

### 5.2 第一版结果展示字段

主要展示：

- `title`：新闻标题；
- `page_content`：命中的 Chunk 正文；
- `source_name`：来源名称；
- `published_at`：可空的新闻发布时间；
- `url`：原文链接；
- `labels`：可空列表形式的新闻标签；
- `score`：相似度排序分数，明确它不是概率或百分比；
- `chunk_index` / `chunk_count`：当前 Chunk 在父文档中的位置，可采用低强调度展示。

`point_id`、`chunk_id`、`document_id`、`content_hash`、Schema 版本和 Embedding 模型等
字段保留在响应契约中，但第一版普通结果列表无需全部展示。需要时可以在开发调试视图
或详情区域显示，不能让底层存储字段主导用户界面。

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
- `lucide-vue-next`：统一图标来源；
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
│   └── vector-search.ts     # /vector-search 的类型化调用
├── components/
│   └── ui/                  # 通用且无业务含义的 UI 原语
├── features/
│   └── semantic-search/
│       ├── components/      # 搜索框、状态、Chunk 结果等业务组件
│       ├── composables/     # 搜索请求与页面行为编排
│       ├── model/           # 展示模型和 API DTO 映射
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

设计应简单、有辨识度、以新闻内容为中心。避免聊天气泡、卡片嵌套、装饰性渐变球、
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

第一版可以先用本地 Vite dev proxy 对接 `127.0.0.1:8000`，公网部署时再落实 Nginx 和
进程编排。是否使用 Docker Compose 在部署阶段根据现有 PostgreSQL、Ollama 和 Qdrant
的实际托管方式决定。

## 10. Access Key 与公网安全边界

### 10.1 已确认的产品约束

- 不建立用户系统。
- 公网页面要求访问者输入一个共享 Access Key 后才能使用。
- 后端 API 也必须执行 Key 校验，不能只在前端隐藏页面。
- 第一开发阶段暂不实现限流。
- 第一开发阶段暂不在页面暴露 Pipeline 操作。
- 用户希望 Key 方案后续比硬编码单一字符串更完整。

### 10.2 推荐的分阶段实现

本地前端联调阶段可以暂不启用 Access Key，避免身份逻辑阻塞核心搜索流程。

公网发布前推荐实现“共享 Access Key 换短期 Session”：

```text
访问者输入 Access Key
    -> POST /api/access/session
    -> 后端使用环境变量中的 Key 摘要进行恒定时间校验
    -> 成功后返回短期 HttpOnly + Secure + SameSite Cookie
    -> /api/vector-search 校验 Session Cookie
```

该方案仍然没有用户、账号或数据库用户表，但长期 Key 不需要由前端保存并在每次搜索时
重复发送。Session 应有明确过期时间，退出操作只需清除 Cookie。

如果为了更快上线而先使用 `X-API-Key`：

- Key 只能由访问者输入，不能编译进前端；
- 不放入 URL、查询参数、日志或持久化 Local Storage；
- 最多只在当前页面内存或 Session Storage 中短暂保存；
- 后端从 Secret/环境变量读取期望值，并采用恒定时间比较；
- 认证失败响应不能泄露 Key 是否部分匹配；
- 后续应迁移到短期 HttpOnly Session。

### 10.3 必须明确接受的风险

复杂 Key 只能降低“猜中”的概率，不能阻止拥有页面访问权限的人在浏览器开发者工具中
观察自己发送的请求，也不能阻止 Key 被分享或泄露。Cloudflare 可以提供 DDoS、防火墙、
Bot 和限流能力，但这些能力需要显式配置；仅仅把域名托管在 Cloudflare 不等于已经防止
撞库、凭据泄露或接口滥用。

共享 Key 如果同时有 `/vector-search` 和 `/pipeline/run-once` 权限，任何拿到展示 Key
的人都能触发 FreshRSS、PostgreSQL、Ollama 和 Qdrant 写操作。这与是否存在用户系统无关。
因此在真正公网发布前，至少落实以下一种最小边界：

1. Nginx 不向公网路由 `/api/pipeline/*`，Pipeline 只允许 VPS 本机/SSH 调用；或者
2. 搜索 Key 与管理 Key 分离，并在后端校验明确的 scope。

当前可以暂缓编写这部分代码，但不能把“未隔离 Pipeline”作为最终公网架构。未来接入
LLM 后单次请求成本更高，届时还应重新评估 IP 限流、每日额度和 Cloudflare Turnstile。

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

### 阶段 E：VPS 公网发布

- 构建前端静态产物；
- 配置 Nginx 同域 `/api` 代理；
- 实现 Access Key 或短期 Session；
- 保证 Pipeline 写入口不被展示 Key 直接授权；
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
- 有效请求能够展示与后端顺序一致的 Chunk 结果。
- 重复 document 的 Chunk 不会导致渲染错误。
- 空结果显示明确的空状态，不伪造成异常。
- 422、502、503、504 等后端错误具有可理解且不泄露敏感内容的页面状态。
- 重复快速提交不会出现旧请求覆盖新请求的竞态；必要时取消旧请求。
- 页面刷新和直接访问能够正常加载。
- 桌面与移动端不存在布局重叠、横向溢出或不可操作控件。
- 前端构建不包含后端、Ollama、Qdrant 或 Access Key Secret。
- 前端类型检查、单元测试、生产构建和关键 Playwright 流程通过。
- 后端现有测试继续通过，现有 API 行为不因前端接入发生回归。

## 13. 新工作区/新会话接手说明

切换到父目录后，新会话应先完整阅读：

1. 平台根 `AGENTS.md`；
2. `backend/AGENTS.md`；
3. `backend/README.md`；
4. 本文件 `docs/frontend_and_rag_roadmap.md`；
5. 后端 `schemas/vector_search.py`、`api/vector_search.py` 和对应 API 测试。

接手时不得假定要实现 LLM。当前授权范围是创建 Vue 前端并跑通已有
`POST /vector-search` 的 Chunk 搜索流程。Access Key、VPS 部署和完整 RAG 按上述阶段
单独实施，每次改变后端行为时同步更新 README、类型契约和测试。
