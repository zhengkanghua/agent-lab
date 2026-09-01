# Spec：Agent 会话绑定账号 + 会话记录

本文件是 [`thread-ownership-prompt.md`](thread-ownership-prompt.md) 那份需求经过设计评审后的落地规格，
也是跨会话交接的唯一依据。需求提出的五个设计点在第 3 节全部有答案，评审中额外发现的三处缺口
（见 3.2）同样在这里定案。

开工前先读仓库根 [`AGENTS.md`](../AGENTS.md)。本文件不复制代码、SQL 和阈值，那些以实现为准。

## 1. 进度

改一处就勾一处。`[~]` 表示进行中，遇阻在行尾用 `←` 记原因。

**后端**

- [x] `models/agent_thread.py` + `models/__init__.py` 导出
- [x] Alembic 迁移（建表，downgrade 正常 drop）
- [x] `agent/errors.py` 加 `AgentThreadNotFoundError`
- [x] `services/agent_thread_service.py`（含 `derive_thread_title`）
- [x] `api/error_contract.py` 两条新规则 + 两个新 code 字面量
- [x] `schemas/agent_thread.py`
- [x] `agent/replay.py`
- [x] `api/agent_chat.py` 接入归属校验，改 docstring
- [x] `api/agent_threads.py` 三条路由
- [x] `main.py` 挂载新路由
- [x] `cli.py` 加 `prune-orphan-threads`

**前端**

- [x] 重新生成 OpenAPI 类型（离线从 `app.openapi()` 导出再喂给 `openapi-typescript`，
      不必起后端；生成物头部不含来源 URL，与连服务器生成的结果一致）
- [x] `api/agent-threads.ts`
- [x] `model/agent-error.ts` 两条文案
- [x] `composables/useThreadList.ts`
- [x] `composables/useAgentChat.ts` 加 `loadThread`
- [x] `components/ThreadSidebar.vue`、`ThreadListItem.vue`
- [x] `app/router.ts` 加 `/agent/:threadId`
- [x] `pages/AgentChatPage.vue` 接入侧栏与路由参数
- [x] 计划外补的两处：`model/conversation.ts` 加 `turnsFromReplay`（回放 DTO → 界面轮次）；
      `AgentTurnCard.vue` 补「这一轮没有留下回答」——原来 answer 为空且无 error 时那张卡是空白的

**测试与文档**

- [x] 后端测试（清单见第 8 节）：400 passed / 5 skipped，12.65s
- [x] 前端测试（清单见第 8 节）：539 passed / 55 files
- [x] ADR **0009**（归属独立建表）、**0010**（流式路由用短 Session）
      ← 原计划编号 0008/0009，但 0008 已被容器部署那条占用，顺延；源码里的引用已同步改过
- [x] `CONTEXT.md`（会话词条补归属 + 新增「孤儿会话」）、`FEATURE_MAP.md`（新增会话记录一行
      与 prune 命令一行）、`backend/README.md`（migration head 改 `c3f8a1b6e492`、六个子命令、
      升级注意事项）、`backend/docs/architecture.md`（表清单、接口清单，并重写那段
      「拿到 id 就等于拿到会话」——它描述的正是本次修掉的漏洞）
- [x] 全套验证命令跑通（第 9 节）：后端 401 passed / 5 skipped；前端 543 passed、
      `vue-tsc` 与 `eslint` 干净、`vite build` 成功
- [x] 浏览器里真跑一遍（第 9 节）：空态、新建、续聊（历史确实接上）、刷新按 URL 恢复、
      翻页（临时造 25 行验完即删）、删除（当前会话删掉后回到 `/agent`）、失败态（真撞上
      上游限流）、越权 404。三条路由用别人的 id 全返回同一个 404 body；`/agent/chat` 那次
      的 `content-type` 是 `application/json` 而不是 `text/event-stream`，证明拒绝发生在
      流开始之前
- [x] 真库归属集成测试：`RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST=1`，2 passed
- [x] 计划外修的一处：`error` 事件原来不带 `thread_id`，见第 12 节

**须老板执行或授权**（第 10 节）

- [x] `alembic upgrade head` ← 老板授权后由助手执行，`alembic check` 报无差异
- [ ] `prune-orphan-threads` 清理现存无主 thread ← 预演报 19 条，等老板定
- [ ] git add/commit/push

## 2. 要解决的问题

`api/agent_chat.py` 原本是 `thread_id = chat_request.thread_id or uuid4()`：前端传什么就用什么，
而 checkpointer 只按 id 取历史、不校验归属。同文件的 docstring 当时写的是「服务端生成，所以别人猜不到」，
代码却接受前端传入——文档和代码不一致。

今天危害有限（路由挂 `current_superuser`，且 UUID4 猜不到），但那段注释明确写着放宽权限是以后的事。
放宽到 `current_active_user` 的那天，这就是一个账号读另一个账号会话的真漏洞。

要做两件事：thread 与账号绑定并校验归属；用户能列出自己的会话、点进去接着聊。

## 3. 设计决定

### 3.1 需求提出的五个设计点

**归属与列表的存储形态**：一张业务表 `agent_threads`，归属和列表信息都在里面；列表不查 checkpointer 的四张表。

不查那四张表的理由是 ADR 0004 的立场——结构的定义权在 `langgraph-checkpoint-postgres` 手里，我们不复制、
不依赖。分页还需要索引，而我们不能给别人管的表加索引：LangGraph 升级时会跑自己的迁移，我们加的东西没人保证。
详见 ADR 0009。

**会话标题**：截取首条提问，服务端在插入时截断到 60 字符，只写一次，之后不改（含首轮运行失败的情况）。

成本为零，且离线测试能钉住它（让模型生成就得打桩模型）。更重要的是它不依赖 `LLM_MODEL` 配置——那项刚踩过坑，
不该再多一个依赖它才正常的功能。**本次不做重命名**，纯粹是范围控制。

不存完整提问（最长 4000 字符）的理由：那些字在 checkpointer 里已经有一份，存第二份就是双真源；
列表一页 20 行还要把它们全发出去。后端只截断不加省略号，省略号交给前端 CSS——后端加的话宽屏明明放得下也会带个多余的点。

**现存无主 thread**：清掉，但**不在 Alembic 迁移里删**。迁移只建表；清理做成 CLI 命令 `prune-orphan-threads`。

不写进迁移的理由和 ADR 0004 拒绝过的那件事同源：迁移文件按约定是「写下来就不再变」的历史记录，
里面调第三方库的删除逻辑，回滚到旧迁移时行为已经不是当初那个了。这个命令长期都有用——将来业务行删了而
checkpointer 残留（见 3.1 删除会话），同一个命令就能收拾。

不归给保底管理员：那些是联调垃圾，没有标题、没有真实时间，归给管理员的结果是老板第一次打开列表就看到一堆
看不懂的东西，还得手工删一遍。

**删除会话**：做。顺序是**先 `adelete_thread` 清 checkpointer 历史，成功后再删业务行**。

不用手写 SQL：`langgraph-checkpoint-postgres` 3.1.2 的 `AsyncPostgresSaver` 有公开的 `adelete_thread`。

顺序是有意的。两者跨两个连接池（SQLAlchemy 与原生 psycopg，见 ADR 0004），不可能同一个事务，所以必须选
「中途失败留下什么」。这个顺序留下的是「历史已删、业务行还在」，用户看到一个点进去是空的会话，可以再点一次删除；
反过来留下的是「业务行已删、历史还在」，那是个查不到也删不掉的孤儿，只能等 prune 命令来收。前者可自愈，后者不能。

### 3.2 评审补出的三处缺口

**（一）SSE 路由不能挂请求级 Session。** 这是本次最容易踩、最难排查的坑。

`get_db_session` 是 async generator 依赖，FastAPI 要等响应彻底结束才执行 `yield` 之后的归还动作。
对 `StreamingResponse` 来说「响应结束」是流关闭之后，而一次对话可能三分钟。照常写 `Depends(get_db_session)`
的后果是一条业务连接被占满全程，几个并发就能把池占空。

故障表现和原因不在一处：老板看到的是检索页报「数据库不可用」，而原因是有人在跟 Agent 聊天。
日志里也不会有「连接池满了」，只有一堆超时。

做法：不挂 `Depends(get_db_session)`，由 `AgentThreadService` 持有 **session 工厂**，每个方法内部自己开、
提交、关。校验和插入几十毫秒，做完连接立刻归还，然后才开始流。详见 ADR 0010。

工厂必须是构造参数而不是模块级直接引用（`db/session.py` 那个 `async_session_factory` 是 import 时就绑好真实
`DATABASE_URL` 的全局对象）。直接引用的话测试没有任何注入点，离线测试会真去连 PostgreSQL 等满超时——
`app_helpers.py` 开头记的那个坑（`agent_runtime_factory` 在 5 个文件里被集体漏掉、白等 30 秒、失败还被
lifespan 咽掉）会重新开一个同类的口子。

**（二）「点进去接着聊」需要历史回放。** 需求只写了列表，但列表能点进去之后界面上得有之前的问答，
否则用户看到空白页却在续聊一个模型记得的会话。

数据来源是 `graph.aget_state(config)`，不自己另存一份消息副本。副本注定分叉：`SummarizationMiddleware`
压缩之后（阈值 40 条、保留 20 条），副本里是完整二十轮，模型实际看到的是「摘要 + 最近 20 条」。
用户指着屏幕上写着的东西问「你刚才说的那个」，模型说不知道——两边各自都是对的，极难排查。

用 `aget_state` 而不是 `aget_state_history`：后者返回所有 checkpoint（实测两轮对话 21 行），只要最新状态。

**（三）删除不用手写 SQL。** 见 3.1「删除会话」。

### 3.3 归属校验失败的对外形状

404 + `agent_thread_not_found`，「不存在」和「不属于你」合并成同一个回答。

403 会给猜 id 的人一个预言机：403 确认「这个 id 存在」，404 确认「不存在」，一比就能枚举出哪些 thread 是别人的。
合并成 404 不泄露存在性，对合法用户也准确。

**校验必须发生在流开始之前**，这样失败还带得动 HTTP 状态码，走前端 `agent-chat.ts` 的 `!response.ok` 分支，
不必塞进 SSE 的 error 事件（流一旦开始就改不了状态码）。

### 3.4 会话行写入时机

只在流开始前写一次并提交：新建就 insert，续聊就更新 `last_active_at`。**流结束后不再写。**

不等运行成功再写：checkpointer 在第一次模型调用时就落历史了，等成功会留下「checkpointer 有历史、业务表无归属」
的孤儿——正是这次要消灭的东西。更硬的理由是那个 thread_id 已存在于 checkpointer，将来若被另一个账号当成新会话
开出来，又是一次跨账号读历史。

代价是失败的运行会在列表里留下「有提问、没答案」的一条。这反而诚实——前端本来就要显示失败轮。

不在流结束时补第二次写：那个位置是生成器的 `finally`，客户端可能已经断开，失败了没人能报给用户，
成功了也只是把排序键从「最后一次提问」改成「最后一次回答完成」，差几分钟，用户分辨不出来。
多一次写、多一个失败面，换不到可见收益。

### 3.5 分页

offset 分页（`limit`/`offset` + 返回总数），每页 20 条。

已知取舍：边翻页边新建会话时，列表整体前移会让一条重复出现、一条被挤走。一个账号的会话是几十到几百，
offset 的性能问题在这个规模不存在，而返回总数能让界面显示「共 N 个」，游标分页做不到（除非再查一次 count）。
要触发那个错位得在翻页的同时另开标签页聊天，不是常见操作。

这是项目第一个分页接口（`/admin/users` 是全量返回），定下来会成为惯例。

`GET /agent/threads/{id}/messages` **不分页**：历史被压缩中间件封在 40 条以内。

### 3.6 权限门

`/agent/threads` 与 `/agent/chat` 同门，保持 `current_superuser`。需求明确说放宽是以后的事，
而这次做的正是「为放宽做准备」。前端 `router.ts` 的 `requiresSuperuser` 不用动。

### 3.7 其他

- 前端信息架构：`AgentChatPage` 内左侧可折叠栏，**URL 带 threadId**（`/agent` 与 `/agent/:threadId`）。
  带 id 才能让刷新、收藏、后退符合预期——聊天页恰恰最容易被刷新（等回答的时候人会手贱）。
  AppShell 不改：它没有侧栏插槽，正文整块本来就归页面自己放。
- 错误码进现有的 `AGENT_CHAT_ERROR_RULES`，不新开表。归属错误在 `/agent/chat` 上就会发生，不是只有会话路由才用。
- 不做：会话重命名；需求「明确不在范围内」那七条。
- 已知既有限制，本次不引入也不修：同一个会话被两个标签页同时提问会交错，这是 checkpointer 现有行为。

## 4. 数据模型

`agent_threads`，ORM 在 `models/agent_thread.py`，必须在 `models/__init__.py` 导出（Alembic 靠它发现表）。

| 列 | 类型 | 说明 |
|---|---|---|
| `thread_id` | `Uuid` 主键 | 与 checkpointer 的 thread_id 同值，服务端生成 |
| `user_id` | `Uuid` 外键 → `users.id`，`ondelete=CASCADE` | 归属 |
| `title` | `String(60)` NOT NULL | 首条提问截断，只写一次 |
| `created_at` | `DateTime(timezone=True)` NOT NULL | 新建时间 |
| `last_active_at` | `DateTime(timezone=True)` NOT NULL | 列表排序键 |

索引 `ix_agent_threads_user_id_last_active_at` 建在 `(user_id, last_active_at DESC)`：列表查询固定是
「我的 + 按活跃倒序」，单列索引会让 PostgreSQL 排完再筛。

CASCADE 照 `models/user.py` 里 `access_tokens` 的既有做法。现在没有删账号的路由，这条暂时走不到，
但方向定了：删账号连带删归属记录，checkpointer 里的历史变孤儿，交给 prune 命令。

迁移文件名沿用现有风格（`<hash>_中文描述_english_slug.py`）。downgrade 正常 `drop_table`，
并在文件里写注释交代：回滚之后归属记录没了、checkpointer 里的历史还在，所有会话变成孤儿，
用 `prune-orphan-threads` 收拾。这不是 bug，是回滚的正常代价，但不写下来的话下一个人会以为回滚无损。

## 5. 后端实现

### 5.1 `services/agent_thread_service.py`（新）

持有 session 工厂而不是 session（理由见 3.2 之一）。每个方法内部自己开、提交、关。

- `ensure_thread(user_id, thread_id, first_message) -> UUID`
  `thread_id` 为 `None` → insert 新行，标题取 `derive_thread_title(first_message)`；
  非 `None` → 校验归属，通过则更新 `last_active_at`，不通过抛 `AgentThreadNotFoundError`。一个事务。
- `list_threads(user_id, limit, offset) -> (rows, total)`
- `get_owned_thread(user_id, thread_id)`：回放和删除的前置校验
- `delete_thread_record(user_id, thread_id)`
- `derive_thread_title(message)`：模块级纯函数，截 60 字符，方便单测

### 5.2 错误契约

`agent/errors.py` 加 `AgentThreadNotFoundError`（叶子模块，不引依赖）。

`api/error_contract.py`：`AgentChatErrorCode` 加两个字面量，`AGENT_CHAT_ERROR_RULES` 加两条规则，
都排在末尾 `Exception` 兜底之前：

| 异常 | 状态码 | code | retryable |
|---|---|---|---|
| `AgentThreadNotFoundError` | 404 | `agent_thread_not_found` | False |
| `SQLAlchemyError` | 503 | `agent_thread_database_unavailable` | True |

`SQLAlchemyError` 那条是必须的：`/agent/chat` 现在开始碰业务库，而表里现有的数据库规则挂的是
`PsycopgOperationalError`（checkpointer 那条独立 psycopg 池），管不到 SQLAlchemy 这边。
不加的话业务库故障会落进 `agent_internal_error` 兜底，前端只能给一句「未分类的服务错误」。

`agent_thread_database_unavailable` 的 detail 不能和 `user_admin_database_unavailable` 相同，
否则撞上「同一个 code 在所有表里必须对应同一句 detail」那条测试约束——两个不同 code 用同一句话也会让那条测试
失去意义。

### 5.3 `api/agent_chat.py` 改动

- 函数签名加 `Depends(current_superuser)` 拿 `UserRecord`（现在权限门挂在 `main.py` 的 `include_router` 上，
  函数体里拿不到 user 对象，得再声明一次）和 `Depends(get_agent_thread_service)`
- 删掉 `thread_id = chat_request.thread_id or uuid4()`，改成流开始前 `await service.ensure_thread(...)`
- 改那段 docstring：现在的理由不再是「服务端生成所以猜不到」，而是「校验归属」
- `schemas/agent_chat.py` 里 `AgentChatRequest.thread_id` 的字段描述同样要改，它现在也写着旧理由

### 5.4 `api/agent_threads.py`（新）

前缀 `/agent/threads`，挂在 `main.py`，`dependencies=[Depends(current_superuser)]`。

| 路由 | 行为 |
|---|---|
| `GET ""` | 返回 `{items, total}`；`limit` 默认 20 上限 100，`offset` 默认 0 |
| `GET "/{thread_id}/messages"` | 先校验归属，再 `runtime.graph.aget_state(config)` 回放 |
| `DELETE "/{thread_id}"` | 先校验归属，再 `adelete_thread`，成功后删业务行（顺序见 3.1） |

`schemas/agent_thread.py`（新）：列表项、列表响应、回放响应、回放 turn 四个模型。

### 5.5 `agent/replay.py`（新）

把 checkpointer 的消息列表翻成前端的 turn 结构（`question` / `answer` / `traces`）。本次最需要小心的一块。

- 按 HumanMessage 切分 turn，AIMessage 的文本内容拼成 `answer`
- 工具轨迹用 `tool_call_id` **精确配对**。这里比 SSE 那条路更准：前端 `conversation.ts` 只能按「同名且还没结果的
  最早那条」FIFO 近似配对，因为 `tool_result` 事件不带 id；回放时消息里有 `tool_call_id`，直接配。
- **摘要那条要单独认出来。** 实测 `SummarizationMiddleware`（langchain 1.3.15）的压缩动作是
  `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 清空、再重建，摘要被包成一条 **HumanMessage**，
  带 `additional_kwargs={"lc_source": "summarization"}`，内容前面还有一句英文
  `Here is a summary of the conversation to date:`。
  照直渲染的话用户会看到一个自己从没问过的「提问」。
  判定按 `additional_kwargs.get("lc_source") == "summarization"`，它不是 turn 起点，
  内容原样进响应的 `summary` 字段。
  **不剥那句英文前缀**：剥前缀要匹配 langchain 的字面量，它一改就静默出错；原样显示最多多一行英文。
- 响应带 `summarized: bool`，前端据此显示「较早的对话已被压缩成摘要」
- 回放出的 turn 一律 `status: 'done'`；`answer` 为空的（首轮失败留下的）前端显示一句灰字说明，不伪造 error

### 5.6 `cli.py` 加 `prune-orphan-threads`

`alist(None)` 枚举全库 checkpoint（每个 `CheckpointTuple` 的 config 里带 thread_id），减去 `agent_threads`
里已有的，剩下的逐个 `adelete_thread`。

用 `alist` 而不是 `SELECT DISTINCT thread_id FROM checkpoints`：后者依赖那四张表的结构，与 ADR 0004 冲突。
代价是它会遍历所有行（实测两轮对话 21 行），对一个运维命令可以接受。

**默认 dry-run 只打印，加 `--yes` 才真删**——它是删数据的命令，默认不该真删。

## 6. 前端实现

- `api/agent-threads.ts`（新）：三个函数，形状检查照 `agent-chat.ts` 的做法，不合契约抛 `response_invalid`
- `composables/useThreadList.ts`（新）：分页、加载态、空态、失败态、删除
- `composables/useAgentChat.ts`：加 `loadThread(threadId)`，把回放的 turn 灌进 `turns` 并设好 `threadId`。
  `startNewConversation` 现有逻辑不变——它那段「必须同时丢掉 threadId」的理由依然成立
- `components/ThreadSidebar.vue`、`ThreadListItem.vue`（新）：复用 `shared/ui/` 的
  `BaseButton` / `BaseIconButton` / `BaseSpinner` / `BaseDisclosure`，不新造基础件
- `model/agent-error.ts`：`agent_thread_not_found` → 「会话不存在或已被删除」（按 3.3，不区分「不属于你」）；
  `agent_thread_database_unavailable` 复用现有 `UNAVAILABLE_COPY`
- `app/router.ts`：加 `/agent/:threadId`，meta 与现有一致（`requiresAuth` + `requiresSuperuser`）
- `pages/AgentChatPage.vue`：侧栏进现有骨架；watch 路由参数去 `loadThread`；窄屏侧栏收成抽屉
- OpenAPI 类型重新生成（命令在 `frontend/README.md`）

## 7. 文档

- `docs/adr/0009-agent-thread-ownership-in-own-table.md`：归属与列表放自己的表、与 checkpointer 解耦
- `docs/adr/0010-sse-routes-use-short-lived-db-sessions.md`：SSE 路由不挂请求级 Session。
  这条尤其需要——那段代码看起来就像「忘了用 Depends」，下一个人极可能顺手改回去，改完之后症状出现在检索页，
  没人会怀疑到 Agent。ADR 是唯一能拦住这件事的东西。
- `CONTEXT.md`：改「会话（thread）」词条，并进归属这层含义，不加新词条（一句定义，不提表名列名）
- `docs/FEATURE_MAP.md`：加三条路由和 prune 命令
- `backend/README.md`：部署一节提 prune 命令
- 不新增 `docs/flows/` 条目：这次没有跨模块时序，够不上那个门槛

404 而非 403（3.3）和删除顺序（3.1）不立 ADR：前者是通行安全做法，一行注释说清「403 会泄露 id 存在性」就够；
后者的理由写在调用处更有用（「这个顺序留下的孤儿可自愈，反过来不能」）。

## 8. 测试

后端全部离线，`InMemorySaver` + `FakeSessionFactory`（`tests/test_agent_tools.py` 已有），
沿用 `tests/app_helpers.py` 的「漏写=安全」原则。**离线测试不许连真实 PostgreSQL**：
之前全套要 20 分钟，原因就是一批测试各自等 30 秒连接超时，改掉后降到 14 秒（提交 `ac9572c`）。

- 别人的 thread_id → 404 `agent_thread_not_found`，且**模型一次都没被调用**（最关键的一条，
  证明校验发生在流开始之前）
- 不传 thread_id → 新建并落库，`done` 事件回的 id 与库里一致
- 列表只返回自己的、分页正确、总数正确
- 回放：普通历史、压缩过的历史、`answer` 为空的历史
- **摘要识别**：拿真实 `SummarizationMiddleware` 跑一次压缩，断言 `replay` 能认出那条。
  langchain 改了内部形态时这个测试会响，而不是让界面上冒出一句英文
- 删除：正常路径；`adelete_thread` 失败时业务行**仍在**（可自愈那一侧）
- `derive_thread_title` 的纯函数单测
- prune 命令的 dry-run 与真删
- 契约测试自动覆盖新 code 的 detail 一致性

前端 `vitest`：`useThreadList`、`loadThread`、两个新组件、`agent-threads.ts` 的契约校验、
`agent-error.ts` 新文案。

## 9. 验证

后端 `pytest`；前端 `vitest run`、`vue-tsc --noEmit`、`eslint .`、`vite build`。

然后前后端都起，浏览器里走一遍：新建会话、切走再切回、刷新页面、删一个、翻页、空态、失败态。
**不要只跑单测就交付。**

Windows 本地运行的既有坑（都踩过）：

- psycopg 异步必须用 SelectorEventLoop：
  `python -m uvicorn agent_lab.main:app --loop agent_lab.runtime:selector_loop_factory`
- 脚本里用 `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)`
- Vite 只绑 IPv6 回环：用 `http://localhost:<port>/`，`127.0.0.1` 打不开
- 指向非默认后端用 `BACKEND_PROXY_TARGET=http://127.0.0.1:<port>`
- 本地 HTTP 调试才用 `AUTH_COOKIE_SECURE=false`，生产 HTTPS 必须保持 `true`；用启动时环境变量覆盖，别改 `.env`
- 杀进程**只按 PID**，不要按镜像名——按名字杀过一次全机 Python，含本项目后端
- `LLM_MODEL` 已钉到具体模型。再看到奇怪的工具调用或空回答，**先查这项配置**，别从业务代码找起

日志纪律：只记异常类型，**绝不记 `str(exc)`**（连接串里带数据库密码）；不记用户输入，
照现有尺度（只记 `thread_id` 和「有没有自定义提示词」）。

## 10. 须老板执行或授权

按 `AGENTS.md`，这三类动作助手不自己做：

1. **`alembic upgrade head`**（建 `agent_threads` 表）。助手把命令写好交给老板，或老板授权后助手执行。
2. **`prune-orphan-threads` 清理现存无主 thread**。建议顺序：先跑迁移建表，再 dry-run 看一眼数量，
   确认了再加 `--yes`。这是删数据。
3. **git `add`/`commit`/`push`**。

## 11. 风险

- `alist(None)` 遍历全部 checkpoint 行，库大了会慢。只影响运维命令，不影响请求路径；真慢了改成带 `limit` 分批。
- 回放依赖 `additional_kwargs.lc_source` 这个 langchain 内部约定。有测试钉住，升级时会明确失败。
- offset 分页在「边翻页边新开会话」时会重一条漏一条（3.5 的已知取舍）。
- checkpoint 四张表仍无保留/清理策略（ADR 0004 记的 v1 现状），会话历史无界增长。本次不做，
  实测数据：一次两轮对话产生 21 行 `checkpoints` + 24 行 `checkpoint_writes`。

## 12. 联调中发现并修掉的一处：`error` 事件不带 `thread_id`

浏览器验证第一次提问就撞上上游限流，于是看见了这个：侧栏一直显示「还没有会话」，而库里
归属行已经写好了。

原因是 `AgentErrorEvent` 只有 `code`/`detail`/`retryable`，而 `thread_id` 只在 `done` 事件里
给。失败那一轮不发 `done`，所以前端拿不到 id。

真正的代价不是「侧栏要等刷新」，而是**点「重发这一轮」会另开一个会话**：请求里没有
`thread_id`，服务端按 3.4 又插一行。同一次提问在列表里占两条，都是「有提问、没答案」，
重试几次就多几条。上游限流不罕见，所以这不是边角情况。

改法：`AgentErrorEvent` 加 `thread_id`，**必填**——它只在 `agent/streaming.py` 一处构造，
那里 `thread_id` 是入参；流开始之前的失败走 HTTP 状态码，不走这个事件。所以不存在「有时
没有」的情况，做成可选反而会让前端多一条永远走不到的分支。前端 `agent-chat.ts` 的形状
校验同步要求它，`useAgentChat.ts` 在 `error` 分支里认下这个 id。

钉住它的测试：后端 `test_the_error_event_also_carries_the_thread_id`；前端
`失败那一轮就认下 thread_id，重发落在同一个会话里`（断言第二次请求带上了 id，而不只是断言
`threadId` 有值）与 `首轮失败也把会话并进侧栏，不用等刷新页面`。

顺带修了两条因此失效的旧断言：`error 缺 retryable` 那条拒绝用例原来不带 `thread_id`，
新校验下它会因为缺 `thread_id` 被拒，名字说的那件事就没被验到。
