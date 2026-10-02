# Signal Desk 前端

Signal Desk 是知识库语义检索工作台的 Vue 3 前端。检索页 `/` 走 `POST /document-search`：
后端按 Document 分组返回相关片段（分组与排序规则在后端，见 `backend/docs/architecture.md`），
前端把命中结果按“旧的在上、最新的贴在底部输入坞上方”的检索流（仿 Agent 会话体感）
逐轮向下累积，多条历史记录可折叠回看，刷新即清空。该页不生成答案、不调用生成式 LLM，只返回检索到的
原文片段；用户点击“阅读全文”后才调用 `GET /documents/{document_id}` 读取 PostgreSQL 当前
完整正文。

`/agent` 是另一条链路：登录账号在那里提问，由后端 Agent 在本次范围内决定检索哪些文档、要不要读
全文，再基于查到的内容作答，回答与工具调用轨迹以 SSE 流式到达。它同样只读——不写
Document、不写 Qdrant；会话归属和范围保存到服务端。`/admin/files` 提供独立的文件管理入口，
上传、替换和删除均需超级用户权限；`/admin/documents` 统一审核文件和 FreshRSS 资料。

浏览器启动时通过 `GET /auth/me` 恢复 HttpOnly Cookie 会话；未登录时进入 `/login`。
登录使用 `POST /auth/login` 的表单编码，前端不读取 Cookie，也不在 Local Storage 或
Session Storage 保存密码和 Token。退出调用 `POST /auth/logout` 撤销数据库 Token，并
清空 Vue Query 缓存，避免同一浏览器的后续账号读取前一个账号缓存。

超级用户可进入 `/admin/users`：页面通过受后端权限保护的 `/admin/users` API 创建账号、
启用/停用、重置密码、撤销会话，以及注销账号；**超级用户身份只在建号时指定，之后没有改它的入口**
（见 `docs/adr/0038-superuser-identity-fixed-at-creation.md`）。注销保留账号行、
会话归属与个人偏好，只清掉登录 Token；已注销的账号默认不在列表里，用「显示已注销」开关
才看得到，它们显示成「已注销」且不再提供停用/启用、注销与重置密码。自己那一行也不提供
停用/启用与注销（后端另有 `account_self_protected` 规则，不靠界面拦）。由部署端
`AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD` 托管的那个超级用户在列表里单独标出，网页
不能停用、改密或注销它；这些值只能写在服务端 `.env`/Secret 中。普通用户手动
访问该路由会被前端送回搜索页，而真正的安全边界仍是后端 `current_superuser` 依赖。

`/settings` 是设置中心（`/settings/:section?`，旧 `/account` 重定向并入）：账号安全
（登录信息 + 改自己密码）、检索偏好（数量参数，改动即生效）与 Agent 偏好（自定义系统
提示词）。三个分区对所有登录账号开放。偏好归属于账号、存在后端 `user_preferences` 表，
换浏览器或换设备登录都一致；前端不保存任何凭据（见
`docs/adr/0027-user-level-preferences-in-database.md`）。
Agent 提示词草稿在设置分区之间切换时保留，只有保存后才影响提问；有未保存修改时，
离开设置中心或刷新页面会提示确认。桌面设置中心为居中浮层，支持关闭、Esc 和焦点约束；
窄屏回退整页，保留相同路由与深链。返回工作台通过外壳导航，离开时继续保护未保存草稿。

前台的 `AppShell` 用左侧栏承载工作台导航、当前页主操作、Agent 会话列表和底部账号区；
手机通过导航抽屉访问同一组入口，支持焦点约束、Esc、滚动锁和焦点恢复。
检索与 Agent 共用 `ComposerDock` 输入坞，外壳负责导航，页面负责检索流或会话内容。

后台的唯一入口位于前台侧栏底部，仅超级用户可见，指向 `/admin`；
设置中心的分区导航只放三个设置分区。后台六个分区由 `AdminShell` 承载，
文件资料与文档审核保持独立入口。单一入口的理由（以及为何不在设置导航里再放一份）见
[ADR 0011](../docs/adr/0011-self-service-page-separate-from-user-admin.md)。

## 交互与数据边界

检索页默认对所有启用知识库检索，也可以选一个非空集合；每次提交都把这次的选择和后端返回的实际范围快照固化在那条记录上。目录加载失败或选择已失效时页面阻止提交，绝不悄悄扩大范围；后端在请求不带 `scope` 时仍按旧契约缺省到 `news`，那是后端的兼容路径，新页面不依赖它。

检索只走按 Document 分组这一种形态：`document_limit` 决定一次检索返回多少篇不同文档，`matches_per_document` 决定每篇带回几个相关片段，两个参数的下限、上限和默认值是 `api/document-search.ts` 里的契约常量。它们的默认值在设置中心的「检索偏好」分区维护、存在账号上，提交那一刻读到什么值这一轮就用什么值。分组和排序由后端完成，前端按返回顺序渲染，不重排、不聚合、不二次去重。

每次搜索固化成一条“检索记录”，追加成一条向下生长的检索流：记录按提交先后从上往下排，最新的那条在最下面、紧邻底部输入坞并完整展开，旧记录折叠成“检索词 + 命中数”的标题行，可以点开回看。刷新或离开页面即清空，这不是真会话，也不落后端。一次只允许一条在途搜索，提交新搜索会取消上一条。输入坞贴底，一轮进入终态后只清空本次提交的草稿，等待期间新写的内容保留；提交后把视口带到最新一条，用户仍在原操作位置时恢复输入焦点，正在阅读全文或已经转到其他控件时不抢焦点。记录区在长高时只在用户仍贴底的情况下跟随滚动，上翻后由输入坞上方的“回到最新”按钮给出回去的入口（与 Agent 对话页共用 `src/shared/composables/useStickToBottom.ts`）。

每篇文档默认只展示最高分片段，其他相关片段用无框分隔列表展开；score 始终显示原始数值，不换算成概率或百分比。全文由 Vue Query 以 `document_id + content_hash` 为缓存 key 按需加载，每次打开都重新核对当前正文，加载中和失败时隐藏旧缓存，关闭或快速切换时取消旧请求；全文失败不清空检索流。搜索结果里的 hash 与 PostgreSQL 当前 hash 不同时展示版本更新提示，并显示数据库里的最新正文。正文用 Vue 文本插值渲染，Markdown 文件交给共享的 `SafeMarkdown` 安全解析，不使用 `v-html`，外链图片不加载；没有 Source 或 URL 的文件同样能阅读。桌面端用右侧阅读面板，移动端用全屏阅读层，两者都支持 Esc、明确的关闭按钮、焦点约束和关闭后把焦点还给触发按钮。

## Agent 对话页的数据边界

`/agent` 对所有登录账号开放，与检索页同级：路由只要求 `meta.requiresAuth`，真正的安全边界是后端 `/agent/*` 上的 `current_active_user` 依赖和按 `user_id` 判定的会话归属（见 [`docs/adr/0030-agent-open-to-all-accounts.md`](../docs/adr/0030-agent-open-to-all-accounts.md)）。系统提示词不再由前端逐轮发送，请求体里只有提问、会话 id 和范围。

流式接口用 `fetch` 加 `response.body.getReader()`，不用 `EventSource`：后者只能发 GET、不能带请求体，提问就得进 query string，会被网关日志和浏览器历史记下来。超时分连接与空闲两道，常量在 `api/agent-chat.ts`，空闲那一道按后端心跳间隔留了四倍余量；不复用 `client.ts` 里整个请求的总时长上限，一次 Agent 运行可能要几分钟，用它会在模型还在写的时候掐断。调用方提前 `break` 时会 `reader.cancel()` 关掉连接，否则后端那次运行会继续跑、继续计费。取消一轮对话靠两道闸，`AbortController` 之外还有一个自增序号：事件已经拿在手里、`await` 还没恢复的那个窗口里 abort 拦不住任何东西，只有比对序号能阻止一次已取消的运行往界面写字，取消后到达的 `done` 因此也不会写回会话 id。

会话 id 和实际范围由服务端在 `run_started` 给出。新会话默认所有启用知识库，选择通过独立的 PATCH 保存，重新打开继续沿用；运行期间改选只影响下一次，页面保留每次运行的范围快照。切换历史会话和浏览器前进后退以路由参数为准，离开后取消在途的历史加载，迟到的响应不会重新改写地址，只有新建会话取得服务端 id 时才补全当前地址。

视口由 `src/shared/composables/useStickToBottom.ts` 管理：内容长高（流式 token、新轮次、状态行）只在用户仍贴底时跟随，上翻之后由输入坞上方的“回到最新”给出回去的入口，不会被后台到达的内容拽走；打开一个已有会话是显式的“我要看最新的”，由页面在载入完成后直接滚到底。会话列表里的删除走 `ConfirmDialog`（应用自己的确认框，文案点名删的是哪个会话），不再弹浏览器原生 confirm。

临时 token 只供流式预览，`done.answer` 校正最终文字并给出完成状态和引用，缺少 Done 的流不能当成完成。结束后同步最新 checkpoint，压缩后只展示保留的近期问答，同步失败可以重试。工具调用和结果优先按本轮 `tool_call_id` 配对，缺 ID 的旧记录保留按名字配对，不会跨问答配对；缺失的结果明确呈现，工具进一步缩小范围时展示实际范围。

回答用共享的 `SafeMarkdown` 渲染，显式 sanitize、不解析原始 HTML、不加载外链图片；用户提问、工具参数和工具返回继续按文本展示。只有服务端核验过的本次引用能变成阅读入口：点击行内引用或引用列表，在阅读器里对照当时取得的片段和当前原文，原文更新、删除、知识库停用和服务失败分别提示；旧回答保持原样，不把当前正文当成历史版本，也不强行高亮不可靠的位置。

自定义系统提示词在设置中心的「Agent 偏好」分区编辑，保存在账号上，作为新会话的初始提示词：会话建立时由服务端拍一份快照进那个会话，已开始的会话不受影响（`docs/adr/0029-session-scoped-system-prompt.md`），清空即回到服务端默认。账号配了提示词时输入条亮一枚链回设置的徽章，不在对话页编辑。

## 文件管理的数据边界

`/admin/files` 上传 `.txt`、`.md` 后展示候选处理状态与正式版本是否可用；保存成功不等于已经
可检索，首次采用前不能打开普通全文。列表对在途处理定时刷新，失败从“查看与审核”进入统一工作台。
替换选定明确 Document 并携带 `revision` 与 `management_revision`，新候选完成采用前旧版本继续可用。
冲突后保留已选文件，刷新并由用户明确选择新记录再提交。同名新增互不覆盖。删除失败保留待办，
再次确认可继续完整删除；上传超时先刷新核对结果，不自动重放。

## 文档审核的数据边界

`/admin/documents` 以知识库、来源和处理状态筛选文档，`?document=<UUID>` 打开工作台。
已采用版本、最新人工草稿和最新来源分别显示；原件经受权限保护的同域接口读取、按其原始文件名下载。
原始 HTML 只显示文本；预览 Markdown 使用 `SafeMarkdown`，不会执行脚本或加载外链图片。

MD 和 FreshRSS 正文使用 Markdown 编辑，TXT 保持纯文本。保存使旧预览失效，重新预览和采用分别
返回后台回执；页面轮询至终态。结构目录按阅读顺序展示标题与内容块，Chunk 可对照标题路径、实际
向量化文本与 token 预算，并定位对应内容。手机用编辑/对照切换，桌面并排展示。

刷新和轮询不覆盖未保存文字；冲突后由用户决定保留本地编辑或使用服务端草稿。采用确认绑定当时的
修订和预览，目标变化就撤销确认。采用中候选不可编辑，准备失败时正式版本保留。来源更新另列，
换用来源须明确确认；拒绝停止整篇后续使用，完整删除才清除原件和历史。历史及审核结论分别分页。
请求超时只核对结果，不自动重放；离开或刷新页面前提示尚未保存的编辑。

请求集中在 `api/document-review.ts`，工作台状态由 `features/document-review/useDocumentReview.ts`
维护，跨功能的状态显示函数位于 `shared/model/document-processing.ts`，文件管理与审核功能不互相导入。

## 任务管理的数据边界

`/admin/scheduled-jobs` 保留原路由，页面分为周期配置与全部任务执行，均需超级用户权限。类型元数据提供参数范围，业务表单和统计保持显式适配；一次性 Pipeline 也可在这里提交。常用周期选项和原始 cron 共用一个值及后端预览，页面明确标注调度时区。

配置在启用或执行期间可以编辑、停用和删除，已有执行继续使用受理快照。独立详情通过 `/task-runs/{run_id}` 查询，地址中的 `view=executions&run=<UUID>` 可恢复目标，不依赖配置存在或最近列表包含目标。关闭面板和离页不取消后台执行；当前查询编号按账号保存在本标签页 `sessionStorage`。

提交前保存原请求标识、操作和参数。超时保留为待确认，用户执行“确认受理”时发送完全相同的请求；确认回执或明确拒绝后才清除待确认项。存储不可用时页面提示限制，仍可凭执行编号或服务端列表查询。不同的主动操作使用不同标识，不靠参数相同合并。请求与状态分别集中在 `api/tasks.ts` 和 `features/scheduled-jobs/composables/`。

详情区分排队、正常资源等待、执行、自动重试和待核实，提供适用的取消、人工重试及原失败关联；未知类型和未知结果保留可识别信息，部分失败单独说明。策略管理修改之后受理的默认重试与历史保留，并显示修改记录，已受理执行的策略快照不变。

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
账号密码或服务端密钥放进 `VITE_*` 变量。恢复用超级用户的配置属于后端进程，只能写入
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

任务页面的公开 HTTP 与交互验证见 `src/api/tasks.spec.ts`、`src/api/scheduled-jobs.spec.ts`、`src/pages/ScheduledJobsPage.spec.ts` 和 `src/features/scheduled-jobs/tests/`。本地页面工具的任务数据位于 `scripts/task-mocks.mjs`，仅使用内存状态，不证明 PostgreSQL、Redis 或 Worker 已运行。

账号与定时任务的启停开关显示服务端已确认的状态，请求中禁用，失败后仍可重试同一个操作。
后台手机导航关闭时不能被 Tab 选中；打开后约束焦点并锁定背景滚动，支持 Esc 关闭和焦点恢复。

开发中按改动选择测试文件或目录，不逐次运行全套：

```powershell
npm run test:run -- src/features/semantic-search/tests/SearchResultCard.spec.ts
npm test -- src/features/semantic-search/tests
npx vitest related src/features/semantic-search/components/SearchResultCard.vue src/styles/shared-styles.node.spec.ts --run
npx eslint src/features/semantic-search/tests/SearchResultCard.spec.ts --max-warnings 0
npx prettier --check src/features/semantic-search/tests/SearchResultCard.spec.ts
```

`vitest related` 按源文件的 import 关系选择受影响测试，适合改动一个组件或状态逻辑时使用。共享样式检查通过文件读取扫描源码，需要显式带上 `shared-styles.node.spec.ts`；它按规则检查全部当前文件，失败消息会指出具体文件。

**测试在本地跑，CI 不跑测试。** 部署工作流只负责构建与部署（见 [部署工作流](../.github/workflows/deploy.yml)）；推送前在本地按上面的范围规则选测试。普通改动用 `vitest related` 带上共享样式检查，前端依赖、公共配置、HTTP/类型契约或入口变化时跑完整测试。

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

- `src/app`：路由表（`router.ts`，权限 `meta` 的唯一声明处）与 Vue Query 客户端；
- `src/layouts`：`AppShell`（前台侧栏外壳）与 `AdminShell`（后台外壳）；
- `src/api`：Cookie 登录、账号管理、HTTP 客户端、文档搜索/详情、Agent SSE 流、错误归一化
  和生成类型；数量参数与提示词上界的契约常量也在这里（`document-search.ts`、
  `agent-chat.ts`），它们是请求契约的一部分；
- `src/features/auth`：当前用户会话恢复、登录、退出和过期状态；
- `src/features/semantic-search`：文档搜索状态（多轮检索流）、展示模型和
  检索流组件（输入条 / 单条记录 / 结果卡）；
- `src/features/agent-chat`：多轮对话状态、工具轨迹配对、错误文案表和对话组件；
- `src/features/file-documents`：文件上传、列表、替换、索引重试与删除状态；
- `src/features/document-review`：文档审核工作台、原件对照、预览与历史；
- `src/features/knowledge-bases`、`src/features/sources`：知识库配置与来源绑定的目录页状态；
- `src/features/user-admin`：账号目录、建号表单与账号操作；
- `src/features/scheduled-jobs`：周期配置、任务执行、Pipeline 提交与策略面板；
- `src/features/settings`：设置中心（账号安全 / 检索偏好 / Agent 偏好）与账号偏好
  store（读写 `/auth/me/preferences`）；
- `src/shared/ui`：`Base*` 基础控件（含 `BaseSwitch` 开关）、`ConfirmDialog`（全站破坏性
  操作的确认框，挂在 `App.vue` 上一次，由 `requestConfirm()` 驱动）、`ComposerDock` 输入坞、
  `ScrollToBottomButton`（输入坞上方的「回到最新」，检索页与 Agent 页共用）、
  `KnowledgeBaseScopePicker`、`ThemeToggle`，以及答案与 Markdown 文件共用的安全渲染器
  `SafeMarkdown.vue`、检索 / Agent / 文件资料三处共用的全文阅读器 `DocumentReader.vue`；
- `src/shared/composables/useKnowledgeBaseScope.ts`：启用知识库目录与选择有效性；
- `src/shared/composables/useDocumentReader.ts`：全文 Query 与阅读层状态（三处入口共用）；
- `src/shared/composables/useStickToBottom.ts`：贴底跟随与「回到最新」的判定（检索页与 Agent 页共用），
  暴露 `atBottom` / `scrollToBottom()`，由 `ScrollToBottomButton` 呈现；
- `src/shared/composables/confirm.ts`：确认请求的单槽队列（渲染方是 `ConfirmDialog`）。
  feature 里的 composable 借它把「弹个确认框」委托出去并拿回用户的选择，写法与 `window.confirm`
  一样是「等一个布尔值」，但弹的是应用自己的框；
- `src/shared/model`：跨功能共用的纯函数与类型（文档处理状态显示、密码规则、
  打开全文所需的文档身份 `ReadableResult` 与引用快照、固定东八区的时间格式化 `datetime.ts`）；
- `src/pages`：登录、检索、Agent 对话、设置中心与后台控制台（单路由
  `/admin/:section?`，AdminPage 按分区组合账号、知识库、来源、文件与任务管理）的路由级组合，
  不直接执行 `fetch`；
- `src/styles`：设计令牌（`tokens.css`）与 `components/` 下的顶栏、动效、目录三态行、
  状态胶囊、窄屏抽屉遮罩、复选行六个共享样式层；全局 reset/base/components 分层写在
  `src/style.css`。共享层只放「多个组件长得必须一样」的规则，且只写进 `@layer components`
  ——分层规则恒定输给组件未分层的 scoped 样式，要压过某个组件（比如把 BaseIconButton
  藏起来）得在调用方自己的 scoped 块里写。
