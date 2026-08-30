# Signal Desk 前端

Signal Desk 是新闻语义检索工作台的 Vue 3 前端。检索页提供两个只读搜索模式：默认
“按新闻”调用 `POST /document-search`，按每篇新闻最高 Qdrant Cosine score 展示结果组；
“按片段”调用兼容的 `POST /vector-search`，逐条保留后端返回的原始 Chunk 顺序和重复
新闻。用户点击“阅读全文”后才调用 `GET /documents/{document_id}` 读取 PostgreSQL 当前
完整正文。这两个模式本身不调用生成式 LLM：它们只返回检索到的原文片段。

`/agent` 是另一条链路：超级用户在那里提问，由后端 Agent 自己决定检索哪些新闻、要不要读
全文，再基于查到的内容作答，回答与工具调用轨迹以 SSE 流式到达。它同样只读——不写
PostgreSQL 业务表、不写 Qdrant。前端不提供 Pipeline 写入入口。

浏览器启动时通过 `GET /auth/me` 恢复 HttpOnly Cookie 会话；未登录时进入 `/login`。
登录使用 `POST /auth/login` 的表单编码，前端不读取 Cookie，也不在 Local Storage 或
Session Storage 保存密码和 Token。退出调用 `POST /auth/logout` 撤销数据库 Token，并
清空 Vue Query 缓存，避免同一浏览器的后续账号读取前一个账号缓存。

超级用户可进入 `/admin/users`：页面通过受后端权限保护的 `/admin/users` API 创建账号、
启用/停用、授予/撤销超级用户权限、重置密码和撤销会话。由部署端
`AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD` 托管的保底管理员会以独立横线样式显示，网页
不能停用、降级或重置其密码；这些值只能在服务端 `.env`/Secret 中修改。普通用户手动
访问该路由会被前端送回搜索页，而真正的安全边界仍是后端 `current_superuser` 依赖。

## 交互与数据边界

- “按新闻”中 `document_limit` 控制不同新闻数量（下限 1、默认 10），
  `matches_per_document` 控制每篇新闻返回的有限相关片段数量。分组由后端 Qdrant grouped
  query 完成，前端直接按返回顺序渲染。
- “按片段”中 `top_k` 控制原始 Chunk 数量（下限 1、默认 10）；同一 document 的多个 Chunk
  会分别出现，后端已排好序，前端不再重排。
- 每篇新闻默认只展示最高分片段，其他相关片段使用无框分隔列表展开；score 始终显示
  原始数值，不转换成概率或百分比。
- 全文由 Vue Query 以 `document_id + content_hash` 为缓存 key 按需加载，关闭或快速
  切换时取消旧请求；全文失败只影响阅读面板，不清空搜索结果。
- 搜索 hash 与 PostgreSQL 当前 hash 不同时展示版本更新提示，并显示数据库中的最新
  纯文本；正文使用 Vue 文本插值，不使用 `v-html`。
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
- 回答、工具参数和工具返回内容全部走 Vue 文本插值，不用 `v-html`：这些文本里含模型输出和
  RSS 抓来的外部内容。
- 会话 id 由服务端在 `done` 事件里给出，前端不自己生成 UUID；“新会话”会同时丢掉它，否则
  模型还看得见用户以为已经删掉的历史。
- 自定义系统提示词只影响之后发出的轮次，不做任何持久化。
- 取消一轮对话靠两道闸，`AbortController` 之外还有一个自增序号：事件已经拿在手里、`await`
  还没恢复的那个窗口里 abort 拦不住任何东西，只有比对序号能阻止一次已取消的运行往界面写字。
  取消后到达的 `done` 因此也不会写回会话 id。
- 工具调用和工具结果按**工具名先来先配**：后端 `tool_result` 事件不带调用 id，同名工具在一轮里
  并发两次时，只能假定结果按调用顺序返回。配错的表现是两条轨迹的参数与输出对调，不影响答案
  正文。结果找不到对应调用时单独显示成一条轨迹——宁可显示一条来源不明的工具结果，也不要让
  用户以为模型没查资料。

## 开发

```powershell
npm install
npm run dev
```

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

```powershell
npm run typecheck
npm run lint
npm run format:check
npm run test:run
npm run build
```

`vue-tsc` 的 `-b` 是必需的：本项目是 solution 风格 tsconfig（根 tsconfig 只有 `references`），
不加 `-b` 读不到子项目，会报 0 个错误并正常退出。

`src/api/generated/openapi.ts` 由后端 `/openapi.json` 使用 `openapi-typescript` 生成（文件头有
生成声明）。后端契约变化后，在后端服务运行时重新执行：

```powershell
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/generated/openapi.ts
```

## 目录边界

- `src/api`：Cookie 登录、账号管理、HTTP 客户端、文档搜索/详情、Agent SSE 流、错误归一化
  和生成类型；
- `src/features/auth`：当前用户会话恢复、登录、退出和过期状态；
- `src/features/semantic-search`：文档/Chunk 搜索状态、全文 Query、展示模型和两类结果
  组件；
- `src/features/agent-chat`：多轮对话状态、工具轨迹配对、错误文案表和对话组件；
- `src/pages`：登录、检索、Agent 对话与超级用户账号管理的路由级组合，不直接执行 `fetch`；
- `src/styles`：设计令牌（`tokens.css`）；全局 reset/base/components 分层写在 `src/style.css`。
