# Agent Lab 后端

本服务把 FreshRSS 新闻同步成 PostgreSQL 业务事实，用 LangChain 切分成 Chunk、经
Ollama ``bge-m3:567m`` 生成 Embedding 写入 Qdrant，并对外提供受登录保护的**只读**语义
检索接口。写入链路只有显式手动入口（CLI 与一个同步 HTTP 接口），没有调度器、常驻
Worker 或后台任务。

本文只讲怎么装、怎么跑、怎么调、怎么测。另外两份：

| 想知道什么 | 看哪里 |
| --- | --- |
| 内部怎么实现、对外契约细节、错误码 | [`docs/architecture.md`](docs/architecture.md) |
| 某处为什么是这样、当时放弃了什么 | 平台根 [`docs/adr/`](../docs/adr/) |
| 已验证的 RSS 地址与 FreshRSS selector | [`docs/rss_sources.md`](docs/rss_sources.md) |

对外接口清单见 [`docs/architecture.md`](docs/architecture.md) 的「对外 HTTP 接口」，
或启动后访问 ``/docs``。

## 外部依赖

服务自身不可独立运行，需要四个外部依赖：

```text
PostgreSQL   业务事实、账号与登录 Token。独立 Database news_vector_lc，
             migration head b7e1a4c9d203。必须先执行 alembic upgrade head。
FreshRSS     唯一的新闻来源。动态网页回源和站点 CSS selector 由它负责，
             Python Pipeline 里不能加站点判断。
Ollama       bge-m3:567m，1024 维。query 与 document 使用同一模型，
             换模型等于换索引空间（必须提升 schema_version 并重建）。
Qdrant       Point 存储。current Alias 必须由部署预先准备，搜索不会创建它。
```

Python 版本固定 ``>=3.12,<3.13``。关键依赖当前解析版本：fastapi 0.141.1、
fastapi-users 15.0.5、langchain 1.3.15、langchain-ollama 1.1.0、qdrant-client 1.19.0。

## 启动前置条件

```text
1. alembic upgrade head 已完成          （启动不执行 migration）
2. Qdrant current Alias 已存在          （搜索不会 ensure_ready）
3. .env 配置合法                         （启动即读，非法配置直接失败）
```

应用启动**只**访问 PostgreSQL 同步环境托管管理员，不探测 FreshRSS、Ollama 或 Qdrant，
也不创建 Collection 或 Alias。真正的 Embedding 与 current Alias query 只在收到请求时
执行；新闻同步与索引只在手动 CLI 或 ``POST /pipeline/run-once`` 时发生。

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
QDRANT_DISTANCE         改这个或维度必须新建 Schema/Collection，不能原地改。
```

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

四个 CLI 子命令（``agent-lab``）都是显式、一次性、有界的：

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
```

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

只有 3 个测试受环境变量门控，默认跳过。仅在明确允许访问当前 ``.env`` 指向的服务时启用；
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

按主题挑选离线测试：

```powershell
# 只读搜索编排、过滤与 Runtime 组装（fake Embeddings + 内存 Qdrant）
uv run pytest -q tests/test_vector_search.py tests/test_qdrant_runtime.py

# HTTP 接口契约与错误映射（fake Runtime + httpx ASGITransport）
uv run pytest -q tests/test_vector_search_api.py tests/test_document_search.py `
  tests/test_documents_api.py

# 错误契约的跨表不变量与全仓库 detail 文案（纯静态，不起 app）
uv run pytest -q tests/test_error_contract.py

# 认证、权限边界与账号管理契约
uv run pytest -q tests/test_auth.py tests/test_user_admin.py

# 手动写入链路：CLI、批次执行 Service 与流水线 API
uv run pytest -q tests/test_cli.py tests/test_news_pipeline_execution.py `
  tests/test_pipeline_api.py

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

## 生产前置要求

生产必须使用 HTTPS 与 Secure Cookie，并在网关限制登录频率、请求体大小、并发和 timeout
——服务本身不做这些。部署步骤见平台根目录
[`docs/vps_deployment.md`](../docs/vps_deployment.md)。

数据库迁移不能放进每个 FastAPI Worker 的启动流程：部署时先由单独步骤执行
``alembic upgrade head``，成功后再启动应用。

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
