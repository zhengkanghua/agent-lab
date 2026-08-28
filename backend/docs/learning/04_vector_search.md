# 阶段 3：Qdrant Vector Search 向量检索

## 本阶段目标和非目标

阶段 2 已经把新闻 Chunk 的 document embedding 保存到 Qdrant。阶段 3 增加相反方向的
只读链路：把用户 query 转换为同一空间的 query embedding，再从 current Alias 中
找出最相似的 Chunk Point。

```text
用户 query 文本
    -> OllamaEmbeddingProvider.embed_query()
    -> Ollama / bge-m3:567m
    -> 1024 维有限、非零 query Vector
    -> Qdrant current Alias
    -> Cosine Vector Search + Payload Filter
    -> 按 Qdrant score 排序的 Chunk 结果
```

本阶段只回答“哪些已索引 Chunk 在向量空间中最接近 query”。它明确不实现：

- 生成式 LLM；
- Prompt Template；
- RAG 问答或答案引用生成；
- Retriever、Agent、Tool Calling 或对话历史；
- Hybrid Search、全文搜索、reranker 或多模型检索；
- 同文档 Chunk 聚合、相邻 Chunk 自动扩展或时间加权；
- 搜索时创建 Collection、切换 Alias、写 Qdrant 或修改 PostgreSQL 状态。

## document embedding 与 query embedding

document embedding 表示“将来要被找到的资料”。本项目先把新闻切成 LangChain Chunk，
再把每个 Chunk 的 `page_content` 交给：

```text
OllamaEmbeddingProvider.embed_documents()
```

query embedding 表示“用户这次想找什么”。搜索必须调用：

```text
OllamaEmbeddingProvider.embed_query()
```

当前 `langchain-ollama==1.1.0` 的 `aembed_query()` 内部会把一条文本交给同一异步
document embed API，但业务代码仍必须保留 query/document 方法边界。这个边界表达了
文本角色，也允许未来模型对 query 和 document 使用不同的合规前缀或任务模式，而不
需要修改所有调用方。

搜索代码不能用 `embed_documents([query])` 代替 `embed_query(query)`。测试中的 fake
会明确记录调用，若搜索误用 document 方法就失败。

## 为什么必须在同一个向量空间

一个 1024 维 Vector 不是 1024 个通用含义固定的指标。每个坐标如何组合表达语义，
由模型及其输入规则共同决定。两个模型即使都返回 1024 维，坐标含义通常也不同。

当前文档与查询必须同时满足：

```text
embedding_model = bge-m3:567m
dimension = 1024
index_schema_version = v1
```

因此 `VectorSearchService` 构造时核对 Provider 模型与 `VectorIndexSpec`，生成 query
Vector 后再次核对维度、数值有限性和 L2 norm。Qdrant 返回的每个 Payload 也必须声明
相同 `embedding_model` 和 `index_schema_version`。仅仅“长度相同”不能证明空间相同。

## Vector Search 的直观解释

可以把 Embedding 想成模型为文本生成的“语义坐标”。例如：

```text
query：央行近期是否调整利率？

Chunk A：央行宣布下调政策利率……
Chunk B：货币政策工具利率保持不变……
Chunk C：某球队赢得联赛冠军……
```

在合适的 Embedding 模型中，query 与 A/B 的向量方向通常比与 C 更接近。Qdrant 使用
Collection 声明的 Distance metric 比较 query Vector 与已存 Vector，再返回最接近的
Point。它比较的是统计语义表示，不证明新闻事实正确，也不等价于关键词严格包含。

## Cosine、Dot、Euclid 和 Manhattan

Qdrant 常见的四种度量有不同数学含义：

| Distance | 直观含义 | 当前 qdrant-client 内存实测 score 排序 |
|---|---|---|
| `Cosine` | 比较向量方向，弱化整体长度 | similarity 越高越靠前 |
| `Dot` | 点积，同时受方向和长度影响 | dot product 越高越靠前 |
| `Euclid` | 两点间直线距离 | distance 越低越靠前 |
| `Manhattan` | 各坐标差值绝对值之和 | distance 越低越靠前 |

Cosine similarity（余弦相似度）公式：

```text
similarity(A, B) = Dot(A, B) / (L2Norm(A) * L2Norm(B))
```

非零向量的数学范围是 `[-1, 1]`：

```text
 1  -> 同方向
 0  -> 正交
-1  -> 反方向
```

有些资料把 Cosine distance（余弦距离）定义成：

```text
distance = 1 - similarity
```

此时 distance 越小越近。但这只是常见数学定义，不能把它直接当成当前 Qdrant 返回
字段的解释。Qdrant 的 `ScoredPoint.score` 是 metric-aware score（随 Collection 度量
解释的排序值）。当前 Collection 使用 `Cosine`，内存实测 `[1, 0]` 查询得到：

```text
[1, 0]  -> score  1
[0, 1]  -> score  0
[-1, 0] -> score -1
```

所以本项目的 Cosine `score` 通常越高越相似。它不是概率，不表示“有 80% 相关”，
也不能跨模型、跨 Schema 或跨 Distance 直接比较。

## Qdrant normalization 与 query Vector 校验

阶段 2 已确认 Qdrant Cosine Collection 会归一化上传的 document Vector。搜索时仍然
不能把任意 query Vector 直接发送过去。应用拒绝：

- 维度不是 1024；
- 空 Vector；
- 非数字坐标或 `bool`；
- `NaN`、正负 `Infinity`；
- 全零 Vector；
- L2 norm 本身溢出为非有限数值。

应用不手动归一化有效 query Vector。Qdrant 根据 Cosine Collection 语义处理比较，
避免项目在读写两侧分别实现一套可能漂移的 normalization。

## Top-K

Top-K 表示“最多返回 score 最好的 K 个 Point”。第一版契约为：

```text
default top_k = 10
minimum       = 1
maximum       = 100
```

默认 10 是一个便于首屏扫描的保守起点，同时限制返回的 Chunk 正文总量。最大 100 是
请求安全上限，避免一次把大量 `page_content` 拉入应用内存或传给后续调用方。它不是
质量结论；真实产品可以先采集延迟、结果点击和标注数据，再决定是否修改契约。

Top-K 是上限，不保证一定返回 K 条。Collection 可能少于 K 条，Payload filter 或
score threshold 也可能进一步减少结果。

## score threshold

`score_threshold` 是可选的最低 Cosine score。当前 Qdrant 对 Cosine 使用：

```text
score >= score_threshold
```

因此同时传入 `top_k=10` 和 `score_threshold=0.6` 的含义是：只考虑分数至少 0.6 的
Point，再最多返回其中排序最好的 10 条。如果只有 3 条达到阈值，响应就是 3 条，不会
由 Python 放宽阈值凑满 10 条。

第一版默认：

```text
score_threshold = None
```

也就是不设置全局阈值。请求可以显式传入有限的 `[-1, 1]` 数值。虽然
`qdrant-client==1.19.0` 自己的 `QueryRequest` 模型会接受越界值、`NaN` 和
`Infinity`，本项目知道当前空间固定为 Cosine，因此在 Pydantic 请求边界主动拒绝
这些无意义值。

不能拍脑袋把 `0.8` 当成通用阈值，原因包括：

- 不同 Embedding 模型的 score 分布不同；
- 中文、英文和混合语言的分布可能不同；
- query 长短、新闻 Chunk 长度和领域术语会改变分数；
- 相似但答案相反的文本可能仍有较高语义相似度；
- 过滤后的候选集合与实际新闻覆盖面会影响结果；
- 模型或 Chunk Schema 变化后旧阈值需要重新评测。

正确做法是建立真实 query、候选 Chunk 和人工相关性标注，观察 recall、precision、
无结果率与分数分布后再选阈值。评测完成前，默认不设 threshold 比假设 0.8 更诚实。

## Payload filter

Payload filter（载荷过滤）是在向量排序候选上施加的结构化约束。当前所有条件由 Qdrant
执行，不会先取一个很大的 Top-K 再由 Python 删除不匹配项。

这样做有三个直接收益：

1. Top-K 针对真正允许的候选集合计算，不会被稍后丢弃的结果占满。
2. 少传输无用的 Chunk 正文，降低网络和应用内存开销。
3. Qdrant 可以使用阶段 2 建立的 Payload index，避免 Python 全量扫描。

第一版过滤映射：

| 请求字段 | Qdrant Payload | 模型 | 语义 |
|---|---|---|---|
| `source_id` | `source_id` | `MatchValue` | UUID 字符串精确匹配 |
| `source_provider` | `source_provider` | `MatchValue` | keyword 精确匹配 |
| `document_type` | `document_type` | `MatchValue` | DocumentType value 精确匹配 |
| `labels` | `labels` | `MatchAny` | Point 标签命中请求标签中的任意一个 |
| `published_from` | `published_at` | `DatetimeRange.gte` | 包含下界 |
| `published_to` | `published_at` | `DatetimeRange.lte` | 包含上界 |

不同字段条件放在 `Filter.must` 中，表示 AND。例如：

```text
source_provider = freshrss_main
AND document_type = article
AND labels 命中（宏观 OR 利率）
AND published_at >= 2026-08-01T00:00:00+08:00
```

## 标签：任意匹配还是全部匹配

第一版明确选择“任意匹配”：

```text
请求 labels = [宏观, 利率]

Point [宏观]       -> 命中
Point [利率]       -> 命中
Point [宏观, 利率] -> 命中
Point [体育]       -> 不命中
```

理由是新闻标签常来自不同来源，粒度和完整度不一致；要求全部标签容易制造大量无结果。
`MatchAny` 也直接符合 Qdrant array keyword 的语义。若未来产品确实需要“全部包含”，
应该新增一个显式模式并单独测试，不能悄悄改变现有字段含义。

空 `labels=[]` 明确表示“不增加标签过滤”。标签数组中的空白字符串会被拒绝；重复标签
会保留首次出现顺序后去重。

## published_at 时间过滤

请求中的 `published_from` 和 `published_to` 必须带时区，例如：

```text
2026-08-01T00:00:00+08:00
2026-08-01T00:00:00Z
```

没有时区的 `2026-08-01T00:00:00` 会被拒绝，因为服务无法判断它代表 UTC、北京时间
还是其他地区。下界晚于上界也会在 Qdrant I/O 前拒绝。

范围端点均包含：

```text
published_at >= published_from
published_at <= published_to
```

阶段 2 对没有来源发布时间的新闻不会伪造 `published_at`，Payload 中直接缺失该字段。
Qdrant 的 datetime range condition 不会匹配缺失字段，所以：

- 没有时间过滤时，缺失 `published_at` 的新闻仍可返回；
- 一旦设置任一时间边界，缺失 `published_at` 的新闻不会返回。

## 为什么不按新闻时间加权

第一版完全使用 Qdrant Cosine score 排序，不把“越新”混入语义 score。新并不等于
相关，旧新闻也可能是查询要找的历史事实。若偷偷给新内容加权，调用方看到的 `score`
将不再是 Qdrant 原始语义分数，threshold 含义也会变得不透明。

需要“最近且相关”时，当前可以使用明确的 `published_from/to` 过滤。未来若业务真的
需要 freshness ranking，应先定义独立公式、参数、返回字段和离线评测，并让调用方
明确选择，而不是修改当前 Vector Search 的默认语义。

## 同一新闻多个 Chunk 命中

一篇新闻会被切成多个独立 Point，多个 Chunk 同时靠近 query 是正常现象。第一版逐条
保留每个 Chunk 命中：

```text
document A / chunk 2 / score 0.91
document A / chunk 3 / score 0.88
document B / chunk 0 / score 0.83
```

系统不提前按 `document_id` 去重或聚合，因为这会丢失原始 score 顺序，还需要回答
“每篇保留几个 Chunk”“聚合 score 用 max 还是 average”等尚未评测的问题。结果保留
`document_id`、`chunk_index/count` 和相邻 ID，调用方可以清楚识别关系。后续若需要
document aggregation，应作为显式阶段另建契约。

## 为什么结果必须保留身份、正文和新闻时间

每条命中至少需要三类信息：

- 身份：`point_id/chunk_id`、`document_id`、`source_id`，用于稳定回查和去歧义；
- 命中内容：`page_content`、`chunk_index/count`、相邻 ID，用于展示实际相关片段；
- 新闻语境：标题、URL、来源、作者、标签、`published_at`，用于判断结果属于何时何地。

只返回 score 没有可读内容，只返回正文无法可靠回到原新闻，只返回 document ID 又无法
解释哪个片段命中。因此结果从 Qdrant Payload 映射完整受控字段，但不会返回完整 query
Vector 或已存 document Vector。

## current Alias 的查询作用

应用只知道：

```text
news_chunks_{environment}_current
```

物理数据可能位于：

```text
news_chunks_langchain_v1_001
news_chunks_langchain_v1_002
```

`QdrantVectorSearch` 构造时只保存 `settings.collection_alias`，调用
`query_points(collection_name=alias)`。它不会保存或接收物理 Collection 名。这样全量
重建完成后，生命周期组件可以原子切换 Alias，而不需要修改或重启每个搜索调用方。

Alias 切换本身是原子操作，不存在“半个 Alias 同时指向两个普通目标”的中间状态。
并发边界上，已经到达 Qdrant 的请求可能按它被服务端解析时看到的旧目标完成，随后
解析的请求看到新目标。应用不能假设所有并发请求在同一个瞬间一起改变，也不会在
搜索失败时擅自重试到物理 Collection。每个正常请求只会看到旧或新的一套完整目标。

## 当前调用流程

```text
调用方
  |
  | VectorSearchRequest
  | query / top_k / optional threshold / filters
  v
VectorSearchService.search()
  |-- 调用 OllamaEmbeddingProvider.embed_query(query)
  |-- 核对模型、1024 维、有限坐标和非零 L2 norm
  v
QdrantVectorSearch.search()
  |-- VectorSearchFilters -> Qdrant Filter.must
  |-- AsyncQdrantClient.query_points(
  |       collection_name=current Alias,
  |       query=query_vector,
  |       limit=top_k,
  |       score_threshold=optional,
  |       with_payload=True,
  |       with_vectors=False)
  v
QueryResponse.points: list[ScoredPoint]
  |-- 校验 Point UUID、有限 score 和完整 Payload
  |-- 不在 Python 过滤、重排或聚合
  v
list[VectorSearchResult]
```

## Search request 模型

`schemas/vector_search.py` 定义：

```text
VectorSearchRequest
    query: str                        # 1..4096 Unicode characters
    top_k: int = 10                 # 1..100
    score_threshold: float | None   # finite [-1, 1]
    filters: VectorSearchFilters

VectorSearchFilters
    source_id: UUID | None
    source_provider: str | None
    document_type: DocumentType | None
    labels: tuple[str, ...] = ()
    published_from: datetime | None
    published_to: datetime | None
```

`query` 在 Pydantic repr 中隐藏；校验错误也不包含原始 input value。代码不记录完整
query 文本，异常只说明失败阶段和安全字段上下文。

Top-K 默认值和最大值属于公开请求安全契约，不是不同部署的连接配置，所以第一版没有
新增 `VECTOR_SEARCH_DEFAULT_TOP_K` 或 `VECTOR_SEARCH_MAX_TOP_K` 环境变量。若未来
压测证明各部署必须不同，再把它们配置化。未经评测的 score threshold 更不能成为
环境默认值。

## Search result 模型

`VectorSearchResult` 是唯一的 Pydantic 响应契约：

| 字段 | 是否可空 | 来源与用途 |
|---|---:|---|
| `point_id` | 否 | `ScoredPoint.id`，Qdrant Point UUID |
| `chunk_id` | 否 | 同一个 Point ID，以 LangChain Chunk 身份表达 |
| `score` | 否 | Qdrant 原始有限 score，保持排序 |
| `page_content` | 否 | Payload 中曾进入 document Embedding 的 Chunk 正文 |
| `document_id` | 否 | Payload，关联 PostgreSQL `documents.id` |
| `content_hash` | 否 | Payload，64 位 SHA-256 正文版本 |
| `chunk_index/count` | 否 | Payload，Chunk 顺序和总数 |
| `title` / `url` | 否 | Payload，展示和回到原文 |
| `published_at` | 是 | Payload，缺失时为 `None` |
| `source_updated_at` | 是 | Payload，缺失时为 `None` |
| `document_type` | 否 | Payload，DocumentType 枚举 |
| `source_id` | 否 | Payload，关联 PostgreSQL `sources.id` |
| `source_provider/name` | 否 | Payload，来源标识和展示名 |
| `source_external_id` | 否 | Payload，来源系统中的来源身份 |
| `document_external_id` | 否 | Payload，来源系统中的文档身份 |
| `authors` / `labels` | 否 | Payload，允许空列表但不允许 null 或错误类型 |
| `previous_chunk_id` | 是 | Payload，首 Chunk 缺失时为 `None` |
| `next_chunk_id` | 是 | Payload，尾 Chunk 缺失时为 `None` |
| `index_schema_version` | 否 | Payload，必须等于当前规格 |
| `embedding_model` | 否 | Payload，必须等于当前 query 模型 |

可选字段缺失会得到明确 `None`。任何必需字段缺失、UUID/URL/时间格式错误、数组类型
错误、Chunk 关系错误、模型或 Schema 不一致都会抛出
`QdrantSearchResponseError`，不会静默返回损坏结果。错误消息只列出字段名和结果位置，
不包含完整 `page_content`。

## Service 与 Qdrant component 的职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| `VectorSearchRequest/Filters/Result` | 输入输出类型和约束 | 网络、Embedding、Filter 构造 |
| `OllamaEmbeddingProvider` | query 文本到已校验 Vector | Qdrant 查询、结果映射 |
| `QdrantVectorSearch` | Filter、current Alias query、Point 映射 | query 文本、Ollama、PostgreSQL、写操作 |
| `VectorSearchService` | 调用顺序、模型空间和向量边界 | Collection lifecycle、LLM、RAG |
| `DocumentIndexingRuntime` | 索引 Worker 的写入/lifecycle 组件 | Search Service、自动查询 |
| `VectorSearchRuntime` | 阶段 4 HTTP API 的最小只读 Provider/client/spec | lifecycle、Store、索引 Service |

搜索不需要 PostgreSQL Session，也不读取或修改 `processing_status`。Qdrant Payload 已
包含结果展示所需快照；PostgreSQL 仍是业务事实来源，需要完整文档时再由另一个明确的
只读用例回查。

## qdrant-client 1.19.0 真实 API

本阶段基于当前环境 introspection 和内存实跑核实，而不是照搬旧示例：

- 已安装版本是 `qdrant-client==1.19.0`；
- `AsyncQdrantClient.query_points()` 是统一查询入口；
- 当前 client 上不存在旧 `search()` 和 `search_batch()` 方法；
- dense 最近邻查询直接把 `list[float]` 传给 `query`；
- Payload 条件参数名是 `query_filter`；
- `score_threshold`、`limit`、`with_payload`、`with_vectors` 和 `timeout` 都是
  `query_points()` 顶层参数；
- 返回类型是 `QueryResponse`，命中位于 `response.points`；
- 每项是 `ScoredPoint`，包含 `id`、`score`、可选 `payload` 和可选 `vector`；
- 本项目传 `with_payload=True` 和 `with_vectors=False`，不把 document Vector 带回应用；
- current Alias 可以直接作为 `collection_name`，内存 client 已真实验证；
- `Filter(must=[...])`、`FieldCondition`、`MatchValue`、`MatchAny`、
  `DatetimeRange(gte/lte)` 已按当前 Pydantic 模型构造并实跑。

`QDRANT_REQUEST_TIMEOUT_SECONDS` 在创建 client 时作为全局 timeout，并在本次
`query_points` 显式传入。当前异步 REST `AsyncApiClient.send()` 只发送一次请求；429
抛出资源耗尽错误，没有内建重试循环。本项目也不自行重试搜索，避免认证错误、配置
错误和高负载被重复放大。以后若增加重试，只能覆盖经过观测确认的暂时性连接/5xx，
并设置有限次数、退避和总时限。

qdrant-client 默认构造远程 client 时会启动后台版本兼容性探测。统一
`build_qdrant_client()` 在阶段 4 设置 `check_compatibility=False`，确保 Runtime 构造
仍然没有隐式网络 I/O；显式 lifecycle/query 才访问 Qdrant。阶段 5 的真实 HTTPS
反代验证还确认必须传 `port=None`，否则 1.19.0 会给无端口 URL 强行追加 6333；统一
builder 已修复，因此搜索和索引都使用配置 URL 的真实端口。

## 错误分类

| 异常 | 含义 | 是否自动修改外部状态 |
|---|---|---:|
| Pydantic `ValidationError` | query、Top-K、threshold 或 Filter 请求非法 | 否 |
| `OllamaAuthenticationError` | query Embedding 认证失败 | 否 |
| `OllamaConnectionError` | 无法连接 Ollama | 否 |
| `OllamaTimeoutError` | Ollama Embedding 超时 | 否 |
| `EmbeddingResponseError` | query Vector 数值或维度响应损坏 | 否 |
| `QueryVectorValidationError` | Vector 与当前 `VectorIndexSpec` 不一致 | 否 |
| `QdrantSearchAuthenticationError` | Qdrant 401/403 | 否 |
| `QdrantSearchConnectionError` | Qdrant 网络连接失败 | 否 |
| `QdrantSearchTimeoutError` | client timeout 或 HTTP 408/504 | 否 |
| `QdrantSearchTargetNotFoundError` | current Alias 或目标 Collection 不存在 | 否 |
| `QdrantSearchConfigurationError` | Collection 拒绝维度/查询配置 | 否 |
| `QdrantSearchResponseError` | Point/Payload 响应契约损坏 | 否 |
| `QdrantSearchServiceError` | 其他 Qdrant/协议失败 | 否 |

远程 `UnexpectedResponse` 可能包含响应 body，项目不会把它的字符串原样传播。API Key、
完整 query、完整 query Vector 和完整新闻正文都不会进入项目异常。

搜索失败不会：

- 调用 `create_collection`；
- 调用 `update_collection_aliases`；
- 回退到物理 Collection；
- 调用 `upsert` 或 `delete`；
- 写 PostgreSQL 或改变 `processing_status`；
- 静默吞掉异常并返回空列表。

## 离线测试方式

默认完整测试：

```powershell
uv sync --all-groups
uv run pytest -q
```

阶段 3 聚焦测试：

```powershell
uv run pytest -q tests/test_vector_search.py tests/test_qdrant_runtime.py
```

测试使用两种离线替身：

1. fake LangChain Embeddings 记录 `aembed_query` 和 `aembed_documents`，证明搜索只走
   query API，并注入错误向量或异常。
2. `AsyncQdrantClient(location=":memory:")` 建立小维度 Cosine Collection/Alias，真实
   执行 `query_points`、score threshold、MatchAny 和 DatetimeRange。

测试还用 read-only spy 断言搜索只调用 `query_points`，物理 Collection 名从未进入
查询，`upsert/delete/create_collection/update_collection_aliases` 均未执行。

## 可选真实只读验证

默认测试用进程内 Qdrant 和 fake Embedding 完整验证搜索实现，不能伪造远程成功。真实
外部系统只保留两个契约级测试，因为它们验证的东西 mock 无法表达：

```powershell
# 真实 bge-m3:567m 返回 1024 维有限 Vector——维度和 Cosine 距离的前提
$env:RUN_OLLAMA_INTEGRATION_TEST="1"
uv run pytest -q tests/test_ollama_embedding_integration.py

# 真实 Qdrant 的 Alias 间接层与 Point 往返——Alias 切换语义 mock 不了
$env:RUN_QDRANT_REMOTE_INTEGRATION_TEST="1"
uv run pytest -q tests/test_qdrant_remote_integration.py
```

搜索本身的只读性不依赖集成测试：`tests/test_vector_search.py` 用 read-only spy 断言
只调用 `query_points`，物理 Collection 名从未进入查询，
`upsert/delete/create_collection/update_collection_aliases` 均未执行。这条边界由
`VectorSearchRuntime` 的结构保证，比在真实环境里跑一次更可靠。

## 常见故障排查

### query 为空或时间无时区

这是请求校验错误，发生在任何网络 I/O 前。给 query 提供至少一个非空白字符；时间
使用带 `Z` 或明确 offset 的 ISO 8601 值。

### query Vector 不是 1024 维

确认 Ollama 真实部署仍是 `bge-m3:567m`，并检查 `QDRANT_VECTOR_DIMENSION=1024`。
不要截断、补零或自动修改向量；模型空间变化需要新 Schema 和重建 Collection。

### current Alias 不存在

搜索不会自动创建或切换 Alias。由部署准备步骤显式运行阶段 2 lifecycle，检查：

```text
news_chunks_{environment}_current -> 正确物理 Collection
```

不要在搜索代码里改用物理 Collection 绕过错误。

### Qdrant 返回 400

常见原因是 query Vector 维度或 Collection 配置不匹配。核对 `VectorIndexSpec`、Alias
实际目标、Collection dimension/Distance/metadata。搜索只把它分类为配置错误，不会
重建生产索引。

### 认证失败、连接失败或 timeout

认证失败先检查本地 `.env` 是否配置了正确 Qdrant API Key，但不要把密钥打印出来。
连接失败检查 URL、DNS、TLS、防火墙和服务状态。timeout 先观察查询延迟和服务负载；
不要用无限 timeout 或无界重试掩盖问题。

### 时间过滤没有返回无发布时间新闻

这是明确契约：缺失 `published_at` 的 Payload 不满足 DatetimeRange。去掉时间过滤才会
重新包含这些 Point；系统不会用抓取时间代替发布时间。

### 同一新闻返回多个结果

每个 Qdrant Point 是独立 Chunk，第一版不聚合。这不是重复写入的充分证据。先比较
`document_id`、`chunk_id` 和 `chunk_index`；真正重复 Point 应由稳定 UUID 和阶段 2
幂等 upsert 防止。

### threshold 后结果太少

Top-K 是最大数量，threshold 会进一步筛选。先去掉 threshold 观察真实 score 分布，
再用标注数据评估，而不是持续降低或固定某个猜测值。

## 本阶段完成标准

- query 必须非空，并且只调用 `embed_query`；
- query 最多 4096 个 Unicode 字符，超长输入不截断；
- query/document 使用相同 `bge-m3:567m` 和 1024 维空间；
- query Vector 拒绝错误维度、非数字、NaN/Infinity、零或非有限 norm；
- 所有 Qdrant 查询只使用 current Alias 和 `query_points`；
- 默认 Top-K 10，最大 100；threshold 可选且默认关闭；
- source、provider、document type、labels 和发布时间可组合过滤；
- labels 是任意匹配，空数组表示不过滤；
- 时间必须带时区，范围包含端点，缺失时间在范围过滤时不命中；
- 返回顺序完全保持 Qdrant score 顺序；
- 同文档多个 Chunk 均保留，不做时间加权或 document 聚合；
- 结果使用 Pydantic 严格验证必需/可选 Payload 字段；
- 认证、连接、timeout、目标不存在、配置和响应损坏可区分；
- 搜索不创建、更新、删除任何 Qdrant/PostgreSQL 数据；
- 默认测试完全离线，真实验证必须显式开启且只读；
- 没有新增依赖、PostgreSQL 表、Retriever、LLM、Agent 或 RAG。

## 阶段 4 HTTP 衔接

阶段 4 原始实现提供独立只读 HTTP API、最小 Search Runtime、稳定错误响应和 422 query
脱敏，见 [`05_vector_search_api.md`](05_vector_search_api.md)。平台后续又增加本地账号
Cookie 认证与普通用户/超级用户边界，见
[`08_local_password_auth.md`](08_local_password_auth.md)；网关限流仍按部署边界配置。

HTTP 可用后仍不应直接把命中正文塞给生成式模型。先讨论并用真实样本回答：

- 如何建立 query 与 Chunk 的人工相关性评测集；
- Top-K、可选 threshold 的 recall/precision 和无结果率；
- 是否确实需要 document 去重、每文档 Chunk 上限或相邻 Chunk 扩展；
- 如何完善登录限流、会话清理和可观测性；
- 是否有证据需要 freshness ranking、Hybrid Search 或 reranker；
- 结果中的正文、URL 和来源字段对调用方的隐私与授权边界。

只有检索质量和输入输出边界经过评测后，才应在后续独立阶段讨论生成式 LLM 或 RAG。
本阶段代码没有提前实现它们。
