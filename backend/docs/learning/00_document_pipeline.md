# 阶段 0：从业务文档到可向量化 Chunk

## 本阶段解决的问题

Embedding 模型不能直接理解 PostgreSQL ORM 对象。模型最终接收的是文本，因此在
生成向量之前，系统需要先建立一条职责清晰、可以重复执行的数据转换链路：

```text
FreshRSS API 数据
    -> SourceDocument
    -> PostgreSQL DocumentRecord
    -> LangChain Document
    -> LangChain Chunk Document
```

本阶段到 Chunk 为止，不调用 Embedding 服务，也不写入向量数据库。这样遇到问题时，
可以先独立判断是抓取、清洗、持久化、Metadata 映射还是切分规则造成的。

## 四种文档对象为什么不能合并

### FreshRSSItem

这是外部 API 的协议模型。字段名称和结构由 FreshRSS 决定，外部 API 变化可能要求
修改它。它不应该直接进入数据库或 RAG 流程。

### SourceDocument

这是系统内部统一的 Pydantic 模型。FreshRSS、SEC 或其他未来来源都应先转换成这个
结构，从而把不同外部协议隔离在 ingestion 层。

### DocumentRecord

这是映射到 PostgreSQL `documents` 表的 SQLAlchemy ORM 实体。它保存业务事实、
来源关联、清洗正文、内容哈希和处理状态；索引状态字段记录 Qdrant 派生副本的版本。
PostgreSQL 是业务数据的事实来源。

### LangChain Document

这是 RAG 流程使用的内存对象，不是数据库表。三个重要字段是：

| 字段 | 当前来源 | 用途 |
|---|---|---|
| `id` | `DocumentRecord.id` | 稳定关联 PostgreSQL 文档 |
| `page_content` | `content_text` | 切分以及下一阶段的 Embedding 输入 |
| `metadata` | 文档和来源字段 | 过滤、展示、引用和回查原文 |

常规 LangChain Embedding 调用只向量化 `page_content`，不会自动把 Metadata 拼进
正文。因此 UUID、URL 等关联字段可以保留在 Metadata 中，而不会污染文本向量。

## Chunk 是什么

一篇新闻可能长于模型适合处理或检索的范围，所以 `DocumentChunker` 会把完整正文
拆成较小的 LangChain Document。每个 Chunk 都有自己的正文、稳定 ID 和 Metadata；
阶段 2 会把它们映射到 Qdrant Point。

当前默认参数：

```text
tokenizer: cl100k_base
chunk_size: 512 tokens
chunk_overlap: 96 tokens
```

`chunk_overlap` 让相邻片段共享少量上下文，降低一句话或一个论点正好跨越边界时的
信息损失。重叠太大会造成存储和检索结果重复，太小则可能丢失上下文，因此后续需要
使用真实新闻样本评估，而不是只凭经验调整。

每个 Chunk Metadata 还会保存父文档、顺序、总数和相邻 Chunk ID。这些关系现在由
Qdrant Payload mapper 写入 Point，用于展示命中片段、回到原文或扩展相邻上下文。

## 为什么 Chunk ID 必须稳定

项目使用父文档 UUID、tokenizer、Chunk 大小、重叠大小和顺序生成 UUIDv5。相同文档
在相同配置下重复切分，会产生相同 Chunk ID。Qdrant 阶段直接把它作为 Point ID，
重复执行索引任务时进行幂等 upsert，而不是产生重复数据。

切分配置发生变化时 ID 也会变化。这是有意设计：新旧切分策略得到的是不同的派生
数据，不应该在没有版本意识的情况下静默覆盖。

## 阶段 1 和阶段 2 的后续链路

本阶段文档当时把下一步限定为 Ollama Embedding；该阶段现在已经完成，随后又完成了
Qdrant 存储阶段。当前完整链路是：

```text
LangChain Chunk Document.page_content
    -> Ollama
    -> bge-m3:567m
    -> list[float] 向量
    -> Qdrant Point（通过 current Alias）
```

需要学习和验证的重点包括：

1. Embedding 是把文本映射为固定维度数值向量，不是生成摘要或回答。
2. 文档 Chunk 和用户查询必须使用同一个模型及兼容的处理规则生成向量。
3. 向量维度以服务真实返回值为准，不能只根据模型资料硬编码。
4. 批量大小影响吞吐、延迟、内存和服务负载，需要配置化并通过测量调整。
5. 远程 Ollama 可能出现认证失败、超时、限流、空向量和维度不一致，代码必须给出
   明确错误，不能让错误数据进入 Qdrant。

当前 `.env.example` 已登记服务地址 `https://ollama.example.com` 和模型
`bge-m3:567m`。真实密钥只应填写在被 Git 忽略的 `.env`，不能写进源码、测试、
README 或提交记录。

## 学习入口

- 阶段 1：[`01_ollama_embedding.md`](01_ollama_embedding.md)
- 阶段 2A：[`02_qdrant_concepts.md`](02_qdrant_concepts.md)
- 阶段 2B：[`03_document_indexing_pipeline.md`](03_document_indexing_pipeline.md)
- 阶段 3：[`04_vector_search.md`](04_vector_search.md)

阶段 3 已实现用户 query 到 Qdrant current Alias 的只读 Vector Search，并继续保持
LangChain Chunk、query/document Embedding、Qdrant Point 与 PostgreSQL 实体的概念
边界。项目仍然没有实现生成式 LLM、Retriever、Agent 或 RAG 问答。
