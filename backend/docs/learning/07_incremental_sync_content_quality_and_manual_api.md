# 阶段 6：可靠增量同步、新闻内容质量与手动执行 HTTP API

## 本阶段解决什么

阶段 5 已经能手动执行 ``sync-news``、``index-pending`` 和 ``run-once``，但“每次读取
最近 N 篇”只能限制单次工作量：如果两次运行之间到达超过 N 篇新闻，较早的新文章会被
窗口挤掉。阶段 6 解决三个直接相关的问题：

1. 用 FreshRSS continuation 和来源级 checkpoint 可靠追赶两次执行之间的新文章；
2. 在 content_hash、revision、DocumentBuilder 和 Chunker 之前得到幂等的干净正文；
3. 提供同步、有界的 ``POST /pipeline/run-once``，复用现有执行 Service。

本阶段仍然没有自动调度、Cron、常驻 Worker、消息队列、asyncio 后台 Task、SSE、
WebSocket、生成式 LLM、Retriever 或 RAG，也没有 ``pipeline_runs`` 表。

## 手动执行与自动执行

“有 HTTP 路由”不等于“自动任务”。当前两个组合入口都由操作者显式触发：

```text
CLI:  uv run agent-lab run-once ...
HTTP: POST /pipeline/run-once
```

二者都只运行一轮，并受三个参数限制：

| 参数 | 默认 | 范围 | 约束对象 |
|---|---:|---:|---|
| ``limit_per_source`` | 2 | 1..100 | 每个来源本次最多同步的新闻数 |
| ``batch_size`` | 20 | 1..1000 | 本次最多读取的索引候选数 |
| ``stale_after_minutes`` | 60 | 1..10080 | processing lease 回收阈值 |

HTTP 请求会一直等待这一轮结束；没有 202、任务 ID、轮询或进度推送。自动执行则还需要
调度频率、并发排他、重试预算、进程退出恢复、部署所有权和运行历史等设计。当前没有
真实容量和运维边界，提前引入后台系统只会扩大失败面，因此保持手动同步执行。

## FreshRSS 实际分页契约

阶段 6 对照 FreshRSS 当前 edge 实现并在当前实例做了只读验证。Google Reader 兼容
``stream/items/ids`` 支持：

```text
n   单页数量
r=n 从新到旧
r=o 配合 continuation 从旧到新追赶
c   numeric continuation
ot 旧时间边界
nt 新时间边界
```

当前实例的响应包含 ``itemRefs`` 和字符串 ``continuation``；``itemRefs`` 实测只需要
``id``。IDs 接口的文章 ID 是十进制 entry ID，而 contents 中同一文章使用
``tag:google.com,2005:reader/item/<16位十六进制>``。实现把两者转换为同一数值比较键，
仍将 contents 的 tag ID 保存为既有 ``documents.external_id``。``stream/items/contents``
根据请求 ID 返回完整协议文章。FreshRSS continuation
是内部 entry ID，不是新闻发布时间、数组偏移量或 ``documents.external_id``，应用不能
自行猜测。当前实现只使用 ``n/r/c``，没有把时间戳当游标。

客户端要求：

- ``itemRefs[].id`` 非空且页内不重复；
- continuation 必须是 ASCII 十进制字符串并去除前导零；
- contents 返回的等价数值 ID 集合必须与请求页完全一致，随后恢复 ID 页顺序；
- 缺少、重复或额外正文会让整页失败，不能跳过后推进游标；
- HTTP、认证、连接、超时和协议错误使用不同异常类型，但不保存响应正文。

## checkpoint 为什么放在 sources

本阶段给 ``sources`` 增加两个可空列：

```text
sync_checkpoint             varchar(128)
sync_checkpoint_updated_at  timestamptz
```

选择来源表而不是独立同步状态表，理由是当前业务粒度正好是“一条 FreshRSS 订阅一个
游标”：

- ``sources(provider, external_id)`` 已经是来源业务唯一键；
- 每个来源只有一个当前 continuation，没有多消费者或多分区状态；
- 游标可与该来源一页文档在同一 PostgreSQL 事务提交；
- 不需要新增 join、清理生命周期、唯一约束或查询索引；
- 将来出现多提供方、多消费组或运行历史时，再用真实需求设计独立表。

两列都有中文 database comment。migration ``d2e6f4a8b1c3`` 的 ``upgrade`` 添加列，
``downgrade`` 只删除两列，不删除来源或文档。没有新增 Chunk 表、Embedding 表、
``pipeline_runs`` 表、索引或额外约束。

## 首次基线与后续追赶

### 首次同步

没有 checkpoint 时：

```text
1. n=1, r=n 读取执行开始时最新 marker
2. n=limit, r=n 读取最近一页
3. 严格读取并映射页内全部文章
4. 一页文档 + 最新 marker 在同一事务提交
```

首次运行有意只建立有界基线：比这一页更老的历史文章不在阶段 6 的可靠性承诺内。若要
全量回填，需要单独的历史导入模式和容量评估，不能偷偷取消安全上限。

### 已有 checkpoint

已有 checkpoint 时：

```text
1. 读取旧 checkpoint，结束短数据库读事务
2. n=1, r=n 读取本次执行开始时最新 marker
3. n=limit, r=o, c=old_checkpoint 从旧到新读取一页
4. 严格读取并映射页内全部文章
5. 满页时提交该页 continuation；不足一页时提交步骤 2 的 marker
```

先取 marker 再取数据页很重要。若请求期间又到新文章，它至多在下次被幂等重读，不会
被一个请求结束后才取得的更晚 marker 越过。积压超过 ``limit_per_source`` 时，一次只
前进一页，下一次继续：安全上限控制单次工作量，但不再变成丢新闻窗口。

## checkpoint 和事务

一个来源的一页是最小原子边界：

```text
FreshRSS IDs/contents
-> 全页协议校验
-> 全页 Mapper/质量规范化
-> source upsert
-> documents upsert
-> WHERE sync_checkpoint IS NOT DISTINCT FROM expected 的条件 UPDATE
-> COMMIT
```

只有 commit 成功，文档和 checkpoint 才同时可见。以下任一步失败都会 rollback，游标
保持旧值：

- FreshRSS 认证、连接、超时、HTTP 或协议响应；
- contents 缺少任一 ID；
- origin 与请求订阅不一致；
- 空标题、空正文、只剩标题或其他映射失败；
- source/document upsert；
- checkpoint UPDATE；
- PostgreSQL commit。

条件 UPDATE 防止两个手动执行从同一旧游标出发时，较晚提交的旧请求把新游标回退。
条件不匹配时文档仍可按业务键幂等提交，本次不报告 checkpoint 推进。

## 来源失败隔离

阶段 6 选择“单来源回滚，继续其他来源”：

- 每个来源失败后显式 rollback；
- 只记录 ``source_external_id`` 和 ``error_type``，不记录 ``str(exc)``；
- 继续下一白名单来源；
- 成功来源的新文档仍可进入本轮 ``index-pending``；
- CLI/HTTP 最终 ``ok=false``，不会把部分成功伪装成完整成功。

订阅列表失败无法建立来源边界，属于批次级失败，会终止同步。Qdrant lifecycle、配置或
候选读取等无法隔离的错误也返回批次级失败。

## 幂等保存和索引状态

文档继续以 ``source_id + external_id`` 唯一。重复读取同一文章时：

- 完全相同字段不会产生新行；
- ``updated_at``、``index_revision`` 和 processing 状态不变；
- 可索引输入真实变化才递增 revision；
- 同步只产生或保留 ``pending``，绝不直接标记 ``indexed``；
- ``processing`` 期间更新仍沿用旧 Worker/new revision 串行化规则。

checkpoint 只决定“下次从哪里读”，不表示文章已经向量化。Qdrant 成功快照仍只能由
``DocumentIndexingService`` 在完整 Point 写入后更新。

## 标题和正文为什么会重复

常见来源包括：

- Feed 的 ``title`` 同时又被网站模板放在正文第一个 ``h1``；
- 某些 Feed 把标题作为正文末尾署名块；
- ``summary`` 与 ``content`` 是同一内容的两种协议字段；
- HTML 抽取后，相邻容器产生两个完全相同文本块；
- entity、NBSP、全角/组合字符或缩进让肉眼相同文本的字节不同；
- 抓取模板错误，把同一段连续输出多次。

不能因此做“出现两次就删除”的宽泛规则。新闻正文可能有合法引述、排比、结论回顾或
重复句子；模糊相似度删除也很难解释和回放。

## 正文质量规则

``ContentQualityNormalizer`` 的输出是后续唯一正文输入，规则顺序固定且幂等：

1. HTML 由 BeautifulSoup 转成文本，script/style/noscript/template 被删除；
2. HTML entity 解码，Unicode 规范为 NFC；
3. 任意 Unicode 空白在段内压缩为普通空格，非空文本块沿用既有单换行连接；
4. 标题单独清除 HTML/entity 并稳定空白；
5. 只比较正文第一个和最后一个完整块与标题；
6. 标题比较键使用 NFKC/casefold，并忽略 Unicode 标点和空白；
7. 只删除相邻且规范化后字符串完全相同的完整段落；
8. 不删除正文中间标题、非相邻重复段落或单段内重复句子。

``content`` 和 ``summary`` 按 content 优先选择一个，从不拼接。这样相同的两个协议块
天然只保留一份；选中块内部仍使用同一规范化器。

边界行为：

| 输入 | 行为 |
|---|---|
| 空标题/仅 HTML 空白标题 | ``FreshRSSContentQualityError(reason=empty_title)`` |
| content 和 summary 都空 | ``reason=empty_content`` |
| HTML 清洗后没有文本 | ``reason=empty_content`` |
| 正文只有标题块 | ``reason=title_only`` |
| 正文是同一合法段落连续多份 | 保留第一份，统计删除数量 |
| 正文很短但不是标题 | 标为 ``content_too_short`` 诊断信号，仍允许合法短快讯保存 |
| 正文中一句合法重复 | 原样保留 |
| 无法确定是否重复 | 原样保留，不静默丢弃 |

质量异常不携带原始正文。因为整页失败，操作者可以修复来源或规则后从旧 checkpoint
重试，不会留下难以发现的新闻缺口。

## content_hash、revision 和 Chunk

数据顺序是：

```text
HTML/协议字段
-> ContentQualityNormalizer.normalized_text
-> documents.content_text
-> SHA-256 content_hash
-> index_revision 变化判断
-> DocumentBuilder.page_content
-> DocumentChunker
-> Embedding
```

相同输入或只含 entity/Unicode/空白差异的等价输入得到稳定 ``content_text`` 和
content_hash，因此不会无意义增加 revision。Document UUID 沿用 PostgreSQL 主键；
Chunk UUIDv5 由父 UUID、tokenizer、大小、重叠和最终 chunk_index 生成。

Chunker 在 LangChain 切分后：

- 丢弃空白 ``page_content``；
- 同文档完全重复的 ``page_content`` 只保留第一次；
- 基于最终列表重新生成 ``chunk_index/chunk_count``；
- 清理并重建 ``previous_chunk_id/next_chunk_id``；
- 相同规范正文和切分参数重复处理得到相同 Chunk UUID。

这不是跨来源新闻去重。两个 Feed 发布相同新闻仍是两个业务文档，除非以后有明确业务
模型和测试证明需要跨来源合并。

## 手动 HTTP API

请求：

```http
POST /pipeline/run-once
Content-Type: application/json
```

```json
{
  "limit_per_source": 2,
  "batch_size": 20,
  "stale_after_minutes": 60
}
```

成功或来源/单篇部分失败都返回 HTTP 200。示例：

```json
{
  "ok": false,
  "execution_mode": "manual",
  "sync": {
    "source_count": 3,
    "successful_source_count": 2,
    "failed_source_count": 1,
    "synchronized_document_count": 4,
    "checkpoint_advanced_count": 2,
    "failures": [
      {"error_type": "FreshRSSConnectionError", "count": 1}
    ]
  },
  "index": {
    "requeued_stale_document_count": 0,
    "candidate_document_count": 4,
    "indexed_document_count": 3,
    "skipped_document_count": 0,
    "failed_document_count": 1,
    "failures": [
      {"error_type": "OllamaTimeoutError", "count": 1}
    ]
  }
}
```

HTTP 响应按异常类型聚合，不返回来源 ID、文档 UUID、异常文本、新闻正文、完整 query、
Vector、密钥、数据库 URL 或第三方响应。参数错误返回已脱敏 422。批次级错误返回
``code/detail/error_type/retryable``，并区分：

- FreshRSS 认证、连接、超时、协议/服务错误；
- PostgreSQL ``SQLAlchemyError``；
- Ollama 认证、连接、超时、模型和响应错误；
- Qdrant lifecycle、配置和 Point Store 错误；
- Pydantic 配置错误和通用 timeout；
- 未知实现错误（统一脱敏 500）。

错误映射从不调用 ``str(error)``。来源级 PostgreSQL/Ollama/Qdrant 失败已经由执行
Service 变成 200 中的安全统计；无法建立批次结果时才使用 5xx。

## Write Runtime 与只读 Runtime

``PipelineWriteRuntime`` 按 HTTP 请求构造：

```text
PipelineWriteRuntime
    -> FreshRSSImportService
    -> NewsPipelineExecutionService
    -> DocumentIndexingRuntime
        -> Qdrant lifecycle
        -> DocumentIndexingService
        -> Point Store
```

路由等待 ``run_once`` 后在 finally 关闭 Runtime。FastAPI startup 只保存这个工厂，不
调用它，更不会执行同步。搜索继续使用：

```text
VectorSearchRuntime
    -> query Embedding
    -> QdrantVectorSearch
```

``VectorSearchRuntime`` 仍没有 lifecycle、Point Store、索引 Service、FreshRSS client
或 PostgreSQL Session。增加写路由没有扩大搜索接口自身的组件权限。

## 当前安全边界

平台后续已增加本地账号 Cookie 认证；Pipeline 要求启用且 `is_superuser=true` 的账号，
普通用户得到 403，匿名调用得到 401。开发仍建议显式绑定：

```powershell
uv run uvicorn agent_lab.main:app --host 127.0.0.1 `
  --loop agent_lab.runtime:selector_loop_factory
```

生产必须使用 HTTPS/Secure Cookie，并继续放在具有限流、请求体上限和 timeout 的网关
后；也可以完全不向公网路由 Pipeline。``OLLAMA_API_KEY``、``QDRANT_API_KEY`` 和
FreshRSS 密码仍只是服务访问上游的凭据，不是 HTTP 客户端认证。实现细节见
[`08_local_password_auth.md`](08_local_password_auth.md)。

## 为什么不做后台任务、WebSocket、LLM 或 RAG

- 同步 HTTP 已有三个上限，可以先测量真实耗时和超时；
- 后台任务需要持久运行状态、幂等重投、取消、恢复和 ``pipeline_runs`` 设计；
- WebSocket/SSE 只解决进度传输，不解决任务可靠性；
- Scheduler/Worker 会引入部署所有权和并发容量问题；
- LLM/RAG 属于回答生成路径，与新闻完整性和写入可靠性无关；
- 当前先把可重建的索引事实做正确，比提前扩展运行模型更重要。

若真实同步请求无法在网关 timeout 内完成，应先收集来源数、页数、p95/p99、失败率和
重试数据，再停止并设计异步任务；不能仅把 ``asyncio.create_task`` 放进路由。

## 常见故障

### checkpoint 长时间不变

查看安全失败 ``error_type``。若某来源请求、映射或数据库写入失败，这是预期保护；修复
后重跑会从旧 checkpoint 继续。不要手工把游标改到最新值来“消除错误”，那会跳新闻。

### 每次只同步上限数量

存在 backlog 时这是正常行为。重复手动运行会沿 checkpoint 每次前进一页。不要为了
一次清空积压无限增大 ``limit_per_source``；先考虑 FreshRSS、数据库和索引容量。

### FreshRSS response invalid

检查当前 FreshRSS 版本是否仍返回 ``itemRefs[].id`` 和 numeric continuation，以及
contents 是否完整返回请求 ID。不要用发布时间、列表偏移或 external_id 猜游标。

### title_only 或 empty_content

检查 Feed 的 content/summary、FreshRSS 抓取规则和站点 selector。质量异常会阻止整页
推进，避免坏文章被静默越过。不要用宽泛正则删除标题或把空正文标成 indexed。

### PostgreSQL unavailable

确认 ``DATABASE_URL`` 指向 ``news_vector_lc``，执行 ``alembic current`` 和
``alembic check``，并确认 migration head 为 ``d2e6f4a8b1c3``。连接错误响应不会回显
URL。

### Qdrant lifecycle 或 Alias conflict

确认 current Alias 为 ``news_chunks_langchain_current``，目标属于 langchain 命名空间。
生命周期组件不会自动覆盖意外 Alias；修复后可单独运行 ``index-pending``。

### Ollama timeout 或 model not found

确认模型为 ``bge-m3:567m``、真实维度为 1024，并检查请求 timeout 与服务负载。失败
文档保持可重试状态；API 只返回异常类型和数量。

### HTTP 504

区分 FreshRSS、Embedding 和通用 timeout。当前请求是同步的，网关 timeout 必须大于
合理的一轮上限；不能通过后台 Task 在响应后继续悄悄写入。

## 离线测试与验证

阶段 6 聚焦测试：

```powershell
uv run pytest -q tests/test_freshrss_incremental_sync.py `
  tests/test_content_quality.py tests/test_pipeline_api.py
```

它们使用内存 continuation、commit/rollback 暂存区、fake Runtime 和 httpx
ASGITransport，覆盖分页不漏、幂等重跑、游标提交顺序、来源隔离、边界标题、段落重复、
Unicode/HTML、空正文、Chunk 关系、参数边界、startup 零写入、Service 复用和错误脱敏。

完整验证顺序：

```powershell
uv sync --all-groups
uv run pytest -q
uv run python -m compileall -q src tests alembic
uv lock --check
uv run alembic current
uv run alembic check
```

真实验证只能使用 ``news_vector_lc`` 和 ``news_chunks_langchain_*``，从小上限开始；输出
只保留数量、状态、UUID 或异常类型，不打印密钥、新闻正文或完整 Vector。

## 完成标准

- ``limit_per_source`` 是安全上限而不是会丢新闻的最近窗口；
- 文档与 checkpoint 同一事务提交，任何页内失败不推进；
- 一个来源失败继续其他来源，最终明确部分失败；
- ``source_id + external_id``、revision 和 processing 并发语义不变；
- 正文规范化发生在 content_hash 和所有 LangChain 处理之前且幂等；
- 不误删正文中的合法重复句子，不做跨来源去重；
- Chunk 非空、同文档正文唯一且关系字段一致；
- HTTP API 同步、有界、类型化、脱敏，并复用执行 Service；
- startup 不自动运行，搜索 Runtime 保持只读；
- migration 可 upgrade/downgrade，所有新列有中文 comment；
- 没有新增依赖、Chunk/Embedding/pipeline_runs 表；
- 没有自动调度、Worker、后台 Task、WebSocket/SSE、LLM 或 RAG。
