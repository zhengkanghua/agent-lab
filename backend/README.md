# Agent Lab 后端

本服务把 FreshRSS 新闻同步成 PostgreSQL 业务事实，用 LangChain 切分成 Chunk、经
Ollama ``bge-m3:567m`` 生成 Embedding 写入 Qdrant，并对外提供受登录保护的**只读**语义
检索接口。写入链路是显式手动入口（CLI 与一个同步 HTTP 接口），另有定时任务调度器按
cron 自动触发同一套执行器（进程内或独立进程两种运行形态，见下文）。

在检索之上还有一条 Agent 对话链路（``POST /agent/chat``，SSE）：一个 LangGraph 工具调用
Agent 把上面的检索能力当工具用，由生成式 LLM 组织答案。它对业务数据同样只读，唯一的写入
是四张 ``checkpoint*`` 会话历史表（见
[ADR 0003 agent-v1-is-read-only](../docs/adr/0003-agent-v1-is-read-only.md)）。**没配 ``LLM_API_KEY``
时只有 ``/agent/*`` 返回 503，检索接口照常工作**，所以只想用检索可以完全不管 LLM 配置。

本文只讲怎么装、怎么跑、怎么调、怎么测。另外两份：

| 想知道什么 | 看哪里 |
| --- | --- |
| 内部怎么实现、对外契约细节、错误码 | [`docs/architecture.md`](docs/architecture.md) |
| 某处为什么是这样、当时放弃了什么 | 平台根 [`docs/adr/`](../docs/adr/) |
| 已验证的 RSS 地址与 FreshRSS selector | [`docs/rss_sources.md`](docs/rss_sources.md) |

对外接口清单见 [`docs/architecture.md`](docs/architecture.md) 的「对外 HTTP 接口」，
或启动后访问 ``/docs``。

## 外部依赖

服务自身不可独立运行，需要四个外部依赖，用 Agent 对话时还要第五个：

```text
PostgreSQL   业务事实、账号与登录 Token。独立 Database news_vector_lc。
             必须先执行 alembic upgrade head（当前 head 用 ``alembic heads`` 查看）。
             Agent 会话的归属与列表元信息在 agent_threads 表（由 Alembic 管）；
             会话历史内容在 checkpointer 自己的四张表，不由 Alembic 管（见下面「Alembic」）。
FreshRSS     唯一的新闻来源。动态网页回源和站点 CSS selector 由它负责，
             Python Pipeline 里不能加站点判断。
Ollama       bge-m3:567m，1024 维。query 与 document 使用同一模型，
             换模型等于换索引空间（必须提升 schema_version 并重建）。
Qdrant       Point 存储。current Alias 必须由部署预先准备，搜索不会创建它。
生成式 LLM   仅 /agent/* 需要。OpenAI 兼容中转站或 Ollama，二选一由 LLM_PROVIDER 决定。
             和上面的 Ollama Embedding 是两件事：Embedding 产出向量，这个产出文字，
             即使都指向同一台 Ollama 也是两套配置。不配则只有 /agent/* 返回 503。
```

Python 版本固定 ``>=3.12,<3.13``。关键依赖当前解析版本：fastapi 0.141.1、
fastapi-users 15.0.5、langchain 1.3.15、langchain-ollama 1.1.0、qdrant-client 1.19.0。
Agent 链路新增：langchain-core 1.5.4、langchain-openai 1.5.1、langgraph 1.2.11、
langgraph-checkpoint-postgres 3.1.2、langsmith 0.10.18。这里的版本号都是 ``uv.lock`` 当前
解析结果，不是上界：装上的每个包对 ``langchain-core`` 都只要求 ``<2.0.0``，所以升级前先跑
``uv lock --upgrade-package`` 看解析，不要照抄这些数字当约束。会话记忆直接用 ``psycopg``
3.3.4 连 PostgreSQL，与 SQLAlchemy 的业务连接池是两套独立连接。

## 启动前置条件

```text
1. alembic upgrade head 已完成          （启动不执行 migration；agent_threads 表由它建）
2. Qdrant current Alias 已存在          （搜索不会 ensure_ready）
3. .env 配置合法                         （启动即读，非法配置直接失败）
4. agent-lab init-checkpointer 已完成    （仅用 /agent/* 时需要；启动不建表）
```

第 1 步不到位时 ``/agent/*`` 会返回 503（``agent_thread_database_unavailable``）而不是崩溃：
归属记录读不出来就不让对话开始，避免在没有归属的情况下写下一段谁都管不了的历史。

应用启动**只**访问 PostgreSQL（同步环境托管管理员；``SCHEDULER_ENABLED=true`` 时调度器还会
读一次定时任务清单），不探测 FreshRSS、Ollama 或 Qdrant，也不创建 Collection 或 Alias。
真正的 Embedding 与 current Alias query 只在收到请求时执行；新闻同步与索引只在手动 CLI、
``POST /pipeline/run-once`` 或调度器到点触发时发生（调度器与手动入口共用同一套写 Runtime
生命周期，取舍见 ``docs/adr/0014-in-process-apscheduler-with-db-as-source-of-truth.md``）。

**定时任务调度器有两种运行形态**（取舍见 [ADR
0017](../docs/adr/0017-scheduler-runs-in-a-dedicated-process.md)）：

- 裸进程部署（本地开发）：``SCHEDULER_ENABLED=true`` 时调度器在 uvicorn 进程内启动。此形态
  必须保持单 uvicorn worker、单实例，否则同一任务会被重复调度。
- 生产容器部署：调度跑在同镜像的独立 ``scheduler`` 容器（``python -m agent_lab.scheduler_main``），
  backend 容器由 compose 强制 ``SCHEDULER_ENABLED="false"``；``WORKER_COUNT``（默认 2）只影响
  API worker 数，与调度器无关。``.env`` 里的 ``SCHEDULER_ENABLED`` 在容器部署下被 compose 覆盖，
  对 backend 容器不生效。

Agent Runtime 的装配是**非致命**的：LLM 配置缺失或会话记忆连不上时，只记异常类型（配置和
连接串里都有凭据，异常文本可能带出来），把 ``app.state.agent_runtime`` 留成 ``None``，进程
照常启动，只有 ``/agent/*`` 返回 503。所以「服务起来了」不等于「Agent 可用」，改完 LLM 配置
要看启动日志里有没有 ``Agent 运行时装配失败``。``LLM_MODEL`` 填成上游不存在的名字属于另一
种情况：启动完全看不出来，要到第一次提问才报错。

## 配置

**完整键列表、默认值和注释以 ``.env.example`` 为准**，``uv run`` 会自动加载 ``.env``。
这里只讲几个填错会直接出问题的：

```text
AUTH_COOKIE_SECURE      生产 HTTPS 必须 true；本地 http 联调才设 false
AUTH_COOKIE_SAMESITE    只允许 strict 或 lax
AUTH_ADMIN_EMAIL        保底超级管理员，必须与 AUTH_ADMIN_PASSWORD 同时配置或同时注释。
AUTH_ADMIN_PASSWORD     留成 AUTH_ADMIN_EMAIL= 这样的空值会因邮箱格式校验直接启动失败。
                        密码 12 到 128 字符，且不能等于邮箱。
FRESHRSS_SYNC_CATEGORIES  分类白名单，JSON 数组。不配就同步不到任何东西。
SCHEDULER_ENABLED         默认 false。生产要定时同步必须在 .env 里显式 true；
                          关闭时定时任务管理 API 仍可用（可手动触发），只是不到点自动执行。
SCHEDULER_TIMEZONE        cron 表达式的解释时区，默认 Asia/Shanghai。只影响「0 9 * * *」
                          翻译成哪个时刻；数据库存储一律 UTC，不受影响。
QDRANT_DISTANCE         改这个或维度必须新建 Schema/Collection，不能原地改。
LLM_API_KEY             LLM_PROVIDER=openai_compatible 时必须非空，否则 /agent/* 全部 503；
                        provider=ollama 时允许为空。检索接口不受影响。
LLM_MODEL               必须是 LLM_BASE_URL 那一侧真实存在的模型名，填错要到第一次
                        提问才报错，启动时看不出来。
LLM_USER_AGENT          默认 agent-lab。留空则沿用 SDK 默认值，此时部分中转站会按
                        User-Agent 把 openai SDK 的默认标识拦成 403，见下文。
LANGSMITH_TRACING       默认 false。设成 true 意味着提问内容和检索到的新闻正文会离开
                        本机、发往境外云服务，并且要同时配 LANGSMITH_API_KEY。
```

``LLM_USER_AGENT`` 存在的原因是一次真实排查：某些 OpenAI 兼容中转站按 User-Agent 拦截通用
SDK 流量，openai SDK 默认发的 ``OpenAI/Python x.y.z`` 会被判 403 ``PermissionDeniedError``
（消息形如 ``Your request was blocked.``），而同一个 Key 换个 User-Agent 就能正常调用。
所以 403 单独映射成 ``llm_request_blocked`` 而不是和 401 合并进 ``llm_authentication_failed``：
两者都不可重试、都是 502，但一个要换凭据、一个要查客户端身份，合并会把排查方向带偏。

``LLM_CHECKPOINT_POOL_SIZE`` 不能通过环境变量设置：该字段声明为 ``strict=True``，而环境
变量取到的一律是字符串，配上去会在启动时直接 ``ValidationError``。要改就改
``config/llm.py`` 里的默认值 ``4``。

``LANGSMITH_*`` 的键名刻意对齐 LangSmith 官方环境变量，但本项目用 pydantic-settings 读
``.env``、不写 ``os.environ``，LangSmith SDK 自己看不到这些值——追踪开关由 ``agent.runtime``
显式传入。所以改这些值必须重启进程才生效。

``OLLAMA_API_KEY`` 与 ``QDRANT_API_KEY`` 允许为空并由 ``SecretStr`` 保护。非空时在
``config/ollama_embedding.py`` 的 ``build_ollama_headers()`` 中集中采用 Bearer
``Authorization`` 约定；如果反向代理实际使用其他 header，只调整这一处。这两个 Key 只是
服务访问上游的凭据，**不能**当作浏览器认证。不要把真实密钥写入源码、测试、README 或
``.env.example``。

## 本地运行

```powershell
uv sync
Copy-Item .env.example .env
# 编辑 .env，同时填写 AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD；
# 本地 HTTP 设置 AUTH_COOKIE_SECURE=false，生产 HTTPS 必须保持 true。
uv run alembic upgrade head
# 只在要用 Agent 对话页时需要：建四张 checkpoint* 会话历史表，幂等，可重复执行。
uv run agent-lab init-checkpointer
uv run agent-lab run-once --limit-per-source 2 --batch-size 20
uv run uvicorn agent_lab.main:app --reload --host 127.0.0.1 `
  --loop agent_lab.runtime:selector_loop_factory
```

``--loop agent_lab.runtime:selector_loop_factory`` 只为解决 Windows 兼容问题：Uvicorn
在 Windows 默认用 ProactorEventLoop，而 Psycopg 3 的异步连接要求 SelectorEventLoop。
Linux 默认事件循环可直接运行，不需要这个参数。

健康检查（无需登录，只执行 ``SELECT 1``，不访问 Ollama 或 Qdrant）：

```text
http://127.0.0.1:8000/health
```

## 手动写入命令

七个 CLI 子命令（``agent-lab``）都是显式、一次性、有界的：

```powershell
# 交互式创建内部登录账号；密码在终端隐藏输入，不进命令历史
uv run agent-lab create-user --email someone@example.com
uv run agent-lab create-user --email admin2@example.com --superuser

# 只执行 FreshRSS -> PostgreSQL；每个白名单来源默认最多 2 篇
uv run agent-lab sync-news --limit-per-source 2

# 显式准备 Qdrant current Alias，并顺序处理最多 20 个 pending/failed 文档
uv run agent-lab index-pending --batch-size 20 --stale-after-minutes 60

# 先同步，再处理一个索引批次，然后退出
uv run agent-lab run-once --limit-per-source 2 --batch-size 20

# 建 Agent 会话历史的四张 checkpoint* 表（数据库结构写入，幂等，不动业务表和 Qdrant）
uv run agent-lab init-checkpointer

# 清掉没有归属记录的会话历史。默认只报数不删，看清数字再加 --yes
uv run agent-lab prune-orphan-threads
uv run agent-lab prune-orphan-threads --yes

# 清掉最后活跃时间早于 N 天前的会话（checkpointer 历史与归属记录一起删）。
# 默认只报数不删，看清数字再加 --yes
uv run agent-lab prune-old-threads --before-days 90
uv run agent-lab prune-old-threads --before-days 90 --yes
```

``prune-orphan-threads`` 与 ``prune-old-threads`` 都会**不可恢复地删除用户数据**，所以默认都是
预演：不加 ``--yes`` 只报告将删除的会话数量、一条都不删。它们必须在 ``alembic upgrade head``
之后跑——
``agent_threads`` 表还不存在时，**所有**会话都会被判成孤儿。「孤儿」指 checkpointer 里有历史、
业务表里没有归属记录的会话，来源有三种：归属功能上线之前留下的历史、迁移被回滚过、
以及删除会话时「清历史成功、删归属记录失败」的残余。它们在网页上既列不出来也删不掉。

``init-checkpointer`` 是唯一一个写数据库**结构**的子命令，其余几个写的是业务数据。它单独成
命令而不是放进启动路径，是因为建表属于运维动作：应用进程平时不该带着 DDL 权限跑，而且
LangGraph 升级表结构时，自动执行会让重启静默改库（[ADR 0004
checkpointer-tables-outside-alembic](../docs/adr/0004-checkpointer-tables-outside-alembic.md)）。

参数上限与各命令的行为差异见
[`docs/architecture.md`](docs/architecture.md) 的「手动写入入口」。

## PowerShell 联调

先建立一个登录会话。密码通过隐藏的凭据提示读取，不写入命令历史：

```powershell
$credential = Get-Credential -UserName admin@example.com
$login = @{
  username = $credential.UserName
  password = $credential.GetNetworkCredential().Password
}
Invoke-WebRequest -Method Post `
  -Uri http://127.0.0.1:8000/auth/login `
  -Body $login `
  -ContentType application/x-www-form-urlencoded `
  -SessionVariable session
$login.password = $null
```

Chunk 级检索：

```powershell
$body = @{
  query = "央行近期是否调整利率？"
  top_k = 10
  filters = @{ labels = @("宏观", "利率") }
} | ConvertTo-Json -Depth 4

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/vector-search `
  -WebSession $session `
  -ContentType application/json `
  -Body $body
```

文档分组检索与按需全文：

```powershell
$grouped = @{
  query = "央行近期是否调整利率？"
  document_limit = 10
  matches_per_document = 3
} | ConvertTo-Json

$results = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/document-search `
  -WebSession $session `
  -ContentType application/json `
  -Body $grouped

Invoke-RestMethod -Method Get `
  -Uri "http://127.0.0.1:8000/documents/$($results[0].document_id)" `
  -WebSession $session
```

手动执行 Pipeline（需要超级用户会话）：

```powershell
$pipeline = @{
  limit_per_source = 2
  batch_size = 20
  stale_after_minutes = 60
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/pipeline/run-once `
  -WebSession $session `
  -ContentType application/json `
  -Body $pipeline
```

## 测试

默认测试完全离线，不访问 PostgreSQL、FreshRSS、Ollama 或 Qdrant：

```powershell
uv run pytest -q
```

写 HTTP 测试时用 ``tests/app_helpers.py`` 的 ``create_offline_app`` 建应用，别直接调
``create_app``：后者每个工厂参数都有生产默认值，漏掉一个，lifespan 就会拿真实的那个去连真实
服务。这已经发生过一次——``agent_runtime_factory`` 被 5 个文件集体漏掉，每次进 lifespan 白等
30 秒连接池超时，而 lifespan 那个 ``except Exception`` 把失败咽掉了，所以测试照常通过、没人
发现。想验证离线，把 ``DATABASE_URL`` 临时指到 ``192.0.2.1`` 这类不可达地址再跑一遍，耗时不变
才算真离线。

只有 5 个测试文件受环境变量门控，默认跳过。仅在明确允许访问当前 ``.env`` 指向的服务时启用；
它们只发送短小、无敏感信息的中文文本，不打印密钥或完整向量。

真实 PostgreSQL 的环境管理员同步与账号管理 Service 行为；使用随机临时记录并自动清理：

```powershell
$env:RUN_POSTGRES_AUTH_INTEGRATION_TEST="1"
uv run pytest -q tests/test_auth_environment_integration.py
```

真实 Ollama 的 query 与批量 document Embedding；校验维度一致且数值有限：

```powershell
$env:RUN_OLLAMA_INTEGRATION_TEST="1"
uv run pytest -q tests/test_ollama_embedding_integration.py
```

真实远程 Qdrant 的 Collection/Alias/Point 生命周期；只写随机隔离命名的测试 Collection
并在 finally 中删除：

```powershell
$env:RUN_QDRANT_REMOTE_INTEGRATION_TEST="1"
uv run pytest -q tests/test_qdrant_remote_integration.py
```

真实 PostgreSQL + FreshRSS + Ollama + Qdrant 的定时任务端到端：验证迁移种子任务、并用
可回滚事务真实执行 ``freshrss_sync`` 与 ``index_pending`` 各一轮（历史不残留）：

```powershell
$env:RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST="1"
uv run pytest -q tests/test_scheduler_postgres_integration.py
```

真实 PostgreSQL 的会话归属过滤与旧会话清理（验证归属只匹配自己的行；需已跑过
``alembic upgrade head``）：

```powershell
$env:RUN_POSTGRES_AGENT_THREAD_INTEGRATION_TEST="1"
uv run pytest -q tests/test_agent_thread_ownership_integration.py
```

按主题挑选离线测试：

```powershell
# 只读搜索编排、过滤与 Runtime 组装（fake Embeddings + 内存 Qdrant）
uv run pytest -q tests/test_vector_search.py tests/test_qdrant_runtime.py

# HTTP 接口契约与错误映射（fake Runtime + httpx ASGITransport）
uv run pytest -q tests/test_vector_search_api.py tests/test_document_search.py `
  tests/test_documents_api.py

# 错误契约的跨表不变量与全仓库 detail 文案（纯静态，不起 app）
uv run pytest -q tests/test_error_contract.py

# Agent 链路：工具、中间件、SSE 事件序列与 /agent/chat 契约（fake 模型，不联网、不连库）
uv run pytest -q tests/test_agent_tools.py tests/test_agent_middleware.py `
  tests/test_agent_streaming.py tests/test_agent_chat_api.py `
  tests/test_agent_checkpointer.py

# 认证、权限边界与账号管理契约
uv run pytest -q tests/test_auth.py tests/test_user_admin.py

# 手动写入链路：CLI、批次执行 Service 与流水线 API
uv run pytest -q tests/test_cli.py tests/test_news_pipeline_execution.py `
  tests/test_pipeline_api.py

# 定时任务：类型注册表与 cron 预览、调度器包装器、管理 API 契约（假 Store/Runtime，不连库）
uv run pytest -q tests/test_scheduled_task_registry.py tests/test_scheduler_runner.py `
  tests/test_scheduled_jobs_api.py

# 增量同步与正文质量
uv run pytest -q tests/test_freshrss_incremental_sync.py tests/test_content_quality.py

# 切分、Embedding、Payload 与索引状态机
uv run pytest -q tests/test_document_pipeline.py tests/test_ollama_embedding.py `
  tests/test_qdrant_vector_store.py tests/test_document_indexing_service.py
```

## Alembic

```powershell
# 查看当前版本
uv run alembic current

# 检查 ORM 与数据库是否存在结构差异
uv run alembic check

# 根据 ORM 变化生成迁移，说明优先使用中文并附简短英文
uv run alembic revision --autogenerate -m "中文说明 short english summary"

# 升级到最新版本
uv run alembic upgrade head
```

自动生成的迁移必须人工审查。表清单见
[`docs/architecture.md`](docs/architecture.md) 的「数据库表」。

Agent 的会话数据分两处，别搞混：``agent_threads``（谁拥有哪个会话、标题、最后活跃时间）
**由 Alembic 管**，是普通业务表；会话的消息内容在 checkpointer 的四张表里，不由 Alembic 管。
分开的理由见 [ADR 0009](../docs/adr/0009-agent-thread-ownership-in-own-table.md)。回滚建
``agent_threads`` 的那个迁移会让每个会话变成孤儿——历史还在，但谁都读不到也删不掉。

四张 ``checkpoint*`` 表（Agent 会话历史内容）**不在 Alembic 管辖范围内**：它们由
langgraph-checkpoint-postgres 自建自迁移，只能通过 ``agent-lab init-checkpointer`` 创建
（[ADR 0004](../docs/adr/0004-checkpointer-tables-outside-alembic.md)）。它们不在
``Base.metadata`` 里，所以 autogenerate 本会把它们当
成「库里多出来的表」而生成 ``op.drop_table('checkpoints')``——``alembic/env.py`` 用
``agent.checkpointer.include_object`` 把这四个表名排除在比较之外，挡住了这件事。这道排除的
单元测试是离线的（直接调 ``include_object``），另外已在「库里真有这四张表」的情况下跑过一次
``alembic check``，结果是无差异。改动这块之后值得再跑一次：它应当报告无差异，而不是提示有
多余的表。新增
checkpointer 表时必须同步 ``CHECKPOINTER_TABLE_NAMES``，漏改就会在下一次 autogenerate 里
出现一条删表语句。它按表名精确匹配、不按 ``checkpoint`` 前缀匹配，所以将来叫
``checkpoint_review`` 之类的业务表不会被顺手排掉。

## 生产前置要求

生产必须使用 HTTPS 与 Secure Cookie，并在网关限制登录频率、请求体大小、并发和 timeout
——服务本身不做这些。部署步骤见平台根目录
[`docs/container_deployment.md`](../docs/container_deployment.md)。

数据库迁移不能放进每个 FastAPI Worker 的启动流程：部署时先由单独步骤执行
``alembic upgrade head``，成功后再启动应用。要提供 Agent 对话则在同一阶段追加一次
``agent-lab init-checkpointer``（幂等，可重复执行），同样在启动应用之前完成。

从「会话归属功能之前」的版本升上来时，库里可能已经存有一批没有归属记录的会话历史。它们不影响
新会话，但会一直占着 checkpointer 的四张表，而且任何账号都读不到也删不掉。升级后先跑一次
``agent-lab prune-orphan-threads``（预演）看数字，确认数量合理再加 ``--yes``。这一步是可选的，
不做只是留着一批用不到的数据；**做错的代价更大**：在 ``alembic upgrade head`` 之前跑它会把
所有会话都判成孤儿。

用 Agent 对话还要额外注意两点。一是 ``/agent/chat`` 是 SSE 长连接，网关的响应缓冲和读超时
必须放开，否则事件会被攒着直到超时——表现是页面一直转圈然后报错，而后端日志里这一轮是成功
的。二是每一轮对话都在花真金白银调上游模型，``/agent/*`` 因此只对超级用户开放，网关侧的
限流要按这个成本来设，不要沿用检索接口的额度。

## 运行时隔离

本项目使用独立 PostgreSQL Database ``news_vector_lc``，Qdrant 已用
``QDRANT_ENVIRONMENT``、Schema 版本和 generation 组成物理 Collection 名称，并使用环境
隔离的 current Alias。与其他项目同时运行还必须使用不同端口：

```powershell
uv run uvicorn agent_lab.main:app --reload --port 8001 `
  --loop agent_lab.runtime:selector_loop_factory
```

``.venv`` 不能随项目目录复制。Windows 虚拟环境中的 ``uvicorn.exe`` 等启动器可能嵌入旧
目录的 Python 绝对路径，导致副本暗中加载旧项目环境。复制目录后应在副本根目录执行：

```powershell
uv venv --clear .venv
uv sync --all-groups
```

以后接入 Redis 或容器编排时，也要分别设置 key 前缀、持久化目录、容器名和宿主机端口，
避免两个实例共享状态或争用资源。
