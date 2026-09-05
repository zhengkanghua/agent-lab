# 前端纯前端可视化工具（scripts/）

> 本目录提供一套**不启动后端**也能「真实渲染 + 核验」前端页面与样式的开发工具。
> 面向**人**，也面向**其它 Agent**：下文按「照着做就能用」的标准写，遇到术语先看仓库根
> `AGENTS.md` 与 `CONTEXT.md`。

## 这套东西是干嘛的

Signal Desk 的检索 / Agent / 后台页面都要登录，而登录依赖后端认证
（FastAPI + PostgreSQL 的 HttpOnly Cookie）。后端是一整串重装备（还要 Ollama、Qdrant），
本地未必起得来。为了让「看样式、走页面流程」不依赖后端，我们用 **Playwright + route mock**
在浏览器里把 `/api/**` 请求全部换成模拟数据，前端照常渲染，截图出来看。

`src/` 的单元测试（`vitest`）是另一条验证链，**不能替代**这套真实浏览器渲染核验；反之，
这套工具的 route mock 也**只能用来核验前端状态与样式，不能作为后端已更新或部署成功的依据**
（仓库 `frontend/AGENTS.md` 对 Playwright route mock 的既有约定，这里一致执行）。

## 前置条件（先满足再跑）

1. **前端 dev server 已在跑**：`cd frontend && npm run dev`（默认 `http://localhost:5173`）。
   脚本是连到一个正在跑的前端，不自己起服务。
2. **playwright + chromium 已装**（一次性）：
   ```powershell
   cd frontend
   npm install
   npx playwright install chromium
   ```
   `playwright` 是 `devDependencies`（见 `package.json`）；chromium 二进制下载到
   `%LOCALAPPDATA%\ms-playwright`，不进仓库。
3. **不需要启动后端**（本工具的意义就在这里）；后端起了也不会干扰，route mock 会抢先命中。

## 想自己在浏览器里点一遍（mock API 服务器）

`dev:shots` / `dev:audit` 是自动截图与审计，人不能交互。想亲手操作时，把同一套 mock
数据（`dev-mocks.mjs`）包成本地 HTTP 服务（`dev-mock-server.mjs`），让 Vite 代理指向它：

```powershell
npm run dev:mock-api                                          # 终端一：mock API，127.0.0.1:8788
$env:BACKEND_PROXY_TARGET='http://127.0.0.1:8788'; npm run dev  # 终端二：前端，5173
```

浏览器开 <http://localhost:5173>：初始是未登录态（会被送去登录页），**任意邮箱 + 任意密码**
即可登录为超管 `admin@example.com`，之后检索、Agent 对话、设置中心、后台各页都能真实点击；
退出登录会真的回到登录页。边界与 route mock 相同：数据是假的、SSE 整段到达（不代表真实
流式节奏），不能作为后端已更新或部署成功的依据。想连真实后端时不设 `BACKEND_PROXY_TARGET`
（默认代理到 `127.0.0.1:8000`）即可。

## 怎么跑（两种）

都请在 `frontend/` 下执行，且 dev server 先跑着。

```powershell
# 方式一：逐页截图到 .devshots/（登录、检索·待输入/结果、Agent、设置中心三分区、后台桌面/移动、检索移动）
npm run dev:shots

# 方式二：程序化布局审计（无横向溢出、侧栏与内容不重叠、关键元素在、数据渲染、控制台报错）
npm run dev:audit
```

等价的长命令：

```powershell
node scripts/dev-screenshot.mjs [--port 5173] [--out .devshots]
node scripts/dev-audit.mjs
```

- 换端口：`npm run dev:shots -- --port 5174`（或环境变量 `DEVSHOT_PORT`）。
- 换截图输出目录：`npm run dev:shots -- --out ./.tmp-shots`。

### 预期输出

- `dev:shots` 在 `.devshots/` 生成 `NN-页面名.png`（该目录已加 `.gitignore`，不入库）。
- `dev:audit` 在终端打印逐页检查结果：`overflowX=0 (ok)`、侧边栏 `244` 与内容区左缘 `244`
  是否重叠、后台目录行数、顶栏功能链接集合，以及有没有 `console.error / pageerror`。

## 文件职责

| 文件                         | 作用                                                                             |
| ---------------------------- | -------------------------------------------------------------------------------- |
| `scripts/dev-mocks.mjs`      | **共享**的模拟数据 + `/api` 匹配函数（`matchApi`）。数据字段必须与后端契约一致。 |
| `scripts/dev-screenshot.mjs` | 逐页渲染 + 全页截图（`01..09`），内部拿真实超管会话走各页。                      |
| `scripts/dev-audit.mjs`      | 布局/结构/控制台审计，打印文本报告。                                             |
| `.devshots/`                 | 截图输出目录（gitignore），可随时删除重建。                                      |

两个 `.mjs` 都 `import { matchApi } from './dev-mocks.mjs'`，**mock 只写一份**，别在两边各抄一遍。

## 维护：后端契约变了怎么办（重要）

mock 的字段必须跟得上前端 `src/api/generated/openapi.ts`（由后端 `/openapi.json` 生成）。
后端改字段/路由后，**先按仓库规矩重新生成 openapi.ts**，再看 `dev-mocks.mjs` 里对应对象要不要补
字段/改名，否则页面会渲染出「加载失败/空表」（前端校验通不过）。

踩坑过的例子，改的时候对照检查：

- `/admin/users` 列表里的每个用户都要含 `updated_at`（前端 `isUserAdminDto` 校验），
  少了会让整个后台目录报「无法读取账号列表」。
- Agent 对话是 SSE：`/agent/chat` 的 body 是若干 `data: {...json...}` 帧，帧间空行 `\n\n`
  分隔；事件类型写在 JSON 的 `event` 字段（`token` / `tool_call` / `tool_result` / `done`），
  不是 SSE 的 `event:` 字段。`done` 必须带合法 UUID 的 `thread_id`。

## 边界与常见坑

- **route 判定只拦 `/api/` 前缀**（脚本里按 `url.pathname.startsWith('/api/')`），
  别误伤 dev server 自己的 `/src/api/*.ts` 源码请求——那是 Vite 在发模块，拦了页面直接白屏。
- `/auth/me` 在 `dev:audit` 第一步故意返回 401 以渲染登录页，`console` 里那条 401 是**预期内**的，
  不代表有问题。
- 想加新页面流程：在 `dev-screenshot.mjs` 的流程里加一步 `goto + 截图`，mock 数据不够就补
  `dev-mocks.mjs`。
- 这些脚本用 `console.log` 输出；`scripts/` 已在 `eslint.config.mjs` 的 `ignores` 里，
  不参与前端 lint/typecheck（它们是 Node 脚本，不守 `src/` 的分层铁律）。

## 想给后端也起一整套做真联调

本工具**不是**真联调替代品：SSE 是否真的逐 token 流式、慢接口行为等，只有连真后端才能验收。
真联调步骤见 `frontend/README.md` 的「开发/验证」与仓库根 `README.md` 的本地启动。
