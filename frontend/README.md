# Signal Desk 前端

Signal Desk 是新闻语义检索工作台的 Vue 3 前端。当前版本提供两个只读搜索模式：默认
“按新闻”调用 `POST /document-search`，按每篇新闻最高 Qdrant Cosine score 展示结果组；
“按片段”调用兼容的 `POST /vector-search`，逐条保留后端返回的原始 Chunk 顺序和重复
新闻。用户点击“阅读全文”后才调用 `GET /documents/{document_id}` 读取 PostgreSQL 当前
完整正文。它不调用生成式 LLM，也不提供 Pipeline 写入入口。

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
  `matches_per_document` 控制每篇新闻返回的有限相关片段数量；前端不会对有限的 Chunk
  top-k 做伪分组。
- “按片段”中 `top_k` 控制原始 Chunk 数量（下限 1、默认 10）；结果不按 document 去重，
  也不在前端重新排序。
- 每篇新闻默认只展示最高分片段，其他相关片段使用无框分隔列表展开；score 始终显示
  原始数值，不转换成概率或百分比。
- 全文由 Vue Query 以 `document_id + content_hash` 为缓存 key 按需加载，关闭或快速
  切换时取消旧请求；全文失败只影响阅读面板，不清空搜索结果。
- 搜索 hash 与 PostgreSQL 当前 hash 不同时展示版本更新提示，并显示数据库中的最新
  纯文本；正文使用 Vue 文本插值，不使用 `v-html`。
- 桌面端使用右侧阅读面板，移动端使用全屏阅读层；支持 Esc、明确关闭按钮、焦点约束
  和关闭后的触发按钮焦点恢复。

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
`/admin/users`、`/document-search`、`/vector-search` 和 `/documents/{document_id}`，然后
进行真实联调。
Playwright route mock 只用于隔离验证前端状态，不能作为后端已经更新或部署成功的依据。

## 验证

```powershell
npm run typecheck
npm run lint
npm run format:check
npm run test:run
npm run build
```

`src/api/generated/openapi.ts` 由后端 `/openapi.json` 使用 `openapi-typescript` 生成，
不能手工修改。后端契约变化后，在后端服务运行时重新执行：

```powershell
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/generated/openapi.ts
```

## 目录边界

- `src/api`：Cookie 登录、账号管理、HTTP 客户端、文档搜索/详情、错误归一化和生成类型；
- `src/features/auth`：当前用户会话恢复、登录、退出和过期状态；
- `src/features/semantic-search`：文档/Chunk 搜索状态、全文 Query、展示模型和两类结果
  组件；
- `src/pages`：登录、搜索与超级用户账号管理的路由级组合，不直接执行 `fetch`；
- `src/styles`：设计令牌（`tokens.css`）；全局 reset/base/components 分层写在 `src/style.css`。
