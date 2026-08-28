# 阶段 2B：单篇新闻索引流水线与 processing_status

## 本文解决什么问题

Qdrant 能保存 Vector，不代表一篇新闻已经可靠完成索引。一次任务还需要回答：

- 谁负责依次调用 Chunk、Ollama 和 Qdrant？
- 哪一步成功后才能把新闻标成 `indexed`？
- 处理中 FreshRSS 又更新正文怎么办？
- 新 Chunk 数量减少后，旧 Point 谁来删除？
- Worker 崩溃后长期停在 `processing` 怎么恢复？

这些问题由 `DocumentIndexingService` 和 PostgreSQL 索引状态字段解决。

## processing_status 的业务含义

PostgreSQL 是新闻业务事实来源，Qdrant 是可重建副本。`processing_status` 表示当前
新闻版本的 Qdrant 副本处理到哪一步：

| 状态 | 中文解释 |
|---|---|
| `pending` | 当前版本需要写入 Qdrant，还没有被 Worker 领取 |
| `processing` | Worker 已领取，正在切分、Embedding 或写 Qdrant |
| `indexed` | 当前版本 Point 已全部写入，旧尾部 Point 也已清理 |
| `failed` | 当前版本处理失败，需要排查或再次领取 |

只有 `DocumentIndexingService` 串接全部步骤并修改状态。各底层组件职责：

```text
DocumentBuilder          ORM -> LangChain Document
DocumentChunker          Document -> Chunk
OllamaEmbeddingProvider  page_content -> Vector
QdrantChunkStore         Point upsert/scroll/delete，仅走 Alias
DocumentRepository       PostgreSQL 状态条件更新
DocumentIndexingService  总编排
```

Ollama 成功不代表 Qdrant upsert 成功；Qdrant upsert 成功也不代表旧 Point 已清理。
因此底层组件不能根据自己的一步成功就把新闻标成 `indexed`。

## documents 表新增字段

阶段 2 migration 没有建立 Chunk 或 Embedding 表，只在 `documents` 增加：

| 字段 | 是否可空 | 业务含义 |
|---|---:|---|
| `index_revision` | 否，默认 1 | 当前新闻应该被索引的版本号 |
| `indexed_revision` | 是 | 最近一次 Qdrant 完整成功的版本号 |
| `indexed_content_hash` | 是 | 最近一次成功版本的正文 SHA-256 |
| `indexed_schema_version` | 是 | 最近一次成功使用的 VectorIndexSpec 版本 |
| `processing_started_at` | 是 | 当前 Worker 开始处理的时间 |
| `indexed_at` | 是 | 最近一次完整成功的时间 |
| `last_processing_error` | 是 | 脱敏且最多 1000 字符的失败说明 |

`index_revision` 和非空 `indexed_revision` 有 PostgreSQL CheckConstraint，必须大于
等于 1。`indexed_*` 允许为空，因为新导入新闻还没有成功的 Qdrant 副本。

对应实体是 `models/document.py::DocumentRecord`，migration 是
`7f21b2f64718_增加文档向量索引状态_add_document_vector_index_state.py`。

## index_revision（索引版本号）

可以把 `index_revision` 理解成“这篇新闻的待索引版本号”：

```text
首次写入 PostgreSQL：revision 1
标题变化：revision 2
正文变化：revision 3
发布时间变化：revision 4
标签变化：revision 5
```

以下会进入 Chunk、Embedding 或 Payload 的字段发生变化时都会递增 revision：

```text
document_type
title
url
published_at
source_updated_at
authors
labels
content_hash
```

`image_urls` 只保存在 PostgreSQL，当前不进入 Chunk、Embedding 或 Payload；图片变化
会更新业务记录，但不会递增 revision、重置 processing_status 或重新调用 Ollama。

来源名称变化也会让该来源下的文档递增 revision，因为 `source_name` 会进入 Payload。
完全相同的 FreshRSS 重复同步不会 UPDATE，不改变 `updated_at` 或 revision。

对应代码是 `repositories/document_repository.py::upsert()` 和
`repositories/source_repository.py::_mark_documents_for_reindex()`。

## 为什么还需要 content_hash

revision 能表示“索引输入变了”，`content_hash` 能证明 Worker 实际处理的是哪份正文。
最终标记 `indexed` 时同时检查：

```text
document_id 相同
index_revision 相同
content_hash 相同
processing_status 仍为 processing
```

全部成立才写入 indexed 字段。这样即使调用方携带了错误的旧 ORM 快照，也不能把新
正文误标成已索引。

## 单篇正常处理流程

```text
1. 根据 document_id eager-load DocumentRecord + source
2. 原子领取 revision：pending/failed -> processing
3. 提交领取事务
4. DocumentBuilder -> LangChain Document
5. DocumentChunker -> Chunk[]
6. OllamaEmbeddingProvider.embed_chunks()
7. 确认真实维度 == VectorIndexSpec.dimension == 1024
8. QdrantChunkStore 通过 current Alias 读取该 document_id 的旧 Point ID
9. 按批 upsert 当前全部 Point，wait=true
10. 删除 existing_ids - current_ids，wait=true
11. 条件更新 processing -> indexed
```

正常非空新闻至少应生成一个 Chunk。如果 Pipeline 返回空列表，Service 会在 Ollama 和
Qdrant 之前失败并标记当前 revision 为 `failed`；它不会把空结果解释成“删除全部旧
Point”，避免切分缺陷造成新闻索引被静默清空。

第 3 步先提交领取事务很重要。Ollama 和 Qdrant 是远程网络 I/O，可能持续数秒；如果
一直持有未提交数据库事务，会占用连接和行锁，降低服务吞吐。

调用入口是：

```python
result = await indexing_service.index_document(session, document_id)
```

生产标准组件由 `DocumentIndexingRuntime.build(qdrant_settings, ollama_settings)` 组装。
启动准备步骤先显式调用 `await runtime.ensure_ready()` 校验 Collection 和 Alias；进程
关闭时调用 `await runtime.close()` 释放 Ollama 与 Qdrant 连接池。

```python
from agent_lab.config.ollama_embedding import (
    get_ollama_embedding_settings,
)
from agent_lab.config.qdrant import get_qdrant_settings
from agent_lab.qdrant.runtime import DocumentIndexingRuntime

runtime = DocumentIndexingRuntime.build(
    get_qdrant_settings(),
    get_ollama_embedding_settings(),
)
try:
    # 这是显式 Qdrant 生命周期 I/O；import 模块本身不会修改外部状态。
    await runtime.ensure_ready()
    result = await runtime.service.index_document(session, document_id)
finally:
    await runtime.close()
```

实际应用应在进程内复用 Runtime，而不是每篇新闻都重新创建 HTTP client。PostgreSQL
`AsyncSession` 仍然是一项工作单元独占，不能跨并发任务共享。

该方法自己使用 `selectinload` 读取 `source` relationship，调用方不需要了解 ORM
eager-loading 细节。

## claim（原子领取）

候选列表只表示“看起来可以处理”，可能在 Worker 真正开始前已经被其他 Worker 领取。
`claim_for_indexing()` 使用一条条件 UPDATE：

```text
WHERE id = document_id
  AND index_revision = expected_revision
  AND processing_status IN (pending, failed)
```

只有更新一行才表示领取成功。两个 Worker 同时领取时，最多一个成功；另一个返回
`skipped=True`，不会继续调用 Ollama 或 Qdrant。

## upsert（存在则更新，不存在则插入）

稳定 Chunk UUID 让重试幂等：

```text
Point ID 已存在 -> 更新 Vector 和 Payload
Point ID 不存在 -> 插入新 Point
```

相同新闻版本重跑不会产生第二套 Point。

## 为什么还要删除旧 Point

一篇新闻旧版可能有 4 个 Chunk，新版只有 2 个：

```text
旧：chunk-0, chunk-1, chunk-2, chunk-3
新：chunk-0, chunk-1
```

upsert 只更新前两个，不会自动知道后两个已经消失。Store 先读取旧 ID，再计算：

```text
stale_ids = existing_ids - current_ids
```

成功 upsert 当前 Point 后，再删除 stale IDs。先 upsert 后删除的理由是：如果写入中途
失败，宁可短暂保留旧尾部 Point，也不先删除正常数据造成更大空窗。

如果 upsert 成功但删除失败，任务进入 `failed`。重试会再次 upsert 相同 ID，然后
继续计算并删除旧 ID，不需要人工修复重复 Point。

对应代码是 `qdrant/store.py::replace_document_chunks()`。

## 处理中新闻又更新怎么办

最危险的竞态是：

```text
旧 Worker 正在处理 revision 1
FreshRSS 写入 revision 2
新 Worker 先写完 revision 2
旧 Worker 最后写完 revision 1
```

如果两个版本并发写相同 Chunk UUID，旧版可能最后覆盖新版。当前项目使用“单文档
revision 串行化”：

```text
旧 Worker 领取 revision 1 -> status processing
FreshRSS 更新 -> revision 变 2，但 status 暂时保持 processing
新 Worker 无法领取，因为 status 不是 pending/failed
旧 Worker 完成时，mark_indexed(revision 1) 条件失败
旧 Worker 调用 release_stale_claim -> status pending
新 Worker 才能领取 revision 2
```

这样不需要在长时间网络 I/O 中持有 PostgreSQL 行锁，也不会让新旧版本并发覆盖同一
Point。

## Worker 崩溃怎么办

Worker 在 `processing` 后进程崩溃时，无法执行 release 或 failed。
`processing_started_at` 用于识别失联任务。Repository 提供：

```python
await repository.requeue_stale_processing(started_before=threshold)
```

它把明显早于正常任务最大时长的 `processing` 重置为 `pending`。阈值必须明显大于
正常 Ollama + Qdrant 处理时长，否则可能把仍在工作的任务重复投递。本阶段只提供
方法，没有提前实现后台调度器或自动重试循环。

## 失败时保存什么

当前 revision 任一步失败：

```text
processing -> failed
last_processing_error = 脱敏、限长说明
```

项目自己的边界异常已经移除 URL 凭据、API Key 和远程响应正文，可以保存安全消息。
未知第三方异常只保存：

```text
ExceptionType: indexing operation failed
```

不会保存：

- API Key；
- Authorization header；
- 完整新闻正文；
- 完整 Vector；
- 可能携带敏感内容的远程 response body。

记录 failed 状态本身也可能因 PostgreSQL 故障失败。Service 使用 Python
`Exception.add_note()` 给原始 Ollama/Qdrant 异常附加说明，但不覆盖根因。

## Qdrant 成功但 PostgreSQL 更新失败

两个独立系统无法共享一个数据库事务。当前策略依赖 Qdrant 幂等写入：

```text
Qdrant 成功
PostgreSQL indexed 更新失败
任务以后再次运行
稳定 Point ID -> upsert 覆盖同一 Point
旧 ID 清理再次计算
```

因此不会产生重复 Point。PostgreSQL 只有在最终条件 UPDATE 成功后才声明 `indexed`。

## ORM identity map 注意点

PostgreSQL `ON CONFLICT ... RETURNING` 已经返回新 revision，但同一 AsyncSession 可能
缓存旧 ORM 对象。若不刷新，Python 仍会读到旧 revision。

Repository 使用：

```python
.execution_options(populate_existing=True)
```

让 RETURNING 结果覆盖 identity map 的旧属性。真实 PostgreSQL 集成测试已经验证
同一 Session 内 revision 1 -> 2 的行为。

## 全量 Collection 重建

单篇 upsert/删除不是全量重建。只有模型、维度、Distance、切分规则或不兼容 Payload
契约变化时，才执行：

```text
创建 news_chunks_prod_v2_001
    -> 全量索引 PostgreSQL 新闻
    -> 校验数量、规格和抽样数据
    -> 原子切换 news_chunks_prod_current Alias
    -> 保留旧 Collection 作为回滚窗口
    -> 人工确认后删除旧 Collection
```

当前 lifecycle 已提供创建、校验、Alias 切换和防误删能力，但没有提前实现全量重建
调度平台。

## 测试方法

默认测试完全离线：

```powershell
uv run pytest -q
```

它使用 fake 状态组件和真实内存 Qdrant，不访问 Ollama、PostgreSQL 或远程 Qdrant。

PostgreSQL 的 revision 版本语义由 `tests/test_document_indexing_service.py` 用 fake 状态组件
覆盖，不需要真实数据库。

如果 `.env` 已配置可用的远程 Qdrant，并明确允许创建临时测试资源，可运行：

```powershell
$env:RUN_QDRANT_REMOTE_INTEGRATION_TEST="1"
uv run pytest -q tests/test_qdrant_remote_integration.py
```

该测试使用随机隔离的 Collection/Alias 名称并在 `finally` 清理。没有显式开关时绝不
修改远程 Qdrant；连接失败时也不能报告伪造的成功结果。

## 阶段 3 已完成的只读衔接

当前 ``VectorSearchService`` 已在独立只读边界中完成：

- query 只调用 ``OllamaEmbeddingProvider.embed_query``；
- query Vector 通过 ``VectorIndexSpec`` 校验后查询 Qdrant current Alias；
- 支持 Top-K、可选 score threshold、来源/类型/标签/发布时间过滤；
- 返回按 Qdrant score 排序的 Pydantic Chunk 结果；
- 不读取或修改 PostgreSQL ``processing_status``；
- 不调用索引 lifecycle 或任何 Qdrant 写方法。

完整说明见 [`04_vector_search.md`](04_vector_search.md)。索引写入失败处理与搜索错误
彼此独立：搜索失败不会把新闻标为 ``failed``，也不会自动创建或切换 Alias。

## 阶段 5/6 已完成的一次性执行入口

阶段 5 使用既有状态方法提供三个有界 CLI：

```text
sync-news       FreshRSS -> PostgreSQL pending
index-pending   pending/failed -> Chunk/Embedding -> Qdrant -> indexed/failed
run-once        依次执行以上两步，然后退出
```

``index-pending`` 先由 lifecycle 显式准备当前 Collection/Alias，再回收超时
``processing`` 并读取一批候选。候选列表不持有锁；每篇仍通过本章的条件 UPDATE 原子
claim，并使用独立 Session，长时间 Ollama/Qdrant I/O 不占用未提交事务。完整说明见
[`06_news_pipeline_execution.md`](06_news_pipeline_execution.md)。

阶段 6 的 ``sync-news``/``run-once`` 对每个 FreshRSS 来源保存 numeric continuation
checkpoint；文档 upsert 与 checkpoint 同一 PostgreSQL 事务提交，失败来源 rollback 后
隔离继续。同步仍不会直接标记 ``indexed``。同步 HTTP ``POST /pipeline/run-once`` 复用
同一执行 Service，但写入 Runtime 与只读 Search Runtime 分离；详见
[`07_incremental_sync_content_quality_and_manual_api.md`](07_incremental_sync_content_quality_and_manual_api.md)。

## 当前仍未实现

- Retriever；
- 生成式 LLM；
- RAG 问答；
- Prompt Template、Agent、Tool Calling 或对话历史；
- Hybrid Search、全文搜索、reranker 或时间加权；
- 搜索结果 document 聚合或相邻 Chunk 自动扩展；
- 自动后台 Worker 循环；
- 定时调度、消息队列或 WebSocket 进度推送；
- 自动全量重建调度；
- 无界自动重试或 failed 人工隔离队列。

后续应先观察阶段 5 真实批次耗时和失败分布，再决定定时频率、有界并发和最大尝试
次数；检索侧仍需用真实 query/新闻评测 Top-K、threshold、同文档多个 Chunk 和相邻
上下文。不能因为搜索已能返回正文，就直接把查询和生成式回答混成一个难以测试的步骤。
