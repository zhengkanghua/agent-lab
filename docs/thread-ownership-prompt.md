# 需求：Agent 会话绑定账号 + 会话记录（前后端）

## 开工前

先读仓库根目录的 `AGENTS.md` 并全程遵循它。全程简体中文回复。

## 要解决的问题

`backend/src/agent_lab/api/agent_chat.py:212` 是：

```python
thread_id = chat_request.thread_id or uuid4()
```

前端传了 `thread_id` 就直接拿去取历史，而 LangGraph 的 checkpointer **只按 id 找会话、不校验归属**。
同一个文件 192–194 行的 docstring 已经把风险写明白了（"任何人猜到 id 就能读到别人的会话"），
但代码并没有挡住它——docstring 说的是"服务端生成"，代码却接受前端传入。

今天危害有限：该路由挂了 `Depends(current_superuser)`（见 `main.py:426-429`），
且 `thread_id` 是 UUID4 猜不到。**但那段注释明确写着"放宽是以后的事"**——
放宽到 `current_active_user` 的那天，这就变成一个账号读另一个账号会话的真漏洞。

## 要做的事

1. **归属绑定**：`thread_id` 与账号绑定；取历史前校验归属，不属于当前账号一律拒绝。
2. **会话记录**：用户能列出自己的历史会话、点进去接着聊。前后端都做。

## 必须知道的既有约束（都是踩过的坑）

**数据库**
- checkpointer 的四张 `checkpoint*` 表**不在 Alembic 管理范围内**，由 `cli.py init-checkpointer`
  显式创建（见 `docs/adr/0004-*`）。新增的归属表/会话表**应该**进 Alembic，别跟着跑到外面去。
- 实测数据量：**一次两轮对话产生 21 行 `checkpoints` + 24 行 `checkpoint_writes`**。
  所以会话列表必须分页，不要 `select *`；也顺带说明为什么保留策略值得单独立项。
- 库里**现在就有无主的历史 thread**（之前联调产生的）。迁移必须明确决定怎么处理：
  归给保底管理员，还是直接清掉。别让它变成一堆查不到、删不掉的孤儿数据。
- 数据库升级需要老板同意后才能执行，不要自己跑 upgrade。

**测试**
- 离线测试**不许连真实 PostgreSQL**。之前全套要 20 分钟，原因就是一批测试各自等
  30 秒连接超时，改掉后降到 14 秒（提交 `ac9572c`）。新测试沿用现有做法：
  `dependency_overrides` + `InMemorySaver`，参考 `tests/test_agent_chat_api.py` 和 `tests/app_helpers.py`。
- 真连库的集成测试是环境变量门控的，参考 `tests/test_auth_environment_integration.py`。

**错误契约**
- `backend/src/agent_lab/api/error_contract.py` 是**有序规则表**，子类规则必须排在基类前面，
  否则被基类先匹配掉。新增失败类型要同时补：后端规则一条 + 前端文案一条
  （`frontend/src/features/agent-chat/model/agent-error.ts`），否则前端会拿到兜底文案。
- 「不属于你的会话」应该是一个明确的 code，而不是落进 `agent_internal_error` 兜底。

**日志与密钥**
- 只记异常类型，**绝不记 `str(exc)`**——连接串里带数据库密码。
- 不记用户输入。现有代码只记 `thread_id` 和"有没有自定义提示词"，照这个尺度来。
- 不要把真实密钥写进源码、测试、README 或 `.env.example`。

**Windows 本地运行**
- psycopg 异步在 Windows 上必须用 SelectorEventLoop：
  `python -m uvicorn agent_lab.main:app --loop agent_lab.runtime:selector_loop_factory`
- 脚本里用 `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)`。
- Vite 只绑 IPv6 回环：用 `http://localhost:<port>/`，`127.0.0.1` 打不开。
- 指向非默认后端用 `BACKEND_PROXY_TARGET=http://127.0.0.1:<port>`。
- 本地 HTTP 调试才用 `AUTH_COOKIE_SECURE=false`，生产 HTTPS 必须保持 `true`；
  用启动时环境变量覆盖，别改 `.env`。
- 杀进程**只按 PID**，不要按镜像名——按名字杀过一次全机 Python，含本项目后端。

**前端结构（刚重构完，照着来）**
- 结构是 `features/<name>/{components,composables,model,tests}`，页面只做编排。
- `shared/ui/` 已有 BaseButton / BaseField / BaseIconButton / BaseSpinner /
  BaseDisclosure / BasePopover，**复用它们**，别再写一套。
- 路径别名 `@/`，tsconfig 与 vite 两处必须一致。
- 前端只经 HTTP/OpenAPI 访问后端，不直连 PostgreSQL/Qdrant/Ollama。
  任何 `VITE_*` 都会进公开构建产物，不放密钥。

**模型配置**
- `LLM_MODEL` 之前是 `auto`，导致中转站每次调用路由到不同上游，其中有的不认我们的工具
  schema（模型会去调根本不存在的 `Bash` 工具，然后说"我没有 search_news"）。现已钉到具体模型。
  如果又看到奇怪的工具调用或空回答，**先查这项配置**，别从业务代码找起。

## 需要你决定并说明理由的设计点

1. 归属关系放哪：单独一张映射表，还是一张完整的会话表（带标题、时间、最后一条消息）。
2. 会话列表的数据来源：直接读 checkpointer 的表，还是维护一张自己的会话表。
   前者省一张表但绑死 LangGraph 内部结构，后者多一张表但解耦——权衡讲清楚。
3. 会话标题怎么来：截取首条提问，还是让模型生成。（倾向前者，成本为零。）
4. 已有无主 thread 的迁移处理。
5. 删除会话要不要做；要做的话 checkpointer 那四张表的对应行怎么一起清掉。

按 ADR 惯例，值得记录的决策写进 `docs/adr/`（下一个编号，现有到 0007）。

## 验收

- 后端：不属于当前账号的 `thread_id` 被拒，有明确的 error code 与前端文案。
- 后端：会话列表分页，只返回当前账号的会话。
- 前端：能列出自己的历史会话、点进去接着聊，空态与失败态都有交代。
- 测试：新增行为都有测试覆盖，且**离线测试不连真实数据库**。
- 全绿：后端 `pytest`；前端 `vitest run`、`vue-tsc --noEmit`、`eslint .`、`vite build`。
- 真正跑起来验一遍（前后端都起，浏览器里点一遍），不要只跑单测就交付。

## 明确不在本次范围内

这些是同一次评审里列出的其他缺口，**本次不要顺手做**，免得范围失控：

1. 可观测性：不记录实际服务的模型名、模型调不存在的工具时无日志、运行结束无结束日志。
2. 空正文不触发备用模型（`ModelFallbackMiddleware` 按异常降级，而"200 + 空正文"不是异常）。
3. 空回答的前端表现（只显示工具轨迹、没有答案也没有解释）。
4. checkpoint 四张表没有保留/清理策略（`cli.py` 只有 `init-checkpointer`）。
5. 提示词注入防御没有测试（`prompts.py` 第 34 行那条防御没有任何测试钉住）。
6. `LLM_MODEL` 没有启动校验（`auto` 这种非模型名能顺利通过启动检查）。
7. `/agent/chat` 没有速率限制。

## 交付方式

git 的 `add`/`commit`/`push` 必须经老板同意，或把命令交给老板执行。不要自己推。
