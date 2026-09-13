# Agent Lab 后端

本服务接收 FreshRSS HTML 与上传的 MD/TXT 原件，使用 MinIO/S3 持久保存，再由 Docling
解析结构并生成带章节上下文的 Chunk。Ollama `bge-m3:567m` 消费冻结文本生成 Embedding，
Qdrant 保存候选索引，准备成功后切换 PostgreSQL 中的已采用版本。对外提供受登录保护的只读
语义检索与超级用户文档审核；Celery Worker 执行已受理的文档批次、HTTP Pipeline 和定时任务，CLI 继续复用同一业务能力与写资源协调。

在检索之上还有一条 Agent 对话链路（``POST /agent/chat``，SSE）：一个 LangGraph 工具调用
Agent 把上面的检索能力当工具用，在本次知识库范围内由生成式 LLM 组织带证据引用的答案。
Tool 不修改 Document 或 Qdrant；会话归属和范围写 ``agent_threads``，消息及证据写四张 ``checkpoint*`` 表（见
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

文件管理走超级用户 `/file-documents` API：上传创建独立 Document，按 ID、正式及管理修订替换；
文件不要求 Source 或外部 URL。原件与待办保存成功即返回，后台正常结果自动采用，异常等待人工处理。
`/document-management` 提供原件、候选、草稿、结构与 Chunk 预览、采用、拒绝、重试和历史查询。
新索引失败时旧已采用版本继续可用；完整删除通过持久待办清除原件、历史和索引。格式及输入边界见
OpenAPI，跨模块流程见 [文档生命周期](../docs/flows/file-document-lifecycle.md)。

## 外部依赖

外部依赖按能力配置：

```text
PostgreSQL   业务事实、账号与登录 Token。独立 Database news_vector_lc。
             必须先执行 alembic upgrade head（当前 head 用 ``alembic heads`` 查看）。
             Agent 会话的归属、选择范围与列表元信息在 agent_threads 表（由 Alembic 管）；
             会话历史内容在 checkpointer 自己的四张表，不由 Alembic 管（见下面「Alembic」）。
FreshRSS     唯一的新闻来源。动态网页回源和站点 CSS selector 由它负责，
             Python Pipeline 里不能加站点判断。
Ollama       bge-m3:567m，1024 维。query 与 document 使用同一模型，
             换模型等于换索引空间（必须提升 schema_version 并重建）。
Qdrant       Point 存储。current Alias 必须由部署预先准备，搜索不会创建它。
MinIO/S3     私有原件存储。先创建桶并配置后端读写与删除权限；浏览器不直接访问桶。
Redis        使用环境已有实例，生产 Compose 不创建 Redis；任务消息与后续缓存按键前缀区分。
             启用 AOF、持久数据盘和 noeviction；缓存按 TTL 过期，内存满时拒绝新增写入。
             PostgreSQL 才是受理、状态与结果的事实来源；没有 Celery result backend。
生成式 LLM   仅 /agent/* 需要。OpenAI 兼容中转站或 Ollama，二选一由 LLM_PROVIDER 决定。
             和上面的 Ollama Embedding 是两件事：Embedding 产出向量，这个产出文字，
             即使都指向同一台 Ollama 也是两套配置。不配则只有 /agent/* 返回 503。
```

Python 版本固定 `>=3.12,<3.13`，依赖声明与解析版本以 `pyproject.toml`、`uv.lock` 为准。
Docling 使用文本解析所需的轻量依赖，锁定 Docling、docling-core 和 BGE-M3 tokenizer 资源；
Agent 继续使用 LangChain/LangGraph，向量存储使用官方 qdrant-client。会话记忆由 psycopg
连接 PostgreSQL，与 SQLAlchemy 的业务连接池分开。

## 启动前置条件

```text
1. alembic upgrade head 已完成          （启动不执行 migration；agent_threads 表由它建）
2. Qdrant current Alias 已存在          （搜索不会 ensure_ready）
3. .env 配置合法                         （启动即读，非法配置直接失败）
4. agent-lab init-checkpointer 已完成    （仅用 /agent/* 时需要；启动不建表）
5. S3 私有桶与原件访问配置可用          （接收文件、FreshRSS 原件和完整删除需要）
6. tokenizer 资源校验通过               （见「文档处理资源」）
7. Redis、单个 Beat 与 prefork Worker 可用（文档批次不需要另建 cron）
```

第 1 步不到位时 ``/agent/*`` 会返回 503（``agent_thread_database_unavailable``）而不是崩溃：
归属记录读不出来就不让对话开始，避免在没有归属的情况下写下一段谁都管不了的历史。

API 启动访问 PostgreSQL，同步环境托管管理员并装配受理与查询组件；不在启动时探测业务上游或 Redis，不创建 Collection/Alias。Redis 暂不可用时仍可持久受理，恢复后由 Beat 补投原执行。

API、单个 Beat 和 Worker 使用同一份后端代码、独立进程与数据库连接。Beat 动态读取周期配置并维护补投、恢复及历史；生产 Worker 使用 Linux prefork，子进程在 fork 后建立自己的持久 asyncio 循环和连接池。Windows 原生的 HTTP、Beat 和 solo Worker 已通过受理、补投、资源等待、非空业务处理及正常关停验证；生产 prefork 的多进程与故障验收由 Linux 承担，具体范围见「测试」。`WORKER_COUNT` 控制 API 进程数，`TASK_WORKER_CONCURRENCY` 控制每个 Worker 容器的子进程数；增加 Worker 实例不增加 Beat。旧进程内调度、独立 scheduler 和常驻文档消费者均已移除。

同步、索引和清理保留周期配置，文档处理和 HTTP Pipeline 也接入公共任务组件。配置启用或执行期间可以编辑、停用和删除，后续受理使用新配置，已有执行沿用旧快照；删除后仍能按执行编号查询。相同配置未结束时，新的人工触发返回冲突，新周期留下跳过记录。错过 cron 不补跑；已受理工作继续推进。同任务约束、写资源等待和清理占用由 PostgreSQL 协调，CLI 也参与。详见 [ADR 0019](../docs/adr/0019-scheduled-execution-and-write-coordination.md)。

清理默认预演，仅选择已采用且超过保留期、没有待处理候选的 Document；待审核、失败和拒绝记录不自动清理。每批 50 连续处理，没有整次上限。失败可能保留删除待办或待核实占用，不能仅因心跳过期就解锁。规则与代价见 [ADR 0019](../docs/adr/0019-scheduled-execution-and-write-coordination.md)，排查和升级顺序见 [部署文档](../docs/container_deployment.md#定时任务升级与恢复)。

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
S3_ENDPOINT / S3_BUCKET    后端可达的 MinIO/S3 地址与预先创建的私有桶。
S3_ACCESS_KEY / S3_SECRET_KEY  仅配置在服务端；需要读取、条件写入和删除原件的权限。
S3_REGION / S3_ADDRESSING_STYLE  区域及 path/virtual 寻址方式，按对象存储配置。
DOCUMENT_TOKENIZER_PATH    已准备并校验的本地 tokenizer 目录。
DOCUMENT_CHUNK_MAX_TOKENS  包含标题与特殊 token 的文本预算，改变后须重新预览与采用。
SCHEDULER_TIMEZONE        cron 表达式的解释时区，默认 Asia/Shanghai。只影响「0 9 * * *」
                          翻译成哪个时刻；数据库存储一律 UTC，不受影响。
REDIS_URL                项目共用 Redis 连接；本地默认 redis://127.0.0.1:6379/0，容器部署必须填写已有实例的可达地址。
REDIS_PASSWORD           Redis 密码，留空表示不需要密码；独立填写，不放入 URL，无需转义特殊字符。
TASK_QUEUE_NAME           单个业务队列名，也决定任务键前缀 tasks:<队列名>:，三个进程必须相同。
TASK_QUEUE_VISIBILITY_TIMEOUT 消息可见性超时，不是业务时长上限；重投仍需数据库领取。
TASK_QUEUE_PUBLISH_TIMEOUT_SECONDS 单次 Redis 发布/连接超时，失败由数据库待办继续补投。
TASK_QUEUE_REDELIVERY_SECONDS / TASK_QUEUE_MAINTENANCE_SECONDS 补投间隔与 Beat 维护间隔。
TASK_WORKER_CONCURRENCY   每个 Worker 容器的 prefork 子进程数，Compose 默认 2。
QDRANT_DISTANCE         改这个或维度必须新建 Schema/Collection，不能原地改。
LLM_API_KEY             LLM_PROVIDER=openai_compatible 时必须非空，否则 /agent/* 全部 503；
                        provider=ollama 时允许为空。检索接口不受影响。
LLM_MODEL               必须是 LLM_BASE_URL 那一侧真实存在的模型名，填错要到第一次
                        提问才报错，启动时看不出来。
LLM_USER_AGENT          默认 agent-lab。留空则沿用 SDK 默认值，此时部分中转站会按
                        User-Agent 把 openai SDK 的默认标识拦成 403，见下文。
LANGSMITH_TRACING       默认 false。设成 true 意味着提问内容和检索到的文档正文会离开
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
uv run python -m agent_lab.prepare_document_resources
uv run alembic upgrade head
# 只在要用 Agent 对话页时需要：建四张 checkpoint* 会话历史表，幂等，可重复执行。
uv run agent-lab init-checkpointer
uv run agent-lab run-once --limit-per-source 2 --batch-size 20
uv run uvicorn agent_lab.main:app --reload --host 127.0.0.1 `
  --loop agent_lab.runtime:selector_loop_factory
```

确认 `REDIS_URL` 指向的 Redis 可达后，在 Linux（含 Docker/WSL）的 `backend/` 分别开两个终端，运行一个 Beat 与 prefork Worker。API、Beat、Worker 的数据库、业务配置、Redis 连接与队列名保持一致：

```bash
uv run celery -A agent_lab.tasks.celery_app:app beat --loglevel=INFO --pidfile=
uv run celery -A agent_lab.tasks.celery_app:app worker --pool=prefork --concurrency=2 --hostname=worker@%h --loglevel=INFO
```

Windows 原生本地联调保留同一 Beat 命令，把 Worker 的 `--pool=prefork --concurrency=2` 换成 `--pool=solo --concurrency=1` 即可。真实受理、补投、连续执行及正常关停已通过本地联调；生产多进程故障恢复仍由 Linux 验收覆盖。

只启动 API 不会自动推进文档解析或 HTTP Pipeline。已有 CLI `index-pending` 仍可显式执行一个处理批次，并遵守同一资源协调。Beat 就绪用 `uv run python -m agent_lab.tasks.status --check`；Worker 连通检查用 `celery ... inspect ping`，业务是否推进仍按执行编号查询。容器入口与停机切换见[部署文档](../docs/container_deployment.md#定时任务升级与恢复)。

``--loop agent_lab.runtime:selector_loop_factory`` 只为解决 Windows 兼容问题：Uvicorn
在 Windows 默认用 ProactorEventLoop，而 Psycopg 3 的异步连接要求 SelectorEventLoop。
Linux 默认事件循环可直接运行，不需要这个参数。

健康检查（无需登录，只执行 ``SELECT 1``，不访问 Ollama 或 Qdrant）：

```text
http://127.0.0.1:8000/health
```

## 手动写入命令

CLI 子命令（``agent-lab``）都是显式、一次性执行后退出的：

```powershell
# 交互式创建内部登录账号；密码在终端隐藏输入，不进命令历史
uv run agent-lab create-user --email someone@example.com
uv run agent-lab create-user --email admin2@example.com --superuser

# 只接收 FreshRSS 原始 HTML 到 S3 并确认 PostgreSQL 待办；每个白名单来源默认最多 2 篇
uv run agent-lab sync-news --limit-per-source 2

# 有界消费解析、采用及旧索引回收待办；明确失败的候选等待人工重试
uv run agent-lab index-pending --batch-size 20 --stale-after-minutes 60

# 先同步，再处理一个索引批次，然后退出
uv run agent-lab run-once --limit-per-source 2 --batch-size 20

# 使用已采用版本的冻结 Chunk 重建，选择尚未存在的 generation
uv run agent-lab rebuild-index --generation 2

# 发布中断后核对已准备目标与当前 Alias，恢复映射；不重新解析或生成向量
uv run agent-lab recover-index-rebuild --generation 2

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

手动提交 Pipeline（需要超级用户会话，返回 HTTP 202）：

```powershell
$pipeline = @{
  limit_per_source = 2
  batch_size = 20
  stale_after_minutes = 60
} | ConvertTo-Json

$pipelineRequestId = [guid]::NewGuid().ToString()
$receipt = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/pipeline/run-once `
  -Headers @{ "Idempotency-Key" = $pipelineRequestId } `
  -WebSession $session `
  -ContentType application/json `
  -Body $pipeline

Invoke-RestMethod -Method Get `
  -Uri "http://127.0.0.1:8000/task-runs/$($receipt.run_id)" `
  -WebSession $session
```

保存请求标识与原始参数后再提交；超时核对时重复同一个 POST 和标识，不重新生成标识。受理成功只代表已持久保存，最终同步与处理统计从执行详情读取。定时任务的立即执行和人工重试同样要求 `Idempotency-Key`；同一标识换内容返回冲突。`GET /task-runs` 列出全部执行，`POST /task-runs/{id}/cancel` 取消尚未开始或等待重试的执行；`POST /task-runs/{id}/retry` 为保留完整参数的失败记录创建关联新执行。默认重试及历史保留通过超级用户 `/task-policy` 管理，已有执行沿用受理时的策略。

## 知识库升级与索引重建

Docling 采用新文档处理表和 v3 索引规格。旧 v2 Point 缺少候选隔离所需身份，不能直接用于新版检索。
本期按已确认的开发资料重置方案切换，不建设旧正文回填或双处理路径。执行顺序：

1. 核对目标数据库、当前环境 Collection/Alias 和原件范围；确认 API、Beat、Worker、旧 scheduler、CLI 与远端未决写入已停止。
2. 准备私有 S3 桶、后端配置及锁定 tokenizer，执行 `uv run alembic upgrade head`。
3. 在已授权范围内清除文档、候选、已采用历史、审核记录和对应索引；仅重置确有必要重新接收的 Source checkpoint。
   保留账号、KnowledgeBase 配置、Source 绑定、任务配置、Agent 会话及其 checkpointer 历史。
4. 使用 `QDRANT_COLLECTION_SCHEMA_VERSION=v3` 和空的新目标，启动新版 API、Beat、Worker 并连接项目共用 Redis。
   上传合成 MD/TXT，接收范围内的 FreshRSS 条目，核对原件、预览、采用、检索和删除。
5. 记录清空和重新导入的数量、实际范围及未完成项。重置 checkpoint 后沿用 FreshRSS 首次有界同步，
   不承诺回灌全部历史；S3 未配置时不能完成该切换。

日常 `rebuild-index --generation N` 仅重建当前可用的已采用快照，复用冻结的 Chunk 与向量化文本。
新 generation 逐篇回读核验，建立发布屏障后切换 Alias 与数据库索引映射；正文 revision 和已采用历史不变。
切分规格变化必须重新预览、采用，不能用重建静默重切。构建失败保留原 Alias，发布中断用
`recover-index-rebuild --generation N` 核对；写占用恢复仍需先确认旧执行与远端写入已停止。

迁移 `f7c1d2e3a4b5` 增加处理、已采用版本和审核记录，表结构以迁移与 ORM 为准。部署操作独立于
离线测试；随机隔离验收通过不表示应用数据库已迁移或已切换。只生成 SQL、不连接数据库：

```powershell
uv run alembic upgrade e74b9a310c65:head --sql
```

## 文档处理资源

文档结构解析使用锁定的 Docling Markdown/HTML 文本后端，本期上传仍只开放 MD、TXT。
`docling-slim` 不安装 PDF/OCR 或 PyTorch；Chunk 计数使用固定 revision 的 BGE-M3 tokenizer。
安装依赖后，在 `backend/` 显式准备资源：

```powershell
uv run python -m agent_lab.prepare_document_resources
uv run python -m agent_lab.prepare_document_resources --check
```

首次准备需要访问 Hugging Face，仅下载 tokenizer 的四个配置与词表文件，不下载模型权重。
默认位置 `.cache/tokenizers/bge-m3`，可通过 `DOCUMENT_TOKENIZER_PATH` 指定预先准备的目录。
运行时校验所有文件 SHA-256 并只从本地加载，文件缺失或替换后不会静默下载另一套 tokenizer。
镜像构建也会准备并核验这些资源，构建环境需要可用网络；服务运行不需要访问 Hugging Face。

默认 `DOCUMENT_CHUNK_MAX_TOKENS=512`，预算包含章节上下文及模型特殊 token。
改变预算属于处理规格变化，需要新预览和采用，不能用重建静默改变已采用 Chunk。

## 测试

开发中先运行受影响的测试文件，需要定位单个用例时追加 `-k <用例名片段>`。连续小修改不逐次执行全量测试：

```powershell
uv run pytest -q tests/test_scheduler_runner.py
```

默认测试完全离线，不访问 PostgreSQL、FreshRSS、Ollama、Qdrant 或 S3。需要完整离线回归时执行：

```powershell
uv run pytest -q
```

CI 仅在后端代码、测试、依赖或配置变化时运行完整离线回归；纯文档和仅前端改动跳过后端测试。比较基线是同分支上一次成功部署，包含其后失败或被取消运行留下的改动。手动触发、工作流修改、基线缺失或无法查询时执行完整验证，范围规则见 [部署工作流](../.github/workflows/deploy.yml)。

写 HTTP 测试时用 ``tests/app_helpers.py`` 的 ``create_offline_app`` 建应用，别直接调
``create_app``：后者每个工厂参数都有生产默认值，漏掉一个，lifespan 就会拿真实的那个去连真实
服务。这已经发生过一次——``agent_runtime_factory`` 被 5 个文件集体漏掉，每次进 lifespan 白等
30 秒连接池超时，而 lifespan 那个 ``except Exception`` 把失败咽掉了，所以测试照常通过、没人
发现。现在 ``tests/conftest.py`` 默认阻断 psycopg 真实连接和 httpx 真实传输；替身接入遗漏会直接让测试失败，不通过访问真实连接来证明离线。

外部集成测试受环境变量门控，默认跳过。运行前需明确访问范围并获得授权；以下账号、模型和既有远程测试可能使用应用环境。定时任务测试可以使用老板已配置的开发 PostgreSQL/Qdrant，但只创建随机 schema、Collection、Alias 和合成数据，不碰业务数据，也不打印密钥或完整向量。

真实 PostgreSQL 的环境管理员同步与账号管理 Service 行为；使用随机临时记录并自动清理：

```powershell
$env:RUN_POSTGRES_AUTH_INTEGRATION_TEST="1"
uv run pytest -q tests/test_auth_environment_integration.py
```

真实 Ollama 的 query 与批量 document Embedding，并核对冻结 Chunk 的本地/服务端 token 计数；需要本地 tokenizer 资源：

```powershell
$env:RUN_OLLAMA_INTEGRATION_TEST="1"
uv run pytest -q tests/test_ollama_embedding_integration.py
```

真实 MinIO/S3 原件生命周期使用已有私有桶中的随机 `acceptance/docling/` 对象键，覆盖原始字节、
幂等条件写入、冲突不覆盖及按版本删除。不建桶、不更改桶版本设置、不读取或清空其他对象；
需要配置 `S3_*` 与相应权限。结果及测试键写入 `.pytest_cache/docling-s3-report.json`，默认跳过：

```powershell
$env:RUN_S3_INTEGRATION_TEST="1"
try {
  uv run pytest -q --tb=short tests/test_document_storage_integration.py
} finally {
  Remove-Item Env:RUN_S3_INTEGRATION_TEST
}
```

真实远程 Qdrant 的 Collection/Alias/Point 生命周期；只写随机隔离命名的测试 Collection
并在 finally 中删除：

```powershell
$env:RUN_QDRANT_REMOTE_INTEGRATION_TEST="1"
uv run pytest -q tests/test_qdrant_remote_integration.py
```

定时任务的多进程 PostgreSQL 与跨库 Qdrant 验证可以直接使用当前开发配置；测试每次创建随机 PostgreSQL schema、随机 Qdrant Collection/Alias，子进程独立提交，结束关闭子进程并删除测试资源。不访问 FreshRSS、Ollama 或模型，也不需要另建测试服务器。测试账号需要创建 schema 和表的权限：

```powershell
uv run pytest -q --tb=short --scheduler-configured-services `
  tests/test_knowledge_postgres_integration.py `
  tests/test_scheduler_postgres_integration.py `
  tests/test_task_migration_postgres_integration.py `
  tests/test_task_handoff_postgres_integration.py `
  tests/test_scheduler_retention_integration.py `
  tests/test_file_documents_integration.py `
  tests/test_processing_postgres_integration.py `
  tests/test_document_review_integration.py `
  tests/test_document_deletion_integration.py `
  tests/test_document_rebuild_integration.py
```

这组命令启用真实 PostgreSQL 和远程 Qdrant；原件存储与 Embedding 仍使用替身，不能作为真实 S3 或模型验收。文档夹具未启用远程 Qdrant 开关时使用内存实例。测试会生成常规 pytest/Python 缓存，不生成新闻导出文件。强制终止测试可能留下带 ``scheduler_test_`` 标识的资源，须先确认测试进程已退出再清理。离线测试不能证明多进程数据库锁、跨库恢复或实际部署；上面的隔离验证也只覆盖合成数据，不等于生产发布验收。

公共任务的真实队列验收使用 Linux prefork、独立 `redis-server` 和随机 PostgreSQL schema。夹具会停止自己创建的进程、丢弃自己的队列消息、重启自己的 Redis，覆盖 AOF、长任务真实重投、Worker 丢失、重连及动态 Beat；不控制共享 Redis。获得对应环境授权后可在安装 Docker 的环境中运行隔离项目：

```bash
docker compose -p agent-lab-task-tests -f docker-compose.task-tests.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p agent-lab-task-tests -f docker-compose.task-tests.yml down --volumes
```

该编排只建立内部测试网络与临时 PostgreSQL，不挂生产 `.env`，没有宿主端口。结果目录为 `.pytest_cache/task-environment/`，Worker/Beat/Redis 日志在其 `task-processes/` 下，先保留失败日志再清理。镜像内同时执行旧结构迁移、多进程 PostgreSQL 与文档事务交接中断验证；交接测试只构造合成待办，验证退出时整体回滚和已知结果重新保存，不调用原件或模型。有已授权 Linux PostgreSQL 时也可设置 `RUN_TASK_QUEUE_INTEGRATION_TEST=1`、`TASK_TEST_DATABASE_URL` 后运行 `tests/test_task_queue_integration.py`，本机需有 `redis-server`。

CI 的 Linux 验收按风险选择，失败即停止部署，进程日志保存为 Actions artifact：

- 任务核心、持久交接及相关业务存储变化时，验证真实请求去重、取消竞争、进程故障恢复和正常关停后续办；健康流程共用一次隔离环境，破坏进程或 Redis 的场景各自隔离。
- 消息组件、队列配置或后端依赖及容器配置变化时，追加 `queue_transport`：Redis AOF 重启、断线重连与自然可见性超时重投。自然重投必须等待真实消息证据，不缩短生产扫描行为来制造通过。
- 迁移、模型、数据库基础设施或迁移夹具变化时，追加历史升级验收。成功升级中的历史保留和新写入约束共用一次升级；拒绝升级及完整回滚独立验证。

在已授权且设置好上述队列开关，以及 `RUN_POSTGRES_SCHEDULER_INTEGRATION_TEST=1`、`SCHEDULER_TEST_DATABASE_URL` 的 Linux 测试环境，日常任务核心改动可只运行：

```bash
uv run pytest -q --tb=short -m "not queue_transport" \
  tests/test_task_queue_integration.py \
  tests/test_task_handoff_postgres_integration.py \
  tests/test_scheduler_postgres_integration.py
```

完整隔离编排保留全部验收。权限、跨存储恢复和用户数据保护测试继续按各自风险与环境授权执行。

已有开发 Redis 时可在 Windows 原生验证 HTTP、真实登录、Beat 和 solo Worker，无需 Docker。以下用例读取 `DATABASE_URL`、`REDIS_URL` 与 `REDIS_PASSWORD`，只创建随机 schema、隔离账号及带随机前缀的任务键。基础用例通过空知识库清理预演验证消息丢失补投、取消、连续执行和 Worker 正常关闭；业务组合用例还验证资源等待让出唯一 Worker，以及同步、索引、文档批次和 HTTP Pipeline 对非空资料的处理。后者保留真实 Docling、tokenizer、业务应用及事务，仅替换外部来源、原件、Embedding 和向量端口，不调用真实业务上游。两项已在 Windows 与开发 PostgreSQL／Redis 上通过，测试键、schema 和进程已清理；不重启或清空共享 Redis：

```powershell
$env:RUN_TASK_LOCAL_INTEGRATION_TEST="1"
try {
  uv run pytest -q --tb=short --basetemp=.pytest_cache/task-local tests/test_task_local_integration.py tests/test_task_business_integration.py
} finally {
  Remove-Item Env:RUN_TASK_LOCAL_INTEGRATION_TEST
}
```

日志与清理报告在 `.pytest_cache/task-local/`，仅该次创建的资源在退出时清理。solo 的单进程验证不能替代前述 Linux prefork 的并发、进程崩溃和 Redis 重启验收。

三存储恢复单独使用真实 PostgreSQL、Qdrant 和已有私有 S3 桶，默认跳过。以下命令只创建随机 schema、Collection/Alias 与 `acceptance/task-recovery/` 对象。五种场景分别覆盖 Qdrant/S3 删除成功后数据库确认未保存、数据库最终收尾失败，以及 Qdrant/S3 实际删除后应用收到异常；最后两种必须保留待核实和写占用，拒绝普通重试，明确核实后才能继续。所有场景均检查配置/普通历史清理后仍可继续业务待办，已经完整删除的目标不重复处理：

```powershell
$env:RUN_TASK_CROSS_STORAGE_INTEGRATION_TEST="1"
try {
  uv run pytest -q --tb=short --scheduler-configured-services tests/test_task_cross_storage_integration.py
} finally {
  Remove-Item Env:RUN_TASK_CROSS_STORAGE_INTEGRATION_TEST
}
```

该命令需事先配置并授权 `S3_*` 读写删除；`--scheduler-configured-services` 提供开发 PostgreSQL/Qdrant 地址。每种故障的精确资源与远端清理结果记录在 `.pytest_cache/task-cross-storage-*.json`。它验证真实三存储与公共 Worker 业务接缝，消息进程语义由前一组真实队列测试验证；两组都通过仍不能替代生产切换验收。

第二阶段文件验证可只运行 ``tests/test_file_documents_integration.py``：覆盖上传到索引、检索、
全文和替换，以及不同状态按 ID 删除、Qdrant 确认后数据库失败恢复、定时清理排除人工待办。
样本资料与代表问题见 ``tests/fixtures/knowledge-base-phase-two/README.md``。
真实回答验收也使用随机 PostgreSQL schema 和 Qdrant Collection/Alias，并调用当前配置的
Embedding 与生成模型；只发送该目录中的合成资料和问题，不读取业务资料或访问 FreshRSS。
该操作会写入隔离服务资源并产生模型调用，须先确认运行授权；默认测试中保持跳过。

```powershell
$env:RUN_KNOWLEDGE_ANSWER_ACCEPTANCE_TEST="1"
try {
  uv run pytest -q --tb=short --scheduler-configured-services tests/test_knowledge_answer_acceptance.py
} finally {
  Remove-Item Env:RUN_KNOWLEDGE_ANSWER_ACCEPTANCE_TEST
}
```

测试记录检索、回答、回放、引用和耗时到 ``.pytest_cache/phase-two-answer-report.json``。
自动检查范围与出处身份后，仍须逐题核对结论是否被原文支持、推断是否标注、冲突是否说明；
报告中的人工语义核对状态初始为 pending，不能用引用 ID 校验通过代替。

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

# 错误码、HTTP 状态、重试提示、脱敏及跨表一致性（不起 app）
uv run pytest -q tests/test_error_contract.py

# Agent 链路：工具、中间件、SSE 事件序列与 /agent/chat 契约（fake 模型，不联网、不连库）
uv run pytest -q tests/test_agent_tools.py tests/test_agent_middleware.py `
  tests/test_agent_streaming.py tests/test_agent_chat_api.py `
  tests/test_agent_checkpointer.py

# 第二阶段：文件入口、多库范围与证据跨流式/回放验证
uv run pytest -q tests/test_file_documents.py tests/test_knowledge_search_scope.py `
  tests/test_agent_evidence_scope.py

# 认证、权限边界与账号管理契约
uv run pytest -q tests/test_auth.py tests/test_user_admin.py

# 手动写入链路：CLI、批次执行 Service 与流水线 API
uv run pytest -q tests/test_cli.py tests/test_news_pipeline_execution.py `
  tests/test_pipeline_api.py

# 公共任务：受理、快照、重试、资源等待与 HTTP；内存 SQLite 不能证明 PostgreSQL 并发锁
uv run pytest -q tests/test_task_execution.py tests/test_task_api.py

# 定时任务：类型注册、cron 预览、动态 Beat、管理 API 与业务资源协调（不连真实服务）
uv run pytest -q tests/test_scheduled_task_registry.py tests/test_scheduler_runner.py `
  tests/test_scheduled_jobs_api.py tests/test_scheduler_safety.py tests/test_scheduler_lifecycle.py `
  tests/test_document_retention_service.py

# 增量同步与正文质量
uv run pytest -q tests/test_freshrss_incremental_sync.py tests/test_content_quality.py

# 真实 Docling 解析、冻结 Chunk、原件协议与审核契约（外部服务仍为替身）
uv run pytest -q tests/test_document_processing.py tests/test_ollama_embedding.py `
  tests/test_candidate_index.py tests/test_document_storage.py tests/test_document_review_api.py
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

Agent 的会话数据分两处，别搞混：``agent_threads``（归属、选择范围、标题、最后活跃时间）
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

Redis 连接由 `config/redis.py` 统一管理，任务队列、后续缓存等使用方各自管理键命名。API、Beat 和 Worker 必须在同一环境内共用 `REDIS_URL` 与 `TASK_QUEUE_NAME`；多个环境复用 Redis 时使用不同队列名，以区分消息及未确认消息等辅助键。生产 Compose 默认只运行这三个应用容器，通过外部 `1panel-network` 连接已有 Redis；实例的持久化、容量和网络由其自身部署管理。共享实例的 `noeviction` 作用于全部键，缓存用 TTL 控制保留时间；内存满时新增写入会失败，任务投递由 PostgreSQL 保留依据并补试。Worker 子进程的异步连接只在本进程的持久循环中创建、使用和关闭。
