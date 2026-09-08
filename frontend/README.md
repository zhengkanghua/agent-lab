# Signal Desk 前端

Signal Desk 是知识库语义检索工作台的 Vue 3 前端。检索页 `/` 走 `POST /document-search`：
后端用 Qdrant grouped query 按每篇 Document 的最高 Cosine score 做分组，前端把命中结果按
“最新一条检索贴在输入框正下方、旧记录往下沉”的检索流（仿 Agent 会话体感）逐轮向下累积，
多条历史记录可折叠回看，刷新即清空。该页不生成答案、不调用生成式 LLM，只返回检索到的
原文片段；用户点击“阅读全文”后才调用 `GET /documents/{document_id}` 读取 PostgreSQL 当前
完整正文。

`/agent` 是另一条链路：超级用户在那里提问，由后端 Agent 在本次范围内决定检索哪些文档、要不要读
全文，再基于查到的内容作答，回答与工具调用轨迹以 SSE 流式到达。它同样只读——不写
Document、不写 Qdrant；会话归属和范围保存到服务端。`/admin/files` 提供独立的文件管理入口，
上传、替换、索引重试和删除均需超级用户权限。

浏览器启动时通过 `GET /auth/me` 恢复 HttpOnly Cookie 会话；未登录时进入 `/login`。
登录使用 `POST /auth/login` 的表单编码，前端不读取 Cookie，也不在 Local Storage 或
Session Storage 保存密码和 Token。退出调用 `POST /auth/logout` 撤销数据库 Token，并
清空 Vue Query 缓存，避免同一浏览器的后续账号读取前一个账号缓存。

超级用户可进入 `/admin/users`：页面通过受后端权限保护的 `/admin/users` API 创建账号、
启用/停用、授予/撤销超级用户权限、重置密码和撤销会话。由部署端
`AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD` 托管的保底管理员会以独立横线样式显示，网页
不能停用、降级或重置其密码；这些值只能写在服务端 `.env`/Secret 中。普通用户手动
访问该路由会被前端送回搜索页，而真正的安全边界仍是后端 `current_superuser` 依赖。

`/settings` 是设置中心（`/settings/:section?`，旧 `/account` 重定向并入）：账号安全
（登录信息 + 改自己密码）、检索偏好（数量参数，改动即生效）与 Agent 偏好（自定义系统
提示词，仅超级用户）。这些偏好只保存在当前浏览器的 localStorage，不进后端，也不含任何
凭据；见 `docs/adr/0018-settings-hub-with-local-preferences.md`。
Agent 提示词草稿在设置分区之间切换时保留，只有保存后才影响提问；有未保存修改时，
离开设置中心或刷新页面会提示确认。手机顶栏保留账号与设置入口，设置页另有明确的“返回工作台”。

## 交互与数据边界

- 检索页默认所有启用知识库，也可选择非空集合；每次提交固化选择及后端实际范围快照。
  目录加载失败或选择失效时阻止提交，不能悄悄扩大范围。旧 HTTP 缺省 news 契约仍保留。
- 检索页只走按 Document 分组：`document_limit` 控制一次检索的不同文档数量
  （下限 1、默认 10），`matches_per_document` 控制每篇文档返回的相关片段数。两者的
  默认值在设置中心的「检索偏好」分区维护（持久在本浏览器），提交那一刻读到什么值
  这一轮就用什么值；分组由后端 Qdrant grouped query 完成，前端按返回顺序渲染。
- 每次搜索固化成一条“检索记录”，追加成向下长的检索流：最新一条顶在输入框正下方并完整
  展开，旧记录折叠成“检索词 + 命中数”的标题行，可点开回看；刷新或离开页面即清空，不做
  真会话、不落后端。
- 一次只允许一条在途搜索：提交新搜索会取消上一条；输入条顶部常驻，一轮进入终态后仅清空
  本次提交的草稿，等待期间新写的内容会保留。用户仍在原操作位置时恢复输入焦点；正在阅读全文
  或已转到其他控件时，不抢走焦点。
- 每篇文档默认只展示最高分片段，其他相关片段使用无框分隔列表展开；score 始终显示
  原始数值，不转换成概率或百分比。
- 全文由 Vue Query 以 `document_id + content_hash` 为缓存 key 按需加载，每次打开重新核对当前
  正文，加载和失败时隐藏旧缓存；关闭或快速切换时取消旧请求。全文失败不清空检索流。
- 搜索 hash 与 PostgreSQL 当前 hash 不同时展示版本更新提示，并显示数据库中的最新
  正文。文本用 Vue 插值，Markdown 文件用共享 `SafeMarkdown` 安全解析，不使用 `v-html`；
  外链图片不加载。没有 Source 或 URL 的文件也能阅读。
- 桌面端使用右侧阅读面板，移动端使用全屏阅读层；支持 Esc、明确关闭按钮、焦点约束
  和关闭后的触发按钮焦点恢复。

## Agent 对话页的数据边界

- `/agent` 只对超级用户开放：路由 `meta.requiresSuperuser` 提前挡住，真正的安全边界仍是
  后端 `/agent/*` 上的 `current_superuser` 依赖。普通账号手动访问会被送回检索页。
- 流式接口用 `fetch` + `response.body.getReader()`，不用 `EventSource`。后者只能发 GET、
  不能带请求体，提问和自定义提示词就得进 query string，会被网关日志和浏览器历史记下来。
- 超时分两道：连接 30 秒、空闲 60 秒（后端心跳 15 秒，留四倍余量）。不复用 JSON 层的 45 秒
  总时长上限——一次 Agent 运行可能要几分钟，用它会在模型还在写的时候掐断。
- 调用方提前 `break` 时会 `reader.cancel()` 关掉连接，否则后端那次运行会继续跑、继续计费。
- 回答用共享 `SafeMarkdown`，显式 sanitize、不解析原始 HTML、不加载外链图片。
  用户提问、工具参数和工具返回继续按文本展示。只有服务端核验的本次引用能变成阅读入口。
- 会话 id 和实际范围由服务端在 `run_started` 给出；新会话默认所有启用知识库，选择通过独立
  PATCH 保存，重新打开继续沿用。运行期间改选只影响下一次，页面保留每次运行的范围快照。
- 切换历史会话和浏览器前进后退以路由参数为准，离开后取消在途历史加载；迟到响应不会重新
  改写地址。只有新建会话取得服务端 id 时补全当前地址。
- 临时 token 供流式预览，`done.answer` 校正最终文字并给出完成状态及引用；缺少 Done 的流不能
  当成完成。结束后同步最新 checkpoint，压缩后仅展示保留的近期问答；同步失败可重试。
- 点击行内引用或引用列表，在阅读器对照当时片段和当前原文；更新、删除、停用和服务失败分别
  提示。旧回答保持原样，不能把当前正文当成历史版本，也不强行高亮不可靠位置。
- 自定义系统提示词在设置中心的「Agent 偏好」分区编辑，保存在本浏览器的偏好里，
  作用于之后发出的每一轮（任何会话）；清空即回到服务端默认。输入条只在覆盖生效时
  亮一枚链回设置的徽章，不做编辑。
- 取消一轮对话靠两道闸，`AbortController` 之外还有一个自增序号：事件已经拿在手里、`await`
  还没恢复的那个窗口里 abort 拦不住任何东西，只有比对序号能阻止一次已取消的运行往界面写字。
  取消后到达的 `done` 因此也不会写回会话 id。
- 工具调用和结果优先按本轮 `tool_call_id` 配对，缺 ID 的旧记录保留按名字配对；不会跨问答
  配对。缺失结果明确呈现，工具进一步缩小范围时展示实际范围。

## 文件管理的数据边界

`/admin/files` 沿用后台分区，上传 `.txt`、`.md` 后展示等待、处理中、成功或失败状态；
保存成功不等于已经可检索。列表对在途索引定时刷新，失败可重试现有 Document，不重复上传。
替换必须先选明确记录并携带 revision；冲突时关闭旧编辑对象，刷新后重新选择。同名新增
互不覆盖。删除失败保留记录及待办，可继续删除；上传超时不自动重放，先刷新核对结果。

## 开发

```powershell
npm install
npm run dev
```

### 不启动后端也能看页面（scripts/）

要看页面真实渲染出来的样式、或走一遍界面流程，却不想（或暂时起不了）后端时，用
`scripts/` 下的纯前端可视化工具（Playwright route mock 拦截 `/api`，返回契约一致的模拟数据）。
见 [`scripts/README.md`](scripts/README.md)：先起 dev server，再 `npm run dev:shots`（逐页截图）
或 `npm run dev:audit`（布局审计）。注意该工具只用于核验前端，不能当后端已更新/部署成功的依据。

开发服务器把 `/api/*` 代理到 `http://127.0.0.1:8000`，因此浏览器只使用同域相对 API
路径。若需要切换地址，可通过 `VITE_API_BASE_URL` 指向另一个公开的 API 前缀；绝不能把
账号密码或服务端密钥放进 `VITE_*` 变量。保底管理员配置属于后端进程，只能写入
`backend/.env` 或部署 Secret。仅需切换本地代理目标时，设置只由 Vite Node 进程读取、
不会进入浏览器产物的 `BACKEND_PROXY_TARGET`，并继续让浏览器访问 `/api`。

后端新增或修改路由后，先重启 Uvicorn，再确认运行中的
`http://127.0.0.1:8000/openapi.json` 已包含 `/auth/login`、`/auth/logout`、`/auth/me`、
`/admin/users`、`/document-search`、`/vector-search`、`/documents/{document_id}`、
`/agent/chat` 和 `/agent/default-prompt`，然后进行真实联调。

SSE 只能在真实联调里验收：Vite 开发代理、生产 Nginx 和 CDN 都可能缓冲响应，把逐 token
到达变成「一次性出现一整段」。界面上出字不等于流式生效，要看首个 token 与提交之间的间隔。
Playwright route mock 只用于隔离验证前端状态，不能作为后端已经更新或部署成功的依据。

## 验证

定时任务页面通过后端类型元数据取得默认值和参数范围，表单仍是少量显式适配。编辑须先停用并等待当前任务执行结束；手动触发按回执 ID 查询，关闭面板或离开页面不取消服务端执行。回执仅按账号保存在当前标签页的 `sessionStorage`，重新进入页面继续查询；浏览器存储不可用时仍可通过服务端历史查看，不自动重发请求。

账号与定时任务的启停开关显示服务端已确认的状态，请求中禁用，失败后仍可重试同一个操作。
后台手机导航关闭时不能被 Tab 选中；打开后约束焦点并锁定背景滚动，支持 Esc 关闭和焦点恢复。

开发中按改动选择测试文件或目录，不逐次运行全套：

```powershell
npm run test:run -- src/features/semantic-search/tests/SearchResultCard.spec.ts
npm test -- src/features/semantic-search/tests
npx eslint src/features/semantic-search/tests/SearchResultCard.spec.ts --max-warnings 0
npx prettier --check src/features/semantic-search/tests/SearchResultCard.spec.ts
```

类型变化时可单独运行 `npm run typecheck`。需要完整回归或发布时执行：

```powershell
npm run lint
npm run format:check
npm run test:run
npm run build
```

`build` 已包含类型检查，同一份代码无需再单独执行 `typecheck`。`vue-tsc` 的 `-b` 是必需的：本项目是 solution 风格 tsconfig（根 tsconfig 只有 `references`），
不加 `-b` 读不到子项目，会报 0 个错误并正常退出。

组件测试默认使用 `jsdom`；不依赖 DOM 的纯函数与源码检查用文件头
`// @vitest-environment node` 选择 Node 环境。计时行为使用虚拟计时器，避免真实等待防抖或超时。

`src/api/generated/openapi.ts` 由后端 `/openapi.json` 使用 `openapi-typescript` 生成（文件头有
生成声明）。后端契约变化后，在后端服务运行时重新执行：

```powershell
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/generated/openapi.ts
```

## 目录边界

- `src/api`：Cookie 登录、账号管理、HTTP 客户端、文档搜索/详情、Agent SSE 流、错误归一化
  和生成类型；数量参数与提示词上界的契约常量也在这里（`document-search.ts`、
  `agent-chat.ts`），它们是请求契约的一部分；
- `src/features/auth`：当前用户会话恢复、登录、退出和过期状态；
- `src/features/semantic-search`：文档搜索状态（多轮检索流）、全文 Query、展示模型和
  检索流组件（输入条 / 单条记录 / 结果卡）；
- `src/features/agent-chat`：多轮对话状态、工具轨迹配对、错误文案表和对话组件；
- `src/features/file-documents`：文件上传、列表、替换、索引重试与删除状态；
- `src/shared/ui/SafeMarkdown.vue`：答案与 Markdown 文件共用的安全渲染器；
- `src/shared/composables/useKnowledgeBaseScope.ts`：启用知识库目录与选择有效性；
- `src/features/settings`：设置中心（账号安全 / 检索偏好 / Agent 偏好）与浏览器本地
  偏好 store；
- `src/pages`：登录、检索、Agent 对话、设置中心与后台控制台（单路由
  `/admin/:section?`，AdminPage 按分区组合账号、知识库、来源、文件与定时任务）的路由级组合，
  不直接执行 `fetch`；
- `src/styles`：设计令牌（`tokens.css`）；全局 reset/base/components 分层写在 `src/style.css`。
