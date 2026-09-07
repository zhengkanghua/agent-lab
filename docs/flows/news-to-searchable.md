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

`knowledge/importing.py`，外部协议在 `knowledge/adapters/freshrss.py`。

1. 按订阅列表发现 Source；新来源只登记，未绑定或绑定库停用时不请求文章、不推进游标。
2. 已绑定启用库的来源：读增量 ID 页 → 拉条目 → `ingestion/freshrss_mapper.py` 映射成领域对象 →
   `ingestion/content_quality.py` 过滤 → `repositories/document_repository.py` 幂等写入。
3. 保存前在短事务中锁定来源及目标库复核状态；文档使用该绑定 ID，同一事务更新 checkpoint。

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
   条件不满足，不覆盖 PostgreSQL 的新版本；Qdrant 可能仍是旧快照，后续索引继续追赶，不能把版本检查当作跨库事务。

三种终态：索引成功 / 跳过（没领到，或被更新版本抢走）/ 失败。定义在 `DocumentIndexingResult`。

## 谁来调这两段

`services/news_pipeline_execution_service.py` 是 CLI 三个命令背后的执行器：开短生命周期
工作单元、取索引候选、**回收超时卡在 processing 的任务**、逐篇调用索引 Service；
PostgreSQL 适配器在事务内提取独立 DocumentSnapshot，后续切分和向量化不访问 ORM。

这些写入口先参加共同的写资源协调。同类写操作串行，同步和索引可并行；清理排他取得二者，避免旧索引在清理之后重新写回。

进程崩在写入过程中会留下处理状态和资源占用。先人工确认旧进程和远端未决写入都已停止、释放占用，后续索引才能取得写资源并回收符合条件的处理记录；仅经过一段时间不足以证明可以重做。

`api/pipeline.py` 的 `POST /pipeline/run-once` 走同一个执行器，只是入口不同，要求超级用户。

## 边界

- 没有后台自动重试。cron 到点与手动触发都会发起执行（见 ADR 0014/0017 的定时任务调度器），但每一轮仍是有界批次，失败靠下一轮 cron 或人工兜底。
- Chunk 和向量不落 PostgreSQL，只在 Qdrant。
- Qdrant Collection 和 Alias 的创建切换归 `qdrant/lifecycle.py`；`rebuild-index` 通过重建用例先构建并验收新 generation，再发布 Alias。它覆盖存量 indexed 文档，不以普通待索引批次代替全量重建。
- 有未完成删除待办的 Document 不参与同步更新或索引认领；同步遇到这种目标会回滚该来源页，checkpoint 不越过它。清理与任务执行恢复见 [定时任务执行](scheduled-job-execution.md)。
