# 阶段 5：新闻同步与向量索引执行入口

> 本章保留阶段 5 的 CLI 设计背景。阶段 6 已在不改变 CLI 有界语义的前提下加入来源
> checkpoint、内容质量规范化和同步 ``POST /pipeline/run-once``；新增行为见
> [阶段 6 学习文档](07_incremental_sync_content_quality_and_manual_api.md)。

## 本阶段目标和非目标

阶段 0 到阶段 4 已经分别实现新闻导入、Document/Chunk、Embedding、Qdrant 索引、
Vector Search 和只读 HTTP API，但此前只能由 Python 调用方手工组装写入 Service。
阶段 5 增加三个一次性 CLI（命令行接口）入口：

```text
sync-news
index-pending
run-once
```

它们把既有组件串成可由开发者、部署脚本或未来 Scheduler 调用的有界任务。本阶段不
实现：

- FastAPI 写入接口（这是阶段 5 当时的非目标，阶段 6 已增加同步手动入口）；
- 定时器、Cron 配置或常驻 Worker 无限循环；
- WebSocket 触发或进度推送；
- 消息队列、分布式任务平台或自动重试风暴；
- 生成式 LLM、RAG、Retriever、Agent、Hybrid Search 或 reranker。

## 它与搜索接口的区别

阶段 5 是 write path（写入路径）：

```text
FreshRSS -> PostgreSQL -> Chunk/Embedding -> Qdrant
```

阶段 4 的 `POST /vector-search` 是 read path（读取路径）：

```text
query -> query Embedding -> Qdrant current Alias -> 搜索结果
```

搜索请求不会抓取新闻、创建 Collection 或修改 `processing_status`；写入命令也不执行
Vector Search。拆开两条路径可以分别控制权限、timeout、失败重试和资源容量。

## 两次“入库”分别保存什么

新闻执行流程会写两个不同的数据系统：

| 存储 | 保存内容 | 不保存 |
|---|---|---|
| PostgreSQL | `sources`、完整 `documents`、revision 和处理状态 | Chunk、Vector、Embedding 表 |
| Qdrant | Chunk Point、1024 维 Vector、扁平新闻/Chunk Payload | PostgreSQL 事务和业务关系 |

PostgreSQL 是新闻业务事实来源；Qdrant 是可从 PostgreSQL 正文和索引规格重建的派生
检索副本。

## 三个命令

安装项目后可查看帮助：

```powershell
uv run agent-lab --help
```

### sync-news

```powershell
uv run agent-lab sync-news --limit-per-source 2
```

调用流程：

```text
FreshRSS 分类白名单
-> 每个允许订阅读取执行边界 marker
-> 首次最近一页，之后按 checkpoint 从旧到新读取最多 N 篇
-> FreshRSSItemMapper
-> SourceDocument
-> PostgreSQL sources/documents 幂等 upsert + checkpoint 原子提交
-> 新建或索引输入变化的新闻进入 pending
```

`limit-per-source` 默认 2、范围 1..100。它是每个 Feed 每次执行的安全上限，不是只看
最近 N 篇的窗口。阶段 6 使用 FreshRSS numeric continuation 分页追赶；成功提交一页后
才推进来源 checkpoint，因此两次执行之间超过 N 篇的新文章会分多次读取而不会越过。
命令只访问 FreshRSS 和 PostgreSQL，不构造 Ollama/Qdrant Runtime。

输出中的 `synchronized_documents` 是经过幂等保存流程的文档数，可能包含新增、实际
更新和完全相同的已有新闻。当前 Repository 没有可靠区分三种结果，因此不能把它误称
为“新增数”。

### index-pending

```powershell
uv run agent-lab index-pending `
  --batch-size 20 `
  --stale-after-minutes 60
```

调用流程：

```text
显式 ensure_ready()
-> 创建或校验当前物理 Collection、Payload index 和 current Alias
-> 回收超过 stale lease 的 processing 任务
-> 读取一批 pending/failed UUID
-> 每篇原子 claim 为 processing
-> Document -> Chunk -> Ollama Embedding
-> Qdrant current Alias replace/upsert
-> indexed 或 failed
```

`batch-size` 默认 20、范围 1..1000。一次命令只读取这一批，不在末尾继续查询下一批，
因此运行时间和外部请求量有明确上限。若 backlog（积压任务）超过一批，应由操作者或
未来 Scheduler 再次执行命令。

`stale-after-minutes` 默认 60、范围 1..10080。它只回收开始时间早于阈值且仍处于
`processing` 的任务。该值应明显大于正常 Chunk/Embedding/Qdrant 最大耗时，避免仍在
工作的任务被重复投递。

### run-once

```powershell
uv run agent-lab run-once `
  --limit-per-source 2 `
  --batch-size 20 `
  --stale-after-minutes 60
```

它严格按以下顺序执行一次：

```text
sync-news
-> index-pending
-> 输出一个 JSON 摘要
-> 进程退出
```

订阅列表等批次级同步失败时不创建 Qdrant Runtime。单个来源失败时，阶段 6 会回滚该
来源页、保持 checkpoint、继续其他来源，并仍索引成功来源；最终 `ok=false`。如果同步
完成但 Qdrant 不可用，新闻仍安全保留在 PostgreSQL `pending`，下次
`index-pending` 可以重试。

## 为什么第一版顺序处理

第一版对候选逐篇顺序执行，不设置协程并发：

1. `AsyncSession` 不能由多个并发 Task 共享；
2. 远程 Ollama/Qdrant 的并发容量还没有实际测量；
3. 顺序执行更容易核对状态、失败和外部写入；
4. 一个批次已经可以由多个独立进程竞争执行，原子 claim 会阻止同 revision 重复处理。

未来增加进程内并发时，每篇必须继续创建独立 Session，并设置明确 semaphore 上限，
不能直接对一个共享 Session 使用 `asyncio.gather()`。

## PostgreSQL 事务边界

阶段 6 的同步事务按来源分隔：先用短读事务取得旧 checkpoint 并结束事务，再访问
FreshRSS；一页全部请求、映射成功后，在同一事务 upsert 来源/文档并条件推进 checkpoint。
网络、映射、upsert 或 commit 任一步失败都会 rollback，绝不越过坏文章。另一个并发
执行若已推进游标，条件 UPDATE 不允许旧请求把 checkpoint 回退。

执行器不会在 Embedding 期间保持长事务：

```text
候选 SELECT
-> Session 关闭

单篇 get + claim UPDATE + COMMIT
-> Ollama/Qdrant 网络 I/O（没有未提交 PG 事务）
-> indexed/failed UPDATE + COMMIT
-> Session 关闭
```

候选列表不是锁。另一个进程可能先领取其中某篇；当前进程随后 claim 失败并计入
`skipped_documents`，不会执行 Chunk、Embedding 或 Qdrant 写入。

## 单篇失败与批次失败

单篇索引失败时：

- `DocumentIndexingService` 尽力把当前 revision 标为 `failed`；
- 执行器只记录 document UUID 和 Python 异常类型；
- 不调用 `str(exc)`，避免第三方响应、正文或凭据进入日志；
- 继续处理本批后续候选；
- 最终 JSON 中 `ok=false`、`failed_documents>0`，进程退出码为 1。

候选读取、stale requeue、配置或 Qdrant lifecycle 等批次级错误会立即终止命令。最外层
也只输出异常类型，不输出原始异常文本。退出码契约：

| 情况 | 退出码 |
|---|---:|
| 完整成功或零候选 | 0 |
| 来源级同步失败、单篇索引失败或命令级外部错误 | 1 |
| CLI 参数非法 | 2 |

## Qdrant lifecycle 边界

`sync-news` 不接触 Qdrant。`index-pending` 和 `run-once` 在领取 PostgreSQL 候选前调用
一次 `DocumentIndexingRuntime.ensure_ready()`：

- 预期 Collection 不存在时可以创建；
- 已存在时校验 dimension、Distance 和 metadata；
- current Alias 不存在时创建；
- Alias 指向意外目标时拒绝，不自动覆盖；
- 不自动删除、重建或切换一个冲突的生产索引。

Point 写入仍只访问 current Alias；只有 lifecycle 组件能看到物理 Collection 名。

## 当前隔离配置

本项目使用独立 PostgreSQL Database：

```text
news_vector_lc
```

当前 migration head：

```text
d2e6f4a8b1c3
```

Qdrant 使用独立环境名：

```text
QDRANT_ENVIRONMENT=langchain
```

生成：

```text
news_chunks_langchain_v1_001
news_chunks_langchain_current
```

修改 `.env` 后必须重新启动 CLI/FastAPI 进程；Settings 和连接池在进程内缓存，运行中
修改文件不会改变已经创建的 Runtime。

## 输出示例

成功但没有候选：

```json
{
  "command": "index-pending",
  "failed_documents": 0,
  "failures": [],
  "index_candidates": 0,
  "indexed_documents": 0,
  "ok": true,
  "requeued_stale_documents": 0,
  "skipped_documents": 0
}
```

失败明细只包含：

```json
{
  "document_id": "00000000-0000-0000-0000-000000000000",
  "error_type": "OllamaTimeoutError"
}
```

不会输出完整新闻、Vector、API Key、数据库 URL 或第三方响应正文。

## 离线测试

阶段 5 聚焦测试：

```powershell
uv run pytest -q tests/test_news_pipeline_execution.py tests/test_cli.py
```

测试全部使用 fake：

- fake FreshRSS Import Service；
- fake Session factory 和 Repository；
- fake Document Indexing Service；
- fake Qdrant Runtime lifecycle。

阶段 6 另外使用内存 continuation 与 commit/rollback 暂存区验证分页不漏、幂等重跑、
checkpoint 只在成功事务后推进和单来源失败隔离；这些测试仍完全离线。

它验证参数边界、独立 Session、批次上限、stale cutoff、claim skip、单篇失败继续、异常
文本脱敏、`ensure_ready -> index -> close` 顺序和进程退出码，不访问任何真实服务。

## 真实验证顺序

真实环境只按明确步骤执行：

1. `alembic current/check` 确认独立数据库；
2. `sync-news --limit-per-source 1` 小批导入；
3. 核对 PostgreSQL 文档数量和 `pending` 状态；
4. `index-pending --batch-size 1` 小批生成 Vector；
5. 核对 PostgreSQL `indexed` 和 Qdrant current Alias Point；
6. 再用 `run-once` 验证组合入口和幂等行为。

真实验证会修改本项目独立 PostgreSQL 和 `langchain` Qdrant 命名空间，但不会连接旧
`news_vector` Database 或 `news_chunks_dev_*` 索引。

## 常见故障

### PostgreSQL database does not exist

Alembic 只能在已存在的 Database 内创建表，不能创建 Database 本身。先由 PostgreSQL
管理员创建 `news_vector_lc`，再执行 `alembic upgrade head`。

### Alias conflict

`news_chunks_langchain_current` 已指向其他物理 Collection 时，生命周期组件会拒绝继续。
先确认另一项目是否使用同名 Alias，不要让命令自动覆盖。

### Qdrant lifecycle 失败但新闻已同步

这是可恢复状态。新闻留在 PostgreSQL `pending`；修复 Qdrant URL、代理、证书、API Key
或 Alias 后重新执行 `index-pending`。

### HTTPS 反代被错误请求到 6333

`qdrant-client==1.19.0` 的默认 `port=6333` 会覆盖无显式端口的完整 HTTPS URL。项目统一
builder 已传 `port=None`，使 `https://qdrant.example.com` 使用 443。不要在单个 CLI
里重新手工构造另一个 client，否则同步、索引和搜索可能访问不同端口。

### 每次运行都重试 failed

当前候选包含 `pending/failed`。这是第一版明确语义，便于暂时性上游错误恢复。长期、
确定性的内容错误需要后续引入最大尝试次数或人工隔离状态，不能在没有字段和审计设计
时静默丢弃。

## 完成标准

- 三个 CLI 子命令可通过项目 entry point 调用；
- 同步和索引依赖按职责分开构造；
- 每来源、每批次和 stale lease 有默认值及最大边界；
- 一次命令不会无限处理 backlog；
- 候选逐篇使用独立 Session；
- Qdrant lifecycle 发生在领取任务之前；
- 单篇失败继续批次但最终返回非零退出码；
- 输出不包含正文、Vector、凭据或原始异常文本；
- 独立 PostgreSQL/Qdrant 命名得到真实验证；
- 默认测试完全离线；
- 没有实现自动调度、WebSocket、常驻 Worker、LLM 或 RAG。

## 阶段 6 手动 HTTP 衔接

``POST /pipeline/run-once`` 复用本章 ``NewsPipelineExecutionService`` 和相同三个参数，
但通过独立 ``PipelineWriteRuntime`` 按请求组装写组件。它是同步 200/5xx 请求，不返回
202 或任务 ID，不创建 ``pipeline_runs`` 表，也不在 FastAPI startup 自动执行。搜索
``VectorSearchRuntime`` 仍然只读，不含 lifecycle、Point Store 或索引 Service。

平台后续已要求超级用户 Cookie 才能调用 HTTP Pipeline；开发仍绑定 ``127.0.0.1``，
生产使用 HTTPS 并放在限流和 timeout 网关后，也可不向公网路由该入口。认证设计见
[`08_local_password_auth.md`](08_local_password_auth.md)，请求/响应和错误排查见阶段 6 文档。

## 后续阶段可能做什么

先观察真实执行次数、每批耗时、失败类型和服务容量，再讨论：

- Scheduler 定时调用 `run-once`；
- 常驻 Worker 与优雅停止；
- WebSocket/SSE 只推送任务进度；
- 有界并发、最大尝试次数和人工失败队列；
- 不记录正文的指标与告警。

这些能力不会改变当前 CLI 的一次性语义，也不应与生成式回答混在同一个阶段实现。
