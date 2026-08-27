# 阶段 2A：Qdrant 基础概念与当前项目映射

## 本阶段目标和非目标

阶段 1 已经能够把新闻 Chunk 转换为 1024 维 Embedding，但向量只存在于 Python
内存。阶段 2 使用 Qdrant 保存向量和对应的新闻片段，为后续 Vector Search（向量
检索）建立可靠的数据基础：

```text
LangChain Chunk Document
    -> Ollama / bge-m3:567m
    -> 1024 维 Embedding
    -> Qdrant Point（通过 current Alias 写入）
```

本文聚焦阶段 2 存储设计；阶段 3 现已在不修改这些写入契约的前提下实现相似度搜索、
Top-K、可选 score threshold 与 Payload filter，详见
[`04_vector_search.md`](04_vector_search.md)。项目仍不实现 Retriever、生成式 LLM 或
RAG 问答，PostgreSQL 也不新增 Chunk 表或 Embedding 表。

## PostgreSQL 和 Qdrant 的职责

PostgreSQL 是业务事实来源，保存新闻、来源和处理状态：

```text
sources
documents
content_text
content_hash
processing_status
```

Qdrant 是可以由 PostgreSQL 新闻和代码中的索引规格重新生成的检索副本，保存：

```text
Collection
    -> Point ID
    -> Vector
    -> Payload
```

如果两个系统内容冲突，应先查清 PostgreSQL 当前新闻版本和索引状态，而不是把 Qdrant
当作新闻业务事实来源。

## Collection（向量集合）

Collection 是 Qdrant 中真正保存 Point、Vector 和 Payload 的容器。可以把它粗略
类比成“带专用向量索引能力的表”，但它不是关系数据库表，也不使用 SQL 行列模型。

每个 Collection 固定声明 Vector dimension（向量维度）和 Distance metric（距离
度量）。当前项目规格为：

```text
dimension = 1024
distance = Cosine
```

同一个 Collection 中的 Vector 必须维度相同，并来自同一个 Embedding 空间。两个
模型即使都返回 1024 维，也不能据此混存，因为相同坐标位置不一定属于同一语义空间。

当前物理 Collection 命名规则：

```text
news_chunks_{environment}_{schema_version}_{generation}

例如：
news_chunks_langchain_v1_001
```

- `environment` 隔离 dev、test、prod。
- `schema_version` 代表模型、维度、Distance、Chunk 规则和 Payload 契约。
- `generation` 代表同一规格下的重建代次。

对应代码是 `config/qdrant.py::QdrantSettings.collection_name`。

## Point（向量点）

Point 是 Qdrant 中的一条记录：

```text
Point ID + Vector + Payload
```

本项目直接复用 LangChain Chunk `Document.id` 作为 Point ID。这个 ID 是由父文档
UUID、tokenizer、Chunk 大小、重叠和顺序生成的稳定 UUIDv5。

相同文档使用相同切分规则重复处理时，Point ID 不变，因此 Qdrant `upsert` 会更新
原 Point，而不是产生重复数据。对应代码是 `pipeline/document_chunker.py`。

## Vector（向量）

Vector 是 `bge-m3:567m` 返回的 `list[float]`。当前真实服务已经探测到 1024 维。
单个坐标不能直接翻译成人类可读的概念，Qdrant 使用整组坐标计算接近程度。

写入前必须满足：

- 恰好 1024 维；
- 每个坐标都是数值；
- 不包含 NaN 或 Infinity；
- L2 norm（L2 范数，也就是向量长度）不为 0。

对应校验位于 `pipeline/ollama_embedding_provider.py` 和 `qdrant/store.py`。

## Payload（结构化附加数据）

Payload 是随 Point 保存的新闻和 Chunk 信息。Payload 不进入当前 Embedding，但可以
用于展示新闻、按来源或时间过滤、回查 PostgreSQL 和清理旧 Chunk。

当前 Payload 字段如下：

| 字段 | 来源 | 用途 |
|---|---|---|
| `page_content` | Chunk 正文 | 展示命中片段，后续 RAG 使用 |
| `document_id` | PostgreSQL 文档 UUID | 按新闻查找和删除全部 Chunk |
| `content_hash` | 正文 SHA-256 | 标识正文版本 |
| `chunk_index`、`chunk_count` | DocumentChunker | 片段顺序和总数 |
| `previous_chunk_id`、`next_chunk_id` | DocumentChunker | 可选相邻上下文关系 |
| `title`、`url` | DocumentRecord | 展示标题和回到原文 |
| `published_at` | 来源声明的新闻发布时间 | 展示和时间范围过滤 |
| `source_updated_at` | 来源声明的更新时间 | 审计来源版本 |
| `document_type` | 文档类型 | article、policy_document 等过滤 |
| `source_id` | PostgreSQL 来源 UUID | 按具体来源过滤 |
| `source_provider` | 数据提供方 | 例如 freshrss_main |
| `source_name` | 来源展示名称 | 展示结果来源 |
| `source_external_id` | 外部来源 ID | 排查外部记录 |
| `document_external_id` | 外部新闻 ID | 排查外部记录 |
| `authors`、`labels` | 作者和标签 | 展示和标签过滤 |
| `index_schema_version` | VectorIndexSpec | 审计索引契约 |
| `embedding_model` | VectorIndexSpec | 审计模型名称 |

`published_at` 可以为空。缺失时不使用抓取时间代替，因为“新闻发布时间”和“服务抓取
时间”是不同业务事实。存在时必须是带时区的 ISO 8601 字符串。

对应映射和类型校验位于 `qdrant/payload.py::QdrantPayloadMapper`。Mapper 使用显式
白名单，不会把任意 LangChain Metadata 自动写入存储契约。

## Payload field 和 Payload index 的区别

Payload field 是实际保存的字段。Payload index（Payload 过滤索引）只为经常参与
过滤或清理的字段建立加速结构。不是每个保存字段都要建立索引。

第一批 Payload index：

```text
document_id      UUID index
source_id        UUID index
source_provider  keyword index
document_type    keyword index
published_at     datetime index
labels           keyword index
```

`title`、`url` 和 `page_content` 当前只用于展示，不建立过滤索引。索引会占用内存和
构建时间，因此不能因为字段存在就全部创建。以后若实现全文或 Hybrid Search（混合
检索），再单独评估 text index。

对应计划位于 `qdrant/lifecycle.py::PAYLOAD_INDEX_SCHEMAS`。

## Alias（别名指针）

Alias 是指向物理 Collection 的稳定名字，它自己不保存 Point：

```text
应用访问：news_chunks_langchain_current
                         |
                         v
真实数据：news_chunks_langchain_v1_001
```

所有应用 Point I/O 都必须访问 Alias：

```text
upsert
filtered scroll
delete
阶段 3 的 query_points/search
```

只有生命周期操作直接使用物理 Collection 名：

```text
创建和校验 Collection
创建 Payload index
全量重建
切换 Alias
删除旧 Collection
```

重建时可以先构建 `news_chunks_langchain_v1_002`，旧 Collection 继续服务。新 Collection
验证完成后，Qdrant 使用一次原子 Alias 更新把 `current` 指向 `002`。Alias 切换不会
复制或移动 Point。

当前代码边界：

| 模块 | 是否允许物理 Collection 名 |
|---|---:|
| `qdrant/lifecycle.py` | 是，仅生命周期操作 |
| `qdrant/store.py` | 否，只使用 current Alias |
| `DocumentIndexingService` | 否，只依赖 Point Store |

离线 spy 测试会断言所有 Point I/O 都没有出现物理 Collection 名。

标准组装入口是 `qdrant/runtime.py::DocumentIndexingRuntime`。它把
`QdrantSettings`、`OllamaEmbeddingSettings`、`VectorIndexSpec`、Chunker、Provider、
Lifecycle 和 Alias Store 绑定成同一套配置，避免调用方手工把模型、Chunk 参数和
Collection 规格拼成不同版本。调用方显式执行 `ensure_ready()` 后再使用其中的
`DocumentIndexingService`；模块 import 不会自动创建外部 Collection。

## Distance metric（距离度量）

Distance metric 决定如何比较两个向量。Qdrant 的常见选择：

| 英文名称 | 中文解释 | 特点 |
|---|---|---|
| `Cosine` | 余弦相似度 | 比较方向，弱化向量整体长度 |
| `Dot` | 点积或内积 | 同时受方向和长度影响 |
| `Euclid` | 欧氏距离 | 两点之间的直线距离 |
| `Manhattan` | 曼哈顿距离 | 各坐标差值绝对值之和 |

当前选择 `Cosine`。公式为：

```text
Cosine(A, B) = Dot(A, B) / (L2Norm(A) * L2Norm(B))
```

所以 Cosine 不是单纯 Dot product（点积），而是使用两个向量长度修正后的点积。新闻
语义检索更关心向量方向表达的语义接近程度，因此当前选择 Cosine。

## L2 normalization（L2 归一化）

L2 normalization 会把向量除以自身长度，使新向量长度为 1，同时保持方向：

```text
[3, 4] 的 L2 norm = 5
归一化后 = [0.6, 0.8]
```

两个向量都归一化后：

```text
Cosine similarity = Dot product
```

Qdrant 的 Cosine Collection 会在上传时执行 normalization。项目不会在 Python 中
重复归一化，但会提前拒绝全零向量，因为全零向量没有方向，不能形成有意义的 Cosine。

真实内存 Qdrant 测试会写入 `[3, 4, 0]`，再验证读回结果约为 `[0.6, 0.8, 0]`。

## VectorIndexSpec（索引规格）

`qdrant/index_spec.py::VectorIndexSpec` 集中记录：

```text
schema_version = v1
embedding_model = bge-m3:567m
dimension = 1024
distance = Cosine
tokenizer = cl100k_base
chunk_size = 512
chunk_overlap = 96
payload_schema_version = v1
```

生命周期组件把规格写入 Collection metadata，并在连接已有 Collection 时逐项校验。
发现不一致会停止写入，不会自动删除或修改已有数据。

模型、维度、Distance、tokenizer、Chunk 参数或不兼容 Payload 契约变化时，必须创建
新的 Schema 版本和物理 Collection。当前只有一套规格，所以不新增
`vector_index_versions` PostgreSQL 表。

## 为什么直接使用 qdrant-client

阶段 2 实际核对了 `langchain-qdrant==1.1.0`。它的公开写入方法会再次调用 Embedding，
并把 Metadata 固定嵌套到 `metadata` 字段。当前 Pipeline 已经使用
`OllamaEmbeddingProvider` 生成并严格校验向量，还需要扁平 Payload 为
`document_id`、`published_at` 等建立过滤索引。

强行使用该写入方法会：

1. 对同一文本重复调用 Ollama；
2. 增加超时和远程负载；
3. 绕过已经完成的响应验证；
4. 破坏扁平 Payload 契约。

所以当前边界是：

```text
LangChain
    -> Document
    -> RecursiveCharacterTextSplitter
    -> OllamaEmbeddings

qdrant-client
    -> 写入预计算 Point
    -> 管理 Collection 和 Alias
```

项目直接依赖官方 `qdrant-client==1.19.0`，没有保留无实际用途的
`langchain-qdrant` 依赖。这仍然是 LangChain-only 文档和 Embedding 架构，只是避免
VectorStore 再做一遍已经完成的 Embedding。

## 配置

```text
QDRANT_BASE_URL=https://qdrant.example.com
QDRANT_API_KEY=
QDRANT_REQUEST_TIMEOUT_SECONDS=30
QDRANT_ENVIRONMENT=langchain
QDRANT_COLLECTION_SCHEMA_VERSION=v1
QDRANT_COLLECTION_GENERATION=1
QDRANT_WRITE_BATCH_SIZE=64
QDRANT_VECTOR_DIMENSION=1024
QDRANT_DISTANCE=Cosine
```

API Key 使用 `SecretStr`，允许为空。生产密钥应与 HTTPS 配合使用，只写本地 `.env`，
不能进入源码、测试、README、学习文档或日志。

当前 `qdrant-client==1.19.0` 的构造参数 `port` 默认是 6333，即使传入完整 HTTPS URL
也会把无端口反代地址改写成 `https://host:6333`。统一 `build_qdrant_client()` 显式传
`port=None`：URL 自带端口时原样保留，否则 HTTPS 使用 443、HTTP 使用 80。该边界已有
离线 URI 测试和真实反代访问验证。

## 当前模块调用关系

```text
QdrantSettings
    -> 物理 Collection 名和 current Alias

VectorIndexSpec
    -> 1024 / Cosine / 模型 / Chunk / Payload 契约

QdrantCollectionLifecycle
    -> 创建和校验物理 Collection
    -> 创建 Payload index
    -> 原子创建或切换 Alias

QdrantPayloadMapper
    -> LangChain Chunk -> 扁平新闻 Payload

QdrantChunkStore
    -> 只通过 current Alias 做 Point I/O

QdrantVectorSearch
    -> 只通过 current Alias 执行 query_points 和 Payload filter

DocumentIndexingRuntime
    -> 只组装索引/lifecycle 组件并集中关闭 Ollama/Qdrant client

VectorSearchRuntime
    -> 只组装 query Embedding/current Alias 搜索组件
```

完整 `processing_status` 和单篇替换流程见
[`03_document_indexing_pipeline.md`](03_document_indexing_pipeline.md)。
一次性同步/索引命令见
[`06_news_pipeline_execution.md`](06_news_pipeline_execution.md)。

## 当前完成标准

- 物理 Collection 与 current Alias 有明确边界；
- 应用 Point I/O 只访问 Alias；
- Collection 使用 1024 维和 Cosine；
- Qdrant 负责 Cosine normalization，应用拒绝零范数；
- Point ID 直接使用稳定 Chunk UUID；
- Payload 保存新闻时间、正文、来源和版本信息；
- 第一批过滤字段有明确 Payload index；
- Collection 规格不一致时停止，不自动破坏数据；
- 默认测试使用本地内存 Qdrant，不依赖远程服务；
- 阶段 3 相似度搜索继续只使用 current Alias，不改变 Point/Payload 写入契约；
- 尚未实现 Retriever、LLM、Agent 或 RAG。
