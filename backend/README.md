# News Vector Service

该服务从 FreshRSS 获取新闻，规范化后保存业务状态，并逐步接入 LangChain、
Embedding 和 Qdrant。当前已经实现按 FreshRSS 分类白名单读取文章、按来源小批量
获取、PostgreSQL 幂等保存、持久化文档到 LangChain Document 的转换，以及使用统一
参数把 Document 切分成带稳定 ID 和关系 Metadata 的 Chunk。阶段 1 通过官方
``langchain-ollama`` 集成调用远程 ``bge-m3:567m``，把 Chunk 正文转换为经过校验的
Embedding。阶段 2 已接入 Qdrant Point 存储、物理 Collection/current Alias 生命周期、
新闻 Payload 和单篇索引状态编排。阶段 3 已实现用户 query 经同一 Embedding 模型到
Qdrant current Alias 的只读 Cosine Vector Search，支持 Top-K、可选 score threshold、
来源/类型/标签/新闻时间过滤和严格结果契约。阶段 4 已新增只读
``POST /vector-search`` FastAPI 接口、最小权限 Search Runtime、稳定上游错误码和
422 query 脱敏。当前又在不改变该 Chunk 接口的前提下增加 ``POST /document-search``
服务端文档分组和 ``GET /documents/{document_id}`` PostgreSQL 全文懒加载。阶段 5 已
增加 ``sync-news``、``index-pending`` 和 ``run-once`` 三个
一次性 CLI，把 FreshRSS -> PostgreSQL -> Chunk/Embedding -> Qdrant 写入链路真正
串起来。阶段 6 又增加 FreshRSS continuation/checkpoint 可靠增量同步、进入 content_hash
前的幂等正文质量规范化，以及同步、有界的 ``POST /pipeline/run-once`` 手动写入 API。
当前已增加 FastAPI Users 内部账号密码登录：PostgreSQL DatabaseStrategy 保存可撤销
Token，浏览器使用 HttpOnly Cookie；搜索与全文要求有效账号，Pipeline 要求超级用户，
且不开放注册。环境变量可托管唯一保底超级管理员，登录后的超级用户通过网页/API 管理
其余账号，CLI 仅作恢复工具。仍未实现自动调度、常驻 Worker、后台 Task、WebSocket/
SSE、生成式 LLM 或 RAG。

``ingestion/content_quality.py`` 现在是 FreshRSS Mapper 的统一规范化路径，也可用于
历史正文的只读诊断。新文章在计算 content_hash、revision、构建 Document 和 Chunk
之前完成 HTML entity、Unicode NFC、空白、边界标题与相邻完整重复段落处理；历史
``documents.content_text`` 不会被自动批量改写。

FreshRSS 同步使用显式分类白名单，不会读取总阅读列表：

```text
FRESHRSS_SYNC_CATEGORIES=["新闻","财经","宏观数据"]
```

`FreshRSSImportService.import_recent_per_source()` 会从至少属于一个允许分类的每个
订阅源读取一页。首次运行保存最近的有界基线；之后使用 FreshRSS numeric
``continuation`` 从已提交 checkpoint 之后按旧到新追赶，因此两次手动运行之间到达的
新闻不会因“只看最近 N 篇”被静默越过。同一订阅属于多个允许分类时只处理一次；
``source_id + external_id`` 保持幂等，完全相同的文章不会新增行或递增 revision。

## 数据对象分层

```text
FreshRSSItem
    外部协议对象，声明在 schemas/freshrss.py
        ↓ FreshRSSItemMapper
SourceDocument
    内部统一 Pydantic 模型，声明在 domain/source_document.py
        ↓ Repository
DocumentRecord
    SQLAlchemy ORM 模型，对应 PostgreSQL documents 表
        ↓ DocumentBuilder
LangChain Document
    完整的 RAG 文档对象
        ↓ DocumentChunker / RecursiveCharacterTextSplitter
LangChain Document Chunk
    可独立检索的 Document，带稳定 ID、Metadata 和文档关系
        ↓ OllamaEmbeddingProvider / bge-m3:567m
ChunkEmbedding
    Chunk ID 与 list[float] 的内存映射
        ↓ QdrantChunkStore / current Alias
Qdrant Point
    稳定 Chunk UUID + 1024 维 Vector + 新闻/Chunk Payload
        ↑ QdrantVectorSearch / current Alias
用户 query
    -> OllamaEmbeddingProvider.embed_query()
    -> 1024 维 query Vector
    -> 按 Qdrant score 排序的 Chunk 搜索结果
```

这些层不能合并：外部协议会变化，内部模型需要稳定，数据库只保存需要持久化
的字段，LangChain Document 则服务于 Chunk 和 Embedding。

RSS 来源是否兼容在 ``FreshRSSItemMapper`` 这一层决定。只要 FreshRSS 能提供
文章 URL、非空标题和可读的正文或摘要，并且字段能通过协议模型校验，就可以转换
成统一 ``SourceDocument``；后续 PostgreSQL、Document 和 Chunk 流程不再关心
文章来自哪个 RSS 网站。动态网页回源和站点 selector 由 FreshRSS 负责，不能在
Python Pipeline 中加入站点判断。

## LangChain Document

`pipeline/document_builder.py` 把已持久化的 `DocumentRecord` 转换成 LangChain
`Document`。正文放入 `page_content`；标题、来源和过滤字段放入 `metadata`；
PostgreSQL 文档 UUID 作为稳定的 `id`。LangChain 的常规 Embedding 流程只嵌入
`page_content`，不会把 UUID、外部 ID、URL 和发布时间等 Metadata 拼入向量文本。

构建器不访问数据库。查询 `DocumentRecord` 时必须提前加载 `source` 关系，例如
使用 `selectinload(DocumentRecord.source)`。这样能够避免异步 SQLAlchemy 在读取
关系属性时发生隐式数据库 I/O。

## LangChain Chunk

`pipeline/document_chunker.py` 使用 LangChain 的 `RecursiveCharacterTextSplitter`
把一个完整 `Document` 切分成多个 Chunk `Document`。默认使用 `cl100k_base`
tokenizer、512 token 的 Chunk 上限和 96 token 的重叠上限，不按 Python 字符数
计量。中英文段落、句末标点、逗号和空格按优先级递归切分，尽量保留语义边界。

每个 Chunk 复制父 Document 的 Metadata，并增加 `parent_document_id`、
`chunk_index`、`chunk_count`、`previous_chunk_id` 和 `next_chunk_id`。Chunk ID 根据
父文档 UUID、tokenizer、切分参数和 Chunk 序号稳定生成，重复处理相同版本时不会
产生随机新 ID。切分后会过滤空白 Chunk 和同文档完全重复的 ``page_content``，再按
最终列表重建 index/count/previous/next。Chunk 不写 PostgreSQL；Payload mapper 会把
这些关系字段、Chunk 正文和新闻 Metadata 写入 Qdrant。

正式串接入口是 ``pipeline/document_chunk_pipeline.py`` 中的
``DocumentChunkPipeline.build_chunks(record)``。调用方只需传入已经 eager-load
``source`` 关系的 ``DocumentRecord``；流水线会按固定顺序执行
``DocumentBuilder -> DocumentChunker``，返回 LangChain Document 列表。

## Ollama Embedding

``pipeline/ollama_embedding_provider.py`` 统一创建官方 ``OllamaEmbeddings``，业务层
不直接拼装客户端。Provider 只把 Chunk ``page_content`` 发送给模型，Chunk ``id``
随结果返回以保持关联，Metadata 不进入 Embedding。它提供异步 query、document、
Chunk 批量调用和真实维度探测；空列表不访问网络，非空列表严格使用
``OLLAMA_EMBEDDING_BATCH_SIZE`` 分批并保持顺序。

每批响应都会验证数量、非空向量、数值类型、NaN/Infinity 和维度；不同批次及同一
Provider 生命周期中的维度也必须稳定。向量维度来自服务真实返回长度，不在源码中
硬编码。Provider 自身仍只返回内存结果；阶段 2 的 ``DocumentIndexingService`` 才会
在完整 Qdrant 写入后更新 PostgreSQL ``processing_status``。

配置由独立 ``OllamaEmbeddingSettings`` 读取：

```text
OLLAMA_BASE_URL=https://ollama.example.com
OLLAMA_EMBEDDING_MODEL=bge-m3:567m
OLLAMA_API_KEY=
OLLAMA_EMBEDDING_REQUEST_TIMEOUT_SECONDS=120
OLLAMA_EMBEDDING_BATCH_SIZE=16
```

API Key 允许为空并由 ``SecretStr`` 保护。非空时当前在
``config/ollama_embedding.py`` 的 ``build_ollama_headers()`` 中集中采用 Bearer
``Authorization`` 约定；如果反向代理实际使用其他 header，只调整这一处。不要把
真实密钥写入源码、测试、README 或 ``.env.example``。

## Qdrant 向量存储

Qdrant 使用官方 ``qdrant-client==1.19.0`` 写入已经由 LangChain
``OllamaEmbeddings`` 生成并校验的 Vector。当前 ``langchain-qdrant==1.1.0`` 的公开
写入方法会再次执行 Embedding，并固定嵌套 Metadata，不适合本项目的预计算向量和
扁平新闻 Payload，因此没有保留该依赖。LangChain 继续负责 Document、Chunk 和
Embedding，Qdrant client 只负责 Point 及 Collection/Alias 生命周期。

当前索引规格：

```text
model: bge-m3:567m
dimension: 1024
distance: Cosine
tokenizer: cl100k_base
chunk_size: 512
chunk_overlap: 96
schema_version: v1
```

物理 Collection 真正保存 Point：

```text
news_chunks_langchain_v1_001
```

所有应用 Point I/O 只访问稳定 Alias：

```text
news_chunks_langchain_current -> news_chunks_langchain_v1_001
```

Alias 只是一条指针，不保存数据。只有 ``QdrantCollectionLifecycle`` 可以直接操作物理
Collection；``QdrantChunkStore`` 的 upsert、scroll 和 delete 始终使用 current Alias。
``DocumentIndexingRuntime`` 是标准组装入口，保证模型、Chunk 参数、索引规格、Alias
Store 使用同一配置，并集中关闭 Ollama/Qdrant client；它只包含写入/lifecycle 组件，
不暴露 Search Service。模块 import 不会自动创建 Collection。索引调用方必须先显式
执行 ``ensure_ready()``，搜索则只读已经由部署准备好的 current Alias，绝不在失败时
隐式执行生命周期操作。

阶段 2 Qdrant 配置：

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

Payload 保存 Chunk 正文、稳定关系、标题、URL、来源、作者、标签、``published_at``、
``source_updated_at``、正文 hash、模型和 Schema 版本。第一批过滤索引建立在
``document_id``、``source_id``、``source_provider``、``document_type``、
``published_at`` 和 ``labels``。

Qdrant Cosine Collection 会在上传时执行 normalization，应用不重复做 L2
normalization；写入边界仍拒绝错误维度、非有限数值和零 L2 norm 向量。
统一 client builder 还显式使用 ``port=None``，避免 ``qdrant-client==1.19.0`` 给完整
HTTPS 反代 URL 强行追加默认 6333；URL 自带端口时仍会原样保留。

## Qdrant Vector Search

阶段 3 使用当前 ``qdrant-client==1.19.0`` 的异步 ``query_points()`` 直接查询预计算
query Vector；该版本客户端已不提供旧 ``search()`` 方法。搜索链路是：

```text
VectorSearchRequest.query
    -> OllamaEmbeddingProvider.embed_query()
    -> bge-m3:567m / 1024 维有限非零 Vector
    -> QdrantVectorSearch.query_points(current Alias)
    -> list[VectorSearchResult]
```

第一版约定：

```text
default top_k = 10
maximum top_k = 100
maximum query characters = 4096
score_threshold = None（请求可选，有限范围 [-1, 1]）
labels = MatchAny（命中任意标签；空数组表示不过滤）
published_from/to = 带时区且包含端点
```

所有不同 Payload 字段使用 AND 组合并由 Qdrant 在候选集合中执行。缺失
``published_at`` 的 Point 在没有时间条件时可以返回；一旦设置时间范围便不匹配。
结果顺序完全沿用 Qdrant Cosine score（通常越高越相似），不在 Python 中重排；同一
新闻多个 Chunk 可以分别返回，不做 document 聚合或新闻时间加权。

```python
from news_vector_service.config.ollama_embedding import (
    get_ollama_embedding_settings,
)
from news_vector_service.config.qdrant import get_qdrant_settings
from news_vector_service.qdrant.runtime import VectorSearchRuntime
from news_vector_service.schemas.vector_search import (
    VectorSearchFilters,
    VectorSearchRequest,
)

runtime = VectorSearchRuntime.build(
    get_qdrant_settings(),
    get_ollama_embedding_settings(),
)
try:
    # 搜索不会调用 ensure_ready，也不会创建/切换 Alias；部署必须先准备好索引。
    results = await runtime.service.search(
        VectorSearchRequest(
            query="央行近期是否调整利率？",
            top_k=10,
            filters=VectorSearchFilters(
                source_provider="freshrss_main",
                labels=["宏观", "利率"],
            ),
        )
    )
finally:
    await runtime.close()
```

``schemas/vector_search.py`` 只定义 Pydantic 请求/响应，``qdrant/search.py`` 只构造
Filter、查询 current Alias 和校验 ScoredPoint，``VectorSearchService`` 只编排 query
Embedding 与 Qdrant 读取。搜索不访问 PostgreSQL、不修改 ``processing_status``，不
调用 upsert/delete/create_collection/update_alias，也不会失败后回退到物理
Collection。完整概念、字段和错误说明见
[`docs/learning/04_vector_search.md`](docs/learning/04_vector_search.md)。

## Vector Search HTTP API

FastAPI 提供：

```text
POST /vector-search
```

JSON body 直接使用 ``VectorSearchRequest``，成功返回 ``VectorSearchResult[]``。空命中
返回 200 ``[]``；请求错误返回脱敏 422；Ollama/Qdrant 上游错误按类别返回稳定的
502/503/504 ``code/detail/retryable``，不会回显完整 query、Vector 或第三方响应。

HTTP 进程使用 ``VectorSearchRuntime``，只持有 Provider、Qdrant client、规格与搜索
组件，没有 lifecycle、Point Store、索引 Service 或 ``ensure_ready``。应用启动不访问
Ollama/Qdrant/PostgreSQL，也不会创建 Collection 或 Alias；真正请求才执行 Embedding
和 current Alias query。完整说明见
[`docs/learning/05_vector_search_api.md`](docs/learning/05_vector_search_api.md)。

## 文档级语义搜索与全文读取

当前浏览器工作台使用额外的只读接口：

```text
POST /document-search
GET  /documents/{document_id}
```

``POST /document-search`` 在 Qdrant 中使用正式的 ``query_points_groups()``，按
Payload ``document_id``（KEYWORD index）完成服务端分组。它不会先取 ``top_k`` Chunk
再由前端去重，因此 ``document_limit`` 始终限制不同新闻数量，
``matches_per_document`` 始终限制每篇新闻返回的相关片段数量：

```json
{
  "query": "央行近期是否调整利率？",
  "document_limit": 10,
  "matches_per_document": 3,
  "score_threshold": null,
  "filters": {"labels": ["宏观", "利率"]}
}
```

成功响应是 ``DocumentSearchResult[]``。每个文档包含 ``document_id``、
``content_hash``、标题、来源、时间、作者、标签、``chunk_count``、最高的
``best_score``、``best_match`` 和有限的 ``additional_matches``。后者只表示本次搜索
返回的相关片段，不是文章的全部物理 Chunk；组内和组间都按原始 Cosine score 降序，
score 不是概率或百分比。原有 ``POST /vector-search`` 仍返回原始
``VectorSearchResult[]``，语义和错误映射不变。

``GET /documents/{document_id}`` 只有用户打开“阅读全文”时才查询 PostgreSQL，使用
``DocumentRepository.get_with_source()`` eager-load source，并返回当前
``documents.content_text``、``content_hash``、``index_revision``（响应字段名
``revision``）以及展示元数据。搜索请求本身不查询 PostgreSQL，不产生 N+1。文档或
关联 source 不存在时返回固定脱敏 404；数据库不可用返回固定 503；不会返回 ORM 状态、
数据库地址或凭据。前端比较搜索结果 hash 与详情 hash；不一致时展示“该新闻已更新，
当前全文与搜索时的索引版本不同。”并使用 PostgreSQL 最新正文，不伪造历史版本。

Qdrant lifecycle 会为 ``document_id`` 确保 KEYWORD Payload index。对阶段 2 遗留的
UUID index，``ensure_current_collection()`` 只重建该索引，不修改 Point、Alias 或
正文；其他索引类型漂移仍会停止并报告配置错误。搜索 Runtime 仍不持有 lifecycle，
不会在请求路径执行任何写操作。

## 文档索引状态

``documents`` 继续保存业务事实，不保存 Chunk 或 Embedding。阶段 2 migration 增加：

```text
index_revision
indexed_revision
indexed_content_hash
indexed_schema_version
processing_started_at
indexed_at
last_processing_error
```

``DocumentIndexingService`` 负责编排：

```text
pending/failed -> processing
    -> Document/Chunk
    -> Ollama Embedding
    -> Qdrant current Alias upsert
    -> 删除同一新闻多余的旧 Chunk Point
    -> indexed

任一步失败 -> failed
```

新闻在处理期间更新时只递增 revision 并暂留 ``processing``；旧 Worker 结束后将新版本
释放为 ``pending``，避免新旧版本并发覆盖同一 Chunk UUID。

## 新闻同步与索引执行入口

阶段 5 CLI 和阶段 6 HTTP API 都是显式、一次性、有界的手动入口：

```powershell
# 只执行 FreshRSS -> PostgreSQL；每个白名单来源默认最多 2 篇
uv run news-vector-service sync-news --limit-per-source 2

# 显式准备 Qdrant langchain Alias，并顺序处理最多 20 个 pending/failed 文档
uv run news-vector-service index-pending --batch-size 20 --stale-after-minutes 60

# 先同步，再处理一个索引批次，然后退出
uv run news-vector-service run-once --limit-per-source 2 --batch-size 20
```

``sync-news`` 不构造 Qdrant/Ollama Runtime；``index-pending`` 在领取 PostgreSQL 候选
前调用一次 ``ensure_ready()``，随后逐篇使用独立 ``AsyncSession``。候选由条件 UPDATE
原子领取，多进程竞争时安全跳过。单篇失败会记录 UUID/异常类型并继续本批，但最终
JSON ``ok=false`` 且退出码为 1；完整异常、正文、Vector 和密钥不进入命令输出。

每次命令只处理一个有界批次，不暗中循环等待新任务。完整职责、事务和故障说明见
[`docs/learning/06_news_pipeline_execution.md`](docs/learning/06_news_pipeline_execution.md)。

HTTP 手动执行使用相同三个边界：

```text
POST /pipeline/run-once
{
  "limit_per_source": 2,
  "batch_size": 20,
  "stale_after_minutes": 60
}
```

请求会同步等待 ``FreshRSS -> PostgreSQL -> Ollama -> Qdrant`` 一轮完成，成功响应包含
来源、checkpoint、候选、indexed/skipped/failed 数量以及按 ``error_type`` 聚合的失败，
不返回正文、完整异常、Vector、凭据或数据库 URL。来源失败会回滚该来源页并继续其他
来源，随后仍索引成功保存的文档，但 ``ok=false``；订阅列表、配置或 lifecycle 等批次
级错误使用脱敏 5xx。写 API 按请求创建独立 ``PipelineWriteRuntime``，搜索仍使用不含
lifecycle、Point Store 和索引 Service 的 ``VectorSearchRuntime``。完整阶段 6 说明见
[`docs/learning/07_incremental_sync_content_quality_and_manual_api.md`](docs/learning/07_incremental_sync_content_quality_and_manual_api.md)。

## 模块边界

代码依赖方向保持为：

```text
config / schemas
        ↓
ingestion（外部协议访问与映射）
        ↓
domain（稳定的内部数据模型）
        ↓
repositories + models（PostgreSQL 持久化）
        ↓
pipeline（Document、Chunk、Ollama Embedding）
        ↓
services + qdrant（索引状态编排、Point/Payload、Collection/Alias 生命周期与只读搜索）
        ↓
pipeline write runtime（只组合手动同步与索引写路径；不提供搜索）
        ↓
api（HTTP 校验、按请求 Runtime、错误映射；不实现 Embedding/Qdrant 细节）
        ↘ cli（一次性写入命令组装；不实现 HTTP、定时器或无限循环）
```

`FreshRSSImportService` 只编排抓取、映射和事务，不处理 Chunk；`DocumentBuilder`
只做 ORM 到 RAG Document 的内存转换；`DocumentChunker` 只负责切分。调用方依赖
这两个 Pipeline 门面，不在业务代码中散落创建框架切分器。只有出现第二种真实
切分实现，或者不同文档类型确实需要不同策略时，才增加 Protocol 或策略选择器，
避免为尚不存在的变化建立空接口和工厂。

## 正文质量诊断

`ContentQualityNormalizer` 只自动处理确定性规则：HTML entity、NFC、Unicode 空白、
正文首尾与标题匹配的完整独立块，以及相邻且规范化后完全相同的完整段落。标题比较
只在首尾发生，并只忽略标点、空白和大小写；正文中间的重复句子和非相邻重复段落
保留。content 与 summary 按协议优先级选择一个，绝不拼接成整篇重复文本。

空标题、空正文和只剩标题分别以稳定质量原因失败，导致整页 rollback 且 checkpoint
不推进；“正文过短”只作为诊断信号，合法短快讯仍保存。历史记录无法区分 content 与
summary，诊断结果使用 ``unknown``，且不会自动回写。相同输入重复规范化产生相同正文
和 content_hash，因而不会无意义增加 revision 或改变稳定 Chunk ID。

## 当前数据库

```text
sources
    动态保存 Feed、机构或其他文档来源，以及来源级 sync_checkpoint/推进时间

documents
    保存清洗正文、来源关联、当前处理状态，以及 Qdrant 索引 revision/成功快照

users
    保存内部登录邮箱、Argon2 密码 Hash、启用/超级用户状态和唯一环境托管标记

access_tokens
    保存浏览器登录产生的可撤销随机 Token、创建时间和所属用户

alembic_version
    由 Alembic 维护当前数据库迁移版本
```

`documents` 保存清洗后的 `content_text`，不保存 FreshRSS 原始 HTML。作者、标签
和图片 URL 使用 PostgreSQL `text[]`。所有时间使用带时区 `datetime`，数据库
连接会话固定为 UTC。当前项目使用独立 Database ``news_vector_lc``，migration head
为 ``b7e1a4c9d203``，不会连接另一个项目的 ``news_vector`` 数据库。认证 migration
新增 users/access_tokens 和环境托管标记；仍未新增 Chunk、Embedding 或 pipeline_runs
表。

## 本地运行

```powershell
uv sync
Copy-Item .env.example .env
# 编辑 .env，同时填写 AUTH_ADMIN_EMAIL/AUTH_ADMIN_PASSWORD；
# 本地 HTTP 设置 AUTH_COOKIE_SECURE=false，生产 HTTPS 必须保持 true。
uv run alembic upgrade head
uv run news-vector-service run-once --limit-per-source 2 --batch-size 20
uv run uvicorn news_vector_service.main:app --reload --host 127.0.0.1 `
  --loop news_vector_service.runtime:selector_loop_factory
```

健康检查：

```text
http://127.0.0.1:8000/health
```

应用启动会在构造搜索 Runtime 前创建或同步 `.env` 中唯一的保底超级管理员。除
`/health` 和 `/auth/login` 外，下面接口都需要登录 Cookie。PowerShell 联调可先建立一个
会话；密码通过隐藏的凭据提示读取，不写入命令历史：

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

Vector Search：

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

文档分组搜索与按需全文：

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

手动执行 Pipeline：

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

登录、退出和当前用户契约分别是 ``POST /auth/login``、``POST /auth/logout`` 和
``GET /auth/me``；没有 ``/auth/register``。普通有效账号访问搜索/全文，只有
``is_superuser=true`` 可访问 ``/admin/users`` 账号管理 API 并执行 Pipeline。环境托管
管理员不能通过 API 停用、降级或重置密码；网页创建的其他账号可正常管理。生产仍必须
使用 HTTPS、Secure Cookie，并在网关限制登录频率、请求体、并发和 timeout；
``OLLAMA_API_KEY`` 与 ``QDRANT_API_KEY`` 只是服务访问上游的凭据，不能当作浏览器认证。
完整设计见
[`docs/learning/08_local_password_auth.md`](docs/learning/08_local_password_auth.md)。
生产部署见平台根目录 [`docs/vps_deployment.md`](../docs/vps_deployment.md)。

## 复制项目隔离

当前目录没有 `.git`，因此它不会继承旧项目的 Git remote、branch、index 或提交历史，
在本目录执行代码修改不会改动旧项目目录。以后若需要独立版本管理，可以在本目录
执行 `git init` 并配置新的 remote。

`.venv` 不能随项目目录复制。Windows 虚拟环境中的 `uvicorn.exe` 等启动器可能嵌入
旧目录的 Python 绝对路径，导致副本暗中加载旧项目环境。复制后应在副本根目录执行：

```powershell
uv venv --clear .venv
uv sync --all-groups
```

Git 隔离不等于运行时隔离。本项目 `.env` 已使用独立 PostgreSQL Database
``news_vector_lc``；另一个项目继续使用 ``news_vector``。两个服务同时运行还必须使用
不同端口，例如本项目可使用：

```powershell
uv run uvicorn news_vector_service.main:app --reload --port 8001 `
  --loop news_vector_service.runtime:selector_loop_factory
```

Qdrant 已使用 ``QDRANT_ENVIRONMENT``、Schema 版本和 generation 组成物理 Collection
名称，并使用环境隔离的 current Alias。以后接入 Redis 或容器编排时，也要分别设置
key 前缀、持久化目录、容器名和宿主机端口，避免两个实例共享状态或争用资源。

已验证的 RSS 地址、正文获取方式和 FreshRSS CSS selector 记录在
[`docs/rss_sources.md`](docs/rss_sources.md)。

学习说明：

- [阶段 0：Document 与 Chunk](docs/learning/00_document_pipeline.md)
- [阶段 1：Ollama Embedding](docs/learning/01_ollama_embedding.md)
- [阶段 2A：Qdrant 基础概念](docs/learning/02_qdrant_concepts.md)
- [阶段 2B：文档索引流水线](docs/learning/03_document_indexing_pipeline.md)
- [阶段 3：Qdrant Vector Search](docs/learning/04_vector_search.md)
- [阶段 4：Vector Search HTTP API](docs/learning/05_vector_search_api.md)
- [阶段 5：新闻同步与向量索引执行入口](docs/learning/06_news_pipeline_execution.md)
- [阶段 6：增量同步、内容质量与手动执行 API](docs/learning/07_incremental_sync_content_quality_and_manual_api.md)

默认测试完全离线，不访问 Ollama：

```powershell
uv run pytest -q
```

阶段 3 搜索聚焦测试使用 fake Embeddings 与内存 Qdrant，不访问任何远程服务：

```powershell
uv run pytest -q tests/test_vector_search.py tests/test_qdrant_runtime.py
```

阶段 4 HTTP API 测试使用 fake Runtime 与 httpx ASGITransport，完全离线：

```powershell
uv run pytest -q tests/test_vector_search_api.py tests/test_qdrant_runtime.py
```

阶段 5 CLI/批次测试使用 fake Session、Repository、导入 Service 和索引 Service：

```powershell
uv run pytest -q tests/test_news_pipeline_execution.py tests/test_cli.py
```

阶段 6 增量、正文和手动 API 测试使用 continuation/事务 fake 与 ASGITransport：

```powershell
uv run pytest -q tests/test_freshrss_incremental_sync.py `
  tests/test_content_quality.py tests/test_pipeline_api.py
```

显式验证真实 Ollama query/document Embedding、内存 Qdrant current Alias 和
``POST /vector-search`` 的完整链路；Qdrant 数据只存在于测试进程内：

```powershell
$env:RUN_VECTOR_SEARCH_OLLAMA_INTEGRATION_TEST="1"
uv run pytest -q tests/test_vector_search_ollama_integration.py
```

显式执行真实只读 Vector Search；它不会创建或写入远程 Qdrant，只查询 current Alias，
且允许合法返回零条结果：

```powershell
$env:RUN_VECTOR_SEARCH_INTEGRATION_TEST="1"
uv run pytest -q tests/test_vector_search_integration.py
```

仅在明确允许访问当前 ``.env`` 指向的服务时启用只读集成测试。测试只发送短小、
无敏感信息的中文文本，不打印密钥或完整向量，也不修改远程数据：

```powershell
$env:RUN_OLLAMA_INTEGRATION_TEST="1"
uv run pytest -q tests/test_ollama_embedding_integration.py
```

验证真实 PostgreSQL revision/状态条件更新；测试使用随机临时记录并自动清理：

```powershell
$env:RUN_POSTGRES_INDEX_STATE_INTEGRATION_TEST="1"
uv run pytest -q tests/test_document_index_state_integration.py
```

验证真实 Ollama 1024 维 Vector 通过 current Alias 写入内存 Qdrant；不修改远程
Qdrant：

```powershell
$env:RUN_QDRANT_OLLAMA_INTEGRATION_TEST="1"
uv run pytest -q tests/test_qdrant_ollama_integration.py
```

仅在 `.env` 已配置远程 Qdrant 且明确允许创建临时测试资源时，验证真实
Collection/Alias/Point 生命周期；测试使用随机隔离名称并尝试自动清理：

```powershell
$env:RUN_QDRANT_REMOTE_INTEGRATION_TEST="1"
uv run pytest -q tests/test_qdrant_remote_integration.py
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

自动生成的迁移必须人工审查，不能把数据库迁移放进每个 FastAPI Worker 的启动
流程。部署时应先由单独步骤执行 `alembic upgrade head`，成功后再启动应用。
