# 新闻从 FreshRSS 到可被检索

跨 `ingestion/` → `repositories/` → `pipeline/` → `qdrant/` 四层，两个 Service 各管一半。
单模块内部的顺序看各自 docstring，本文只记跨模块的接缝、终态和失败边界。

## 两段独立的过程

关键前提：**同步和索引是两件事，不在一个事务里，也可以分别单独跑。**

```
[同步] FreshRSS ──► PostgreSQL documents（processing_status = 待处理）
[索引] PostgreSQL 待处理文档 ──► Chunk ──► Ollama 向量 ──► Qdrant ──► 回写状态
```

`run-once` 只是把两段连着跑一遍，不是把它们合成一个事务。中间断了，第一段的成果留在
PostgreSQL 里，下次 `index-pending` 会接着处理。

## 第一段：同步入库

`services/freshrss_import_service.py`

1. 按订阅列表逐个来源处理。
2. 每个来源：读增量 ID 页 → 拉条目 → `ingestion/freshrss_mapper.py` 映射成领域对象 →
   `ingestion/content_quality.py` 过滤 → `repositories/document_repository.py` 幂等写入。
3. 同一事务里更新该来源的 checkpoint（同步进度游标）。

**失败边界是「单个订阅」。** 一个来源报错只回滚它自己，checkpoint 不前进，下次重跑；
其他来源已提交的数据不受影响。所以部分成功是正常终态，不是异常。

## 第二段：向量索引

`services/document_indexing_service.py`，一篇一次调用。

先占坑、再干活、后确认：

1. **领取**：条件 UPDATE 把状态从 pending/failed 原子改成 processing。多个 Worker 同时抢，
   只有一个成功。
2. **干活**：切 Chunk → Ollama 向量化 → 写 Qdrant。**这期间不占数据库事务**，因为这几步
   都是网络调用，占着事务等网络会把连接池耗光。
3. **确认**：按 revision 条件回写最终状态。如果处理期间新闻内容变了（revision 变了），
   条件不满足，不覆盖新版本——旧 Worker 白干，但不会写坏数据。

三种终态：索引成功 / 跳过（没领到，或被更新版本抢走）/ 失败。定义在 `DocumentIndexingResult`。

## 谁来调这两段

`services/news_pipeline_execution_service.py` 是 CLI 三个命令背后的执行器：开短生命周期
Session、取索引候选、**回收超时卡在 processing 的任务**、逐篇调用索引 Service。

进程崩在第二步会留下 processing 状态的孤儿任务，靠这里的超时回收捞回来，不需要人工介入。

`api/pipeline.py` 的 `POST /pipeline/run-once` 走同一个执行器，只是入口不同，要求超级用户。

## 边界

- 没有常驻 Worker、定时调度和后台自动重试。每一轮都是外部显式触发。
- Chunk 和向量不落 PostgreSQL，只在 Qdrant。
- Qdrant Collection 和 Alias 的创建切换归 `qdrant/lifecycle.py`，不在本链路里。
