# Agent Lab 后端

本服务把 FreshRSS 新闻同步成 PostgreSQL 业务事实，用 LangChain 切分成 Chunk、经
Ollama ``bge-m3:567m`` 生成 Embedding 写入 Qdrant，并对外提供受登录保护的**只读**语义
检索接口。写入链路只有显式手动入口（CLI 与一个同步 HTTP 接口），没有调度器、常驻
Worker 或后台任务。

本文档描述**当前状态**，分三部分：

- [当前能力](#当前能力)：现在真实可用的功能与契约。
- [边界](#边界)：外部依赖、配置、前置条件、运行与排障。
- [明确不做](#明确不做)：范围外的内容，以及为什么不提前做。

## 当前能力

### 对外 HTTP 接口

```text
GET  /health                              应用与 PostgreSQL 连通性（无需登录）
POST /auth/login                          账号密码登录，签发 HttpOnly Cookie（无需登录）
POST /auth/logout                         撤销当前 Token
GET  /auth/me                             当前账号的最小身份与权限字段
POST /vector-search                       Chunk 级只读语义检索
POST /document-search                     文档分组只读语义检索
GET  /documents/{document_id}             按需读取 PostgreSQL 完整正文
POST /pipeline/run-once                   手动、同步、有界的写入流水线（超级用户）
GET    /admin/users                       账号列表（超级用户）
POST   /admin/users                       创建账号（超级用户）
PATCH  /admin/users/{user_id}             改启用状态与超级用户位（超级用户）
POST   /admin/users/{user_id}/password    重置密码（超级用户）
DELETE /admin/users/{user_id}/sessions    撤销该账号全部登录会话（超级用户）
```

除 ``/health`` 和 ``/auth/login`` 外都需要有效登录 Cookie。搜索与全文要求普通启用
账号，``/pipeline/run-once`` 与 ``/admin/users`` 要求 ``is_superuser=true``。
**没有 ``/auth/register``**，账号只能由超级用户或 CLI 创建。

### 账号与权限

登录、退出由 FastAPI Users 的 Cookie backend 提供，``api/auth.py`` 只额外挂一个安全的
``GET /auth/me``。Token 是 DatabaseStrategy 保存在 ``access_tokens`` 的可撤销随机值，
浏览器只拿到 HttpOnly Cookie。

应用启动时在构造搜索 Runtime 之前同步 ``.env`` 中唯一的保底超级管理员
（``sync_configured_environment_admin()``）。该账号带环境托管标记，**不能**通过 API 停用、
降级或重置密码（``environment_admin_protected``）；网页创建的其他账号可正常管理。
``UserAdminService`` 另外保护「最后一个超级用户」（``last_superuser_protected``），并对
重复邮箱和弱密码返回 ``user_already_exists`` / ``invalid_password``。

### FreshRSS 增量同步

同步使用显式分类白名单，不读取总阅读列表：

```text
FRESHRSS_SYNC_CATEGORIES=["新闻","财经","宏观数据"]
```

``FreshRSSImportService.import_recent_per_source()`` 从至少属于一个允许分类的每个订阅源
读取一页。首次运行保存最近的有界基线；之后使用 FreshRSS numeric ``continuation`` 从已
提交 checkpoint 之后按旧到新追赶，因此两次手动运行之间到达的新闻不会因「只看最近 N
篇」被静默越过。同一订阅属于多个允许分类时只处理一次；``source_id + external_id``
保持幂等，完全相同的文章不会新增行或递增 revision。

「文档 + checkpoint」在同一个数据库事务提交，checkpoint 用条件 UPDATE 推进（WHERE 带读到
的旧值），因此外部 cron 或多实例并发时不会把游标退回旧值。条件不满足不算错误：文档仍幂等
提交，只是本次不报告游标推进（竞态窗口的完整说明见 ``_save_source_page`` docstring）。

### 正文质量规范化

``ingestion/content_quality.py`` 是 FreshRSS Mapper 的统一规范化路径，也可用于历史正文的只读
诊断。新文章在计算 content_hash、revision、构建 Document 和 Chunk **之前**完成规范化。

``ContentQualityNormalizer`` 只自动处理确定性规则：HTML entity、Unicode NFC、Unicode 空白、
正文首尾与标题匹配的完整独立块，以及相邻且规范化后完全相同的完整段落。标题比较只在首尾发生，
并只忽略标点、空白和大小写；正文中间的重复句子和非相邻重复段落保留。content 与 summary 按
协议优先级选择一个，绝不拼接。相同输入重复规范化产生相同正文与 content_hash，不会无意义增加
revision 或改变稳定 Chunk ID。

空标题、空正文和只剩标题分别以稳定质量原因失败，导致整页 rollback 且 checkpoint 不推进；
「正文过短」只作为诊断信号，合法短快讯仍保存。历史记录无法区分 content 与 summary，诊断结果
使用 ``unknown``。

### 数据对象分层

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

这些层不能合并：外部协议会变化，内部模型需要稳定，数据库只保存需要持久化的字段，LangChain
Document 则服务于 Chunk 和 Embedding。

RSS 来源是否兼容在 ``FreshRSSItemMapper`` 这一层决定：只要 FreshRSS 提供文章 URL、非空标题和
可读的正文或摘要且能通过协议模型校验，就能转换成统一 ``SourceDocument``。

### LangChain Document 与 Chunk

``pipeline/document_builder.py`` 把 ``DocumentRecord`` 转换成 LangChain ``Document``：正文放入
``page_content``，标题、来源和过滤字段放入 ``metadata``，PostgreSQL 文档 UUID 作为稳定的
``id``。只有 ``page_content`` 参与 Embedding，UUID、外部 ID、URL 和发布时间不拼入向量文本。
构建器不访问数据库，因此查询 ``DocumentRecord`` 时必须提前加载 ``source`` 关系（例如
``selectinload(DocumentRecord.source)``），避免异步 SQLAlchemy 读取关系属性时发生隐式 I/O。

``pipeline/document_chunker.py`` 使用 ``RecursiveCharacterTextSplitter`` 切分。默认
``cl100k_base`` tokenizer、512 token Chunk 上限、96 token 重叠上限，不按 Python 字符数计量；
中英文段落、句末标点、逗号和空格按优先级递归切分。

每个 Chunk 复制父 Metadata，并增加 ``parent_document_id``、``chunk_index``、``chunk_count``、
``previous_chunk_id`` 和 ``next_chunk_id``。Chunk ID 由父文档 UUID、tokenizer、切分参数和序号
经 uuid5 稳定生成，重复处理相同版本不产生随机新 ID。切分后先过滤空白 Chunk 和同文档完全重复
的 ``page_content``，再**按去重后的最终列表**重建 index/count/previous/next，避免被丢弃的片段
在关系链上留下空洞。Chunk 不写 PostgreSQL。

串接入口是 ``DocumentChunkPipeline.build_chunks(record)``：传入已 eager-load ``source`` 的
``DocumentRecord``，按固定顺序执行 ``DocumentBuilder -> DocumentChunker``。

### Ollama Embedding

``pipeline/ollama_embedding_provider.py`` 统一创建官方 ``OllamaEmbeddings``，业务层不直接拼装
客户端。它提供异步 query、document、Chunk 批量调用和真实维度探测；空列表不访问网络，非空
列表严格按 ``OLLAMA_EMBEDDING_BATCH_SIZE`` 分批并保持顺序。

每批响应都会验证数量、非空向量、数值类型、NaN/Infinity 和维度，不同批次及同一 Provider
生命周期中的维度也必须稳定。向量维度取自服务真实返回长度，不在源码中硬编码。Provider 只
返回内存结果，``DocumentIndexingService`` 才在完整 Qdrant 写入后更新 ``processing_status``。

### Qdrant 向量存储

Qdrant 使用官方 ``qdrant-client``（当前 lock 解析为 1.19.0）写入已由 ``OllamaEmbeddings`` 生成
并校验的 Vector。``langchain-qdrant`` 的公开写入方法会再次执行 Embedding 并固定嵌套 Metadata，
不适合本项目的预计算向量和扁平新闻 Payload，因此**不是本项目依赖**。LangChain 负责 Document、
Chunk 和 Embedding，Qdrant client 只负责 Point 及 Collection/Alias 生命周期。

当前索引规格（``qdrant/index_spec.py`` 的 ``VectorIndexSpec``）：

```text
model: bge-m3:567m
dimension: 1024
distance: Cosine
tokenizer: cl100k_base
chunk_size: 512
chunk_overlap: 96
schema_version: v1
payload_schema_version: v1
```

``schema_version`` 代表整个索引空间的版本，不只是数据库迁移版本：模型、维度、Distance、
tokenizer、Chunk 参数或 Payload 契约任一不兼容，就必须换新版本。

物理 Collection 真正保存 Point，名称由 ``QDRANT_ENVIRONMENT``、Schema 版本和 generation 组合；
所有应用 Point I/O 只访问稳定 Alias：

```text
news_chunks_langchain_v1_001                                    物理 Collection
news_chunks_langchain_current -> news_chunks_langchain_v1_001    应用只用这个
```

只有 ``QdrantCollectionLifecycle`` 直接操作物理 Collection；``QdrantChunkStore`` 的 upsert、
scroll 和 delete 始终使用 current Alias。索引调用方必须先显式 ``ensure_ready()``，搜索则只读
已由部署准备好的 current Alias。

Payload 保存 Chunk 正文、稳定关系、标题、URL、来源、作者、标签、``published_at``、
``source_updated_at``、正文 hash、模型和 Schema 版本。过滤/分组索引
（``qdrant/lifecycle.py`` 的 ``PAYLOAD_INDEX_SCHEMAS``）：

```text
document_id      KEYWORD    （grouped query 的 group_by 要求 keyword/integer）
source_id        UUID
source_provider  KEYWORD
document_type    KEYWORD
published_at     DATETIME
labels           KEYWORD
```

``ensure_current_collection()`` 对 ``document_id`` 上历史遗留的 UUID 索引只重建该索引，不
修改 Point、Alias 或正文；**其他**索引类型漂移会停止并抛 ``VectorIndexConfigurationError``。

写入边界拒绝错误维度、非有限数值和零 L2 norm 向量。统一 client builder 显式使用
``port=None``，避免 qdrant-client 给完整 HTTPS 反代 URL 强行追加默认 6333；URL 自带端口时仍
原样保留。

### 两个 Runtime：读写权限分离

``qdrant/runtime.py`` 提供两个 Runtime，共享零件由模块级 ``_build_shared_components()``
组装（规格、Qdrant client、Embedding Provider），``_close_shared_clients()`` 负责「两个
client 都尝试关闭、保留第一个异常为根因」。刻意用函数而不是共同基类：两者的分裂是**权限
边界**，只读 Runtime 绝不能通过继承意外获得写能力。

```text
VectorSearchRuntime      只读。Provider + Qdrant client + 规格 + 只查 current Alias 的
                         搜索组件。没有 lifecycle、Point Store、索引 Service 或
                         ensure_ready。lifespan 启动时创建一次，全部请求共享。

DocumentIndexingRuntime  写入。额外持有切分流水线、Collection/Alias lifecycle 和只用
                         current Alias 的 Point Store，提供 ensure_ready()。

PipelineWriteRuntime     手动写入编排（pipeline/write_runtime.py）。绑定 FreshRSS、
                         Session factory 与索引写路径，按请求新建、请求结束整体关闭。
```

### Chunk 级语义检索：POST /vector-search

搜索链路：

```text
VectorSearchRequest.query
    -> OllamaEmbeddingProvider.embed_query()
    -> bge-m3:567m / 1024 维有限非零 Vector
    -> QdrantVectorSearch.query_points(current Alias)
    -> list[VectorSearchResult]
```

请求契约（``schemas/vector_search.py``）：

```text
default top_k = 10          （DEFAULT_TOP_K）
maximum top_k = 100         （MAX_TOP_K）
maximum query characters = 4096  （MAX_QUERY_CHARACTERS）
score_threshold = None      （可选，有限范围 [-1, 1]，拒绝 bool 与字符串）
labels = MatchAny           （命中任意标签；空数组表示不过滤）
published_from/to           （带时区且包含端点）
```

不同 Payload 字段以 AND 组合并由 Qdrant 在候选集合中执行。缺失 ``published_at`` 的 Point 在没有
时间条件时可以返回，一旦设置时间范围便不匹配。结果顺序完全沿用 Qdrant Cosine score，不在
Python 中重排；同一新闻的多个 Chunk 可以分别返回，不做 document 聚合或时间加权。成功返回
``VectorSearchResult[]``，空命中返回 200 ``[]``。

程序内直接使用 Runtime（不经 HTTP）：

```python
from agent_lab.config.ollama_embedding import get_ollama_embedding_settings
from agent_lab.config.qdrant import get_qdrant_settings
from agent_lab.qdrant.runtime import VectorSearchRuntime
from agent_lab.schemas.vector_search import VectorSearchFilters, VectorSearchRequest

runtime = VectorSearchRuntime.build(
    get_qdrant_settings(), get_ollama_embedding_settings()
)
try:
    # 搜索不会调用 ensure_ready，也不会创建/切换 Alias；部署必须先准备好索引。
    results = await runtime.service.search(
        VectorSearchRequest(
            query="央行近期是否调整利率？",
            top_k=10,
            filters=VectorSearchFilters(labels=["宏观", "利率"]),
        )
    )
finally:
    await runtime.close()
```

### 文档级语义检索：POST /document-search

``POST /document-search`` 使用 Qdrant 正式的 ``query_points_groups()``，按 Payload
``document_id``（KEYWORD index）完成**服务端**分组。它不先取 ``top_k`` Chunk 再由前端
去重，因此 ``document_limit`` 始终限制不同新闻数量，``matches_per_document`` 始终限制
每篇新闻返回的相关片段数量：

```json
{
  "query": "央行近期是否调整利率？",
  "document_limit": 10,
  "matches_per_document": 3,
  "score_threshold": null,
  "filters": {"labels": ["宏观", "利率"]}
}
```

```text
default document_limit = 10        maximum = 100
default matches_per_document = 3   maximum = 20
```

成功响应是 ``DocumentSearchResult[]``。每个文档包含 ``document_id``、``content_hash``、标题、
来源、时间、作者、标签、``chunk_count``、最高的 ``best_score``、``best_match`` 和有限的
``additional_matches``。后者只表示本次搜索返回的相关片段，不是文章的全部物理 Chunk；组内和组间
都按原始 Cosine score 降序，score 不是概率或百分比。

组间排序键是 ``(-score, str(document_id))``：分数降序、并列时按文档 ID 字典序升序，保证同样输入
永远产出同样输出。用负号而不是 ``reverse=True``，是因为后者会把第二个键也翻成 Z→A。

### Qdrant 响应的信任边界

``qdrant/search.py`` 是「Qdrant 响应可信度」的信任边界：Qdrant 返回的内容一律当作外部不可信
输入，Point/Payload 契约和文档分组的跨 Chunk 不变量都在这里一次验干净。``search_groups()``
保证：组非空、组内每个 Payload 的 document_id 等于本组 ``document_id``、组内 chunk_id 互不
重复、文档级元数据（``content_hash``、``title``、``url``、``source_name``、``published_at``、
``authors``、``labels``、``chunk_count``）组内一致、``matches`` 按 score 降序。

因此下游**不再重复校验**：``VectorSearchService._map_document_group()`` 只做纯字段搬运，
``DocumentSearchResult`` 只做字段级约束。

``_validate_payload_json_types()`` 只补 Pydantic 覆盖不到的那一类漂移：目标类型不是 ``str`` 的
字段（``document_id``/``source_id``/``*_chunk_id`` 声明为 ``UUID``，``published_at``/
``source_updated_at`` 声明为 ``datetime``）。Pydantic 宽松模式会接受真正的 ``UUID`` 对象，也会
把整数当 Unix timestamp 解析成 ``datetime``，从而把写坏或来自旧 Schema 的 Payload 悄悄「修复」
成看似合法的结果。其余字段不再手工检查：Pydantic 的 ``str`` 解析已拒绝非字符串 JSON 类型，
``chunk_index``/``chunk_count`` 用了 ``strict=True``，``authors``/``labels`` 有要求 JSON array
的 before-validator。

### 按需读取全文：GET /documents/{document_id}

只有用户打开「阅读全文」时才查询 PostgreSQL，使用 ``DocumentRepository.get_with_source()``
eager-load source，返回当前 ``documents.content_text``、``content_hash``、``index_revision``
（响应字段名 ``revision``）以及展示元数据。搜索请求本身不查询 PostgreSQL，不产生 N+1。

文档或关联 source 不存在返回固定脱敏 404；数据库不可用返回 503；数据库记录违反公开契约返回
502（只记异常类型，不把字段值或正文写进日志）。前端比较搜索结果 hash 与详情 hash，不一致时
提示新闻已更新，并使用 PostgreSQL 最新正文，不伪造历史版本。

### 集中的错误契约：api/error_contract.py

搜索、文档搜索、手动流水线和账号管理四类路由共用一个错误契约层，映射收在有序的
``ErrorContractRule`` 表里，各路由只负责「catch 什么异常」和「记什么日志」。

三条必须长期保住的设计约束：

1. **只读异常的「类型」。** 全模块不出现 ``str(error)``、``error.args`` 或任何异常文本
   拼接，因此数据库 URL、API Key、用户 query、新闻正文、Vector 和第三方原始响应都不可能
   进入 HTTP 响应。查表天然满足这一点，新增规则时不要破坏它。
2. **表的顺序有语义。** 刻意从「具体子类」排到「基础异常」，``resolve_error_contract()``
   返回第一条 ``isinstance`` 命中的规则。若把基类规则提前，它会吞掉后面的子类规则。
3. **``code`` 是对外稳定契约**，只能新增不能改值；``detail`` 只是给人看的中文概述，
   同一个 ``code`` 在所有表里必须对应同一句 ``detail``。

``tests/test_error_contract.py`` 守护上述三条。其中 detail 文案的检查不止覆盖规则表：
它用 AST 扫 ``src/agent_lab`` 全树，把 ``detail=`` 关键字实参、``*_DETAIL`` 模块常量和
``UserAdminDomainError(code, detail)`` 的位置实参一并纳入，要求都是以「。」收尾的中文句子。
原因是 ``documents.py`` / ``health.py`` 用裸 ``HTTPException``、账号管理领域错误按 ``code``
而非异常类型分支，都进不了类型键控的规则表——历史上 ``health.py`` 就漏过一次句尾句号。

四张表与对应的响应构造器：

```text
VECTOR_SEARCH_ERROR_RULES   build_vector_search_error_response()
    search_runtime_unavailable 503 / embedding_authentication_failed 502 /
    embedding_timeout 504 / embedding_unavailable 503 / embedding_model_not_found 503 /
    embedding_response_invalid 502 / qdrant_authentication_failed 502 /
    qdrant_timeout 504 / qdrant_unavailable 503 / qdrant_target_missing 503 /
    qdrant_configuration_invalid 503 / qdrant_response_invalid 502 /
    qdrant_service_error 502

PIPELINE_ERROR_RULES        build_pipeline_error_response()
    freshrss_authentication_failed 502 / freshrss_unavailable 503 /
    freshrss_timeout 504 / freshrss_response_invalid 502 / freshrss_sync_failed 502 /
    postgresql_unavailable 503 / embedding_* （与读链路共享四条 Ollama 规则）/
    embedding_failed 502 / qdrant_configuration_invalid 503 / qdrant_unavailable 503 /
    qdrant_write_failed 502 / pipeline_configuration_invalid 503 /
    pipeline_timeout 504 / pipeline_runtime_unavailable 503

USER_ADMIN_ERROR_RULES      build_user_admin_error_response()
    user_admin_database_unavailable 503

UNCLASSIFIED_ERROR_RULE     全项目唯一的未分类兜底：pipeline_internal_error 500
INVALID_REQUEST_RULE        请求校验失败：invalid_request 422
```

两处刻意保留的分叉，不能合并改值：读链路把 ``EmbeddingResponseError`` 归为
``embedding_response_invalid``，写链路归为 ``embedding_failed``；账号管理的
``user_admin_database_unavailable`` 与流水线的 ``postgresql_unavailable`` 是两个既有契约值。

搜索错误响应固定三字段 ``code``/``detail``/``retryable``（``retryable=true`` 只表示稍后重试
可能恢复，不代表服务会自动重试）；流水线响应多一个 ``error_type``，只放异常的 Python 类名。

两条搜索路由通过共享的 ``SEARCH_UPSTREAM_EXCEPTIONS``（``OllamaEmbeddingError``、
``QueryVectorValidationError``、``QdrantVectorSearchError``）捕获已分类上游失败。它不含
``VectorSearchRuntimeUnavailableError``——那个由依赖注入在进入 endpoint 前抛出，endpoint 内的
try 接不到，统一由应用级 handler 映射成同一个 503。

### 422 脱敏的两个层级

``main.py`` 的应用级 ``RequestValidationError`` handler 是默认兜底：只保留字段位置、稳定错误
类型和安全消息，丢弃 ``input`` 和 ``ctx``（它们可能带着用户提交的完整 query）。

需要更强脱敏的路由改用 ``SanitizedValidationRoute``（``APIRoute`` 子类），把校验失败收敛成单一
``invalid_request``。做成 route class 而不是在装配根判断 URL 前缀，是因为脱敏是**路由自身的
属性**（它的请求体里有明文密码），不是 ``main.py`` 要维护的一串路径常量。当前只有
``/admin/users`` 路由族挂了它。

### 共享的依赖注入与校验器

``api/dependencies.py`` 位于 FastAPI 边界层最底部，只从 ``application.state`` 取装配根放进
去的组件，不构造 Runtime、不做任何 I/O。``get_vector_search_service()`` 取进程级共享
Service；``get_pipeline_write_runtime_factory()`` 取「能造写 Runtime 的函数」，真正构造发生
在调用方，每请求一个新 Runtime。取不到就抛 ``VectorSearchRuntimeUnavailableError`` /
``PipelineWriteRuntimeUnavailableError``，由错误契约层映射成稳定 503。存在的意义是让
``vector_search`` 与 ``document_search`` 这类平级路由都依赖公共模块，而不是互相 import。

``schemas/_query_validators.py`` 提供 ``require_non_whitespace_query()`` 和
``require_numeric_threshold()``，被 ``VectorSearchRequest`` 与 ``DocumentSearchRequest`` 共用。
两者是独立请求契约（字段集合不同，不能合并成一个模型），但对 ``query`` 和 ``score_threshold``
的要求必须完全一致——**包括错误文案**，因为 422 响应体里的文案属于对外契约且有测试直接断言。

### 文档索引状态机

``documents`` 继续保存业务事实，不保存 Chunk 或 Embedding。索引状态列：

```text
processing_status        pending | processing | indexed | failed
index_revision          当前业务版本（>= 1）
indexed_revision        成功写入 Qdrant 的版本快照
indexed_content_hash
indexed_schema_version
processing_started_at
indexed_at
last_processing_error
```

关键设计是「派生副本」与「业务事实」分开：``index_revision`` 是业务版本，``indexed_*`` 是成功
快照。

``DocumentIndexingService`` 的编排：

```text
1. claim        条件 UPDATE 把 pending/failed 原子改成 processing，抢不到就跳过
2. 处理          Document/Chunk -> Ollama Embedding -> Qdrant current Alias upsert
                 -> 删除同一新闻多余的旧 Chunk Point
3. mark_indexed 带 revision 条件更新 -> indexed
                 条件不满足说明处理期间有新版本 -> release_stale_claim 放回 pending
4. 任一步异常    尽力 mark_failed（也带 revision 条件），再原样抛出
```

失败也写 ``failed`` 而不是直接抛，是为了让文档保持可被下轮重新领取的状态。新闻在处理期间更新
时只递增 revision 并暂留 ``processing``，旧 Worker 结束后把新版本释放为 ``pending``，避免新旧
版本并发覆盖同一 Chunk UUID。

### 手动写入入口

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

CLI 与 HTTP 共用 ``pipeline/limits.py`` 的有界参数：

```text
limit_per_source     默认 2    最大 100
batch_size           默认 20   最大 1000
stale_after_minutes  默认 60   最大 10080（7 天）
```

``sync-news`` 不构造 Qdrant/Ollama Runtime；``index-pending`` 在领取 PostgreSQL 候选前调用一次
``ensure_ready()``，随后逐篇使用独立 ``AsyncSession``。候选由条件 UPDATE 原子领取，多进程竞争
时安全跳过。单篇失败会记录 UUID 与异常类型并继续本批，但最终 JSON ``ok=false`` 且退出码为 1；
完整异常、正文、Vector 和密钥不进入命令输出。

``POST /pipeline/run-once``（超级用户）接受同样三个边界参数，同步等待
``FreshRSS -> PostgreSQL -> Ollama -> Qdrant`` 一轮完成。成功响应包含来源、checkpoint、候选、
indexed/skipped/failed 数量以及按 ``error_type`` 聚合的失败，不返回正文、完整异常、Vector、
凭据或数据库 URL。来源失败会回滚该来源页并继续其他来源，随后仍索引成功保存的文档，但
``ok=false``（HTTP 仍为 200）；订阅列表、配置或 lifecycle 等批次级错误使用脱敏 5xx。写 API 按
请求创建独立 ``PipelineWriteRuntime``。

每次命令或请求只处理一个有界批次，不暗中循环等待新任务。

### 模块边界

```text
config / schemas
        ↓
ingestion（外部协议访问、正文规范化与映射）
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
api（HTTP 校验、按请求 Runtime、错误契约；不实现 Embedding/Qdrant 细节）
        ↘ cli（一次性写入命令组装；不实现 HTTP、定时器或无限循环）
```

``api/`` 内部再分一层：``dependencies.py`` 与 ``error_contract.py`` 是基础设施，
``vector_search.py``、``document_search.py``、``documents.py``、``pipeline.py``、
``user_admin.py``、``auth.py``、``health.py`` 是平级特性路由，彼此不互相 import；
``main.py`` 是唯一的装配根。

``FreshRSSImportService`` 只编排抓取、映射和事务，不处理 Chunk；``DocumentBuilder`` 只做 ORM 到
RAG Document 的内存转换；``DocumentChunker`` 只负责切分。调用方依赖 Pipeline 门面，不在业务代码
里散落创建框架切分器。

## 边界

### 外部依赖

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

### 启动前置条件

```text
1. alembic upgrade head 已完成          （启动不执行 migration）
2. Qdrant current Alias 已存在          （搜索不会 ensure_ready）
3. .env 配置合法                         （启动即读，非法配置直接失败）
```

应用启动**只**访问 PostgreSQL 同步环境托管管理员，不探测 FreshRSS、Ollama 或 Qdrant，
也不创建 Collection 或 Alias。真正的 Embedding 与 current Alias query 只在收到请求时
执行；新闻同步与索引只在手动 CLI 或 ``POST /pipeline/run-once`` 时发生。

### 配置

完整模板见 ``.env.example``。``uv run`` 会自动加载 ``.env``。

数据库与登录 Cookie：

```text
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/news_vector_lc
DATABASE_ECHO=false
DATABASE_CONNECT_TIMEOUT=5
DATABASE_HEALTH_CHECK_TIMEOUT=5
DATABASE_TIMEZONE=UTC
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10

AUTH_COOKIE_NAME=news_auth
AUTH_COOKIE_SECURE=true          # 生产 HTTPS 必须 true；本地 http 联调才设 false
AUTH_COOKIE_SAMESITE=strict      # 只允许 strict 或 lax
AUTH_SESSION_LIFETIME_SECONDS=28800
# 环境托管保底超级管理员：必须同时配置或同时保持注释。
# 留成 AUTH_ADMIN_EMAIL= 这样的空值会因邮箱格式校验直接启动失败。
# AUTH_ADMIN_EMAIL=admin@example.com
# AUTH_ADMIN_PASSWORD=<12 到 128 字符，且不能等于邮箱>
```

FreshRSS：

```text
FRESHRSS_PROVIDER_KEY=freshrss_main
FRESHRSS_API_BASE_URL=https://freshrss.example.com/api/
FRESHRSS_USERNAME=
FRESHRSS_API_PASSWORD=
FRESHRSS_REQUEST_TIMEOUT_SECONDS=15
FRESHRSS_VERIFY_SSL=true
FRESHRSS_SYNC_CATEGORIES=["新闻","财经","宏观数据"]
```

Ollama Embedding（由独立 ``OllamaEmbeddingSettings`` 读取）：

```text
OLLAMA_BASE_URL=https://ollama.example.com
OLLAMA_EMBEDDING_MODEL=bge-m3:567m
OLLAMA_API_KEY=
OLLAMA_EMBEDDING_REQUEST_TIMEOUT_SECONDS=120
OLLAMA_EMBEDDING_BATCH_SIZE=16
```

Qdrant：

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

``OLLAMA_API_KEY`` 与 ``QDRANT_API_KEY`` 允许为空并由 ``SecretStr`` 保护。非空时在
``config/ollama_embedding.py`` 的 ``build_ollama_headers()`` 中集中采用 Bearer
``Authorization`` 约定；如果反向代理实际使用其他 header，只调整这一处。这两个 Key 只是
服务访问上游的凭据，**不能**当作浏览器认证。不要把真实密钥写入源码、测试、README 或
``.env.example``。

修改 ``QDRANT_DISTANCE`` 或维度必须新建 Schema/Collection，不能原地改。

### 数据库

```text
sources          Feed、机构或其他文档来源，以及来源级 sync_checkpoint 与推进时间
documents        清洗正文、来源关联、当前处理状态，以及 Qdrant 索引 revision/成功快照
users            内部登录邮箱、Argon2 密码 Hash、启用/超级用户状态和唯一环境托管标记
access_tokens    浏览器登录产生的可撤销随机 Token、创建时间和所属用户
alembic_version  由 Alembic 维护当前迁移版本
```

``documents`` 保存清洗后的 ``content_text``，不保存 FreshRSS 原始 HTML。作者、标签和
图片 URL 使用 PostgreSQL ``text[]``。所有时间使用带时区 ``datetime``，数据库连接会话
固定为 UTC。仍未新增 Chunk、Embedding 或 pipeline_runs 表。

### 本地运行

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

### PowerShell 联调

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

### 生产前置要求

生产必须使用 HTTPS 与 Secure Cookie，并在网关限制登录频率、请求体大小、并发和 timeout
——服务本身不做这些。部署步骤见平台根目录
[`docs/vps_deployment.md`](../docs/vps_deployment.md)。

数据库迁移不能放进每个 FastAPI Worker 的启动流程：部署时先由单独步骤执行
``alembic upgrade head``，成功后再启动应用。

### 测试

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

### Alembic

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

自动生成的迁移必须人工审查。

### 运行时隔离

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

### 参考文档

已验证的 RSS 地址、正文获取方式和 FreshRSS CSS selector 记录在
[`docs/rss_sources.md`](docs/rss_sources.md)。

跨会话需要留存的架构决策记录在平台根 [`docs/adr/`](../docs/adr/)，按 `NNNN-slug.md`
编号。本文描述系统当前是什么样，ADR 解释某处为什么是这样、以及当时放弃了什么。

## 明确不做

以下都是**有意**不做的，不是遗漏。改变其中任何一条前，先确认需求真实存在。

### 当前范围是只读语义检索

现在没有生成式 LLM 调用、对话历史、Prompt 模板层和 SSE/WebSocket 流式输出。检索结果直接
返回原始 Chunk 与 Cosine score，不做摘要、改写或答案生成。这是当前形态的描述，不是禁令——
真的要做生成式能力时按需要加。

要避免的只是**没有真实行为的占位层**：空接口和占位工厂比缺失的功能更难删除，一个没有第二种
实现的 Protocol 会让每个读代码的人以为存在多态，一个只有一条分支的策略选择器会让调用方
以为可以替换。因此等出现第二种真实实现再引入抽象。

### 不做自动化执行

没有调度器、常驻 Worker、后台 asyncio Task、队列、Celery 或定时器。写入只有两个入口：
CLI 子命令和 ``POST /pipeline/run-once``，两者都是显式触发、同步等待、单批次有界。
命令不暗中循环等待新任务；HTTP 请求不创建后台任务就返回。

需要周期执行时由外部 cron 或 systemd timer 调用 CLI——这也是 ``_save_source_page()`` 必须用
条件 UPDATE 推进 checkpoint 的原因。

### 不做隐式生命周期操作

搜索路径绝不执行写操作：不 ``ensure_ready()``、不创建 Collection、不切换 Alias、不
upsert/delete Point、不修改 ``processing_status``，也不会在失败时回退到物理 Collection。
Alias 缺失就返回 ``qdrant_target_missing`` 503，由部署去修，不在请求路径里偷偷做有副作用
的事。同理，``VectorSearchRuntimeUnavailableError`` 时不现场临时构造 Runtime 兜底。

模块 import 也不会创建任何远程资源。

### 不做重复校验

跨 Chunk 的分组不变量只在 ``QdrantVectorSearch.search_groups()`` 这一个信任边界校验，
Service 与 Pydantic 响应模型不再重复。同理，``VectorSearchService`` 不再用
``isinstance`` 检查请求类型——FastAPI 已经保证传入的是校验过的模型。重复校验不增加
安全性，只让契约变更需要改多处，且各处慢慢分叉。

### 不开放注册与自助操作

没有 ``/auth/register``、密码重置、邮箱验证或用户枚举路由。账号只能由超级用户经
``/admin/users`` 创建，或用 ``agent-lab create-user`` 恢复。环境托管管理员由 ``.env``
唯一托管，不能通过 API 停用、降级或改密码。

### 不在响应里回显输入或上游细节

错误契约层不读 ``str(error)``、``error.args`` 或任何异常属性，只按类型查表。422 丢弃
Pydantic 的 ``input``/``ctx``。流水线只返回异常类名而不是异常文本。因此数据库 URL、
API Key、用户 query、新闻正文、Vector 和第三方原始响应都不会出现在响应或日志里。

### 不在 Python 里重做 Qdrant 已做的事

文档聚合、去重和结果排序由 Qdrant grouped query 在后端完成，不在 Python 中重排 score，
不做新闻时间加权。响应交付给调用方时已经去重排好序，调用方再算一遍只会和后端漂移。
应用不重复做 L2 normalization（Cosine Collection 在上传时已执行）。

### 不做站点特定处理

动态网页回源和站点 CSS selector 由 FreshRSS 负责。``FreshRSSItemMapper`` 之后的
PostgreSQL、Document 和 Chunk 流程不关心文章来自哪个网站，Python Pipeline 里不能加站点
判断分支。

### 不做的持久化

不建 Chunk 表、Embedding 表或 pipeline_runs 表。Chunk 与 Embedding 都能由
``documents.content_text`` 加固定切分参数重新派生，存两份只会引入不一致。历史
``content_text`` 也不会被正文规范化规则自动批量改写。

### 改对外契约要连着下游一起改

``POST /vector-search`` 是 Chunk 级契约，允许同一 document 的多个 Chunk 分别出现；
``POST /document-search`` 才是文档级分组。两者语义不同，是并存的两个接口。

错误 ``code`` 的值被前端 ``frontend/src/api/error-copy.ts`` 按字面量映射成文案，包括那两处
刻意保留的分叉。新增 ``code`` 只影响文案兜底，改已有 ``code`` 的值会让前端静默显示错误提示——
要改就两边一起改。
