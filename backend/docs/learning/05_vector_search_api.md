# 阶段 4：只读 Vector Search HTTP API

## 本阶段目标和非目标

阶段 3 已经提供有类型的 Python Search Service。阶段 4 把它接入 FastAPI，使其他进程
可以通过稳定 JSON 契约执行同一条只读检索链路：

```text
HTTP POST /vector-search
    -> VectorSearchRequest 校验
    -> VectorSearchService
    -> OllamaEmbeddingProvider.embed_query()
    -> QdrantVectorSearch.query_points(current Alias)
    -> list[VectorSearchResult]
    -> HTTP 200 JSON array
```

本阶段负责 HTTP 边界、进程生命周期、依赖注入、OpenAPI、错误状态和敏感输入保护。
它不改变阶段 3 的向量、Filter、score 或 Payload 语义，也不实现：

- 生成式 LLM、Prompt Template 或 RAG；
- Retriever、Agent、Tool Calling 或对话历史；
- Hybrid Search、全文搜索、reranker 或时间加权；
- document 聚合、相邻 Chunk 自动扩展或答案引用；
- Qdrant Collection 创建、Alias 切换、Point 写入或 PostgreSQL 状态更新；
- 本阶段原始范围内的调用方认证、配额或分布式限流（平台后续已补本地账号认证）。

## 为什么需要独立 HTTP 层

Search Service 只处理应用用例，不应该知道 HTTP status、JSON 或 OpenAPI。HTTP 层解决
另一组问题：

- JSON body 如何映射为 Pydantic 请求；
- 请求非法时返回 422，且不回显完整 query；
- Ollama/Qdrant 错误如何成为稳定、可机器处理的 HTTP code；
- 进程启动和关闭时谁持有 client；
- 多个并发请求如何复用连接池；
- API 文档如何准确声明成功与失败结构。

因此 API 不直接创建 Ollama client、拼 Qdrant Filter 或解释 score。它只注入
`VectorSearchService` 并映射已分类异常。

## 文档级 grouped search 与全文边界

在保留本页 ``POST /vector-search`` Chunk 契约的基础上，Signal Desk 使用：

```text
POST /document-search
    -> DocumentSearchRequest 校验
    -> 同一个 VectorSearchService 的 query Embedding/Filter 共享逻辑
    -> Qdrant query_points_groups(group_by="document_id")
    -> DocumentSearchResult[]

GET /documents/{document_id}
    -> DocumentRepository.get_with_source()
    -> PostgreSQL documents.content_text
    -> DocumentDetailResponse
```

``document_limit`` 限制不同新闻组数，``matches_per_document`` 限制每组相关片段数；
聚合在 Qdrant 完成，不能由前端对有限 ``top_k`` 结果去重。``best_match`` 是组内最高
score，``additional_matches`` 是本次返回的有限相关集合，不是全部物理 Chunk。详情
接口只在用户点击阅读时查询 PostgreSQL，因此 document search 不产生 N+1；详情
``content_hash`` 与搜索 hash 不一致时由前端提示当前正文已更新。

## Endpoint 契约

```text
POST /vector-search
Content-Type: application/json
```

请求示例：

```json
{
  "query": "央行近期是否调整利率？",
  "top_k": 10,
  "score_threshold": 0.6,
  "filters": {
    "source_provider": "freshrss_main",
    "document_type": "article",
    "labels": ["宏观", "利率"],
    "published_from": "2026-08-01T00:00:00+08:00",
    "published_to": "2026-08-31T23:59:59+08:00"
  }
}
```

字段继续复用阶段 3 契约：

| 字段 | 必需 | 约束 |
|---|---:|---|
| `query` | 是 | 1..4096 个 Unicode 字符，至少一个非空白字符 |
| `top_k` | 否 | 默认 10，整数范围 1..100 |
| `score_threshold` | 否 | 默认 null，有限 Cosine score `[-1, 1]` |
| `filters.source_id` | 否 | UUID |
| `filters.source_provider` | 否 | 非空 keyword |
| `filters.document_type` | 否 | `DocumentType` 枚举 |
| `filters.labels` | 否 | MatchAny；空数组表示不过滤 |
| `filters.published_from/to` | 否 | 显式带时区，包含范围端点 |

4096 是 HTTP/Embedding 资源安全上限，不是模型 token 上限。query 是检索意图而不是待
索引文档；超过该长度通常意味着调用方传错了正文。应用不会截断，因为静默截断会改变
语义。若真实业务证明需要更长 query，应先测量模型延迟和相关性，再显式调整契约。

## 成功响应

成功始终返回：

```text
HTTP 200
Content-Type: application/json
```

Body 是 `VectorSearchResult` 数组，不包裹额外 envelope：

```json
[
  {
    "point_id": "52f03ef7-03ca-4eec-aa1d-c2cc43964f85",
    "chunk_id": "52f03ef7-03ca-4eec-aa1d-c2cc43964f85",
    "score": 0.8123,
    "page_content": "命中的新闻 Chunk 正文",
    "document_id": "c558027b-5c86-4325-b8b8-35e0a3aecb72",
    "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "chunk_index": 0,
    "chunk_count": 2,
    "title": "新闻标题",
    "url": "https://example.com/news",
    "published_at": "2026-08-14T00:00:00Z",
    "source_updated_at": null,
    "document_type": "article",
    "source_id": "a2637146-42db-4f2d-9a35-14bd121552f7",
    "source_provider": "freshrss_main",
    "source_name": "示例来源",
    "source_external_id": "feed/1",
    "document_external_id": "article/1",
    "authors": [],
    "labels": ["宏观"],
    "previous_chunk_id": null,
    "next_chunk_id": null,
    "index_schema_version": "v1",
    "embedding_model": "bge-m3:567m"
  }
]
```

没有命中不是错误，返回 `HTTP 200` 和空数组 `[]`。这与“上游失败”严格区分，调用方
不能把 502/503/504 当成零结果。

结果顺序继续完全使用 Qdrant score，不在 API 层排序、过滤、聚合或改写。

## 422 请求错误与 query 脱敏

FastAPI 默认 `RequestValidationError.errors()` 可能包含原始 `input`。如果错误发生在
query 字段本身，例如类型错误或超长，默认 422 可能把完整 query 回显给客户端，也可能
被网关记录。

`create_app()` 注册全局安全 handler，只保留：

```json
{
  "detail": [
    {
      "type": "less_than_equal",
      "loc": ["body", "top_k"],
      "msg": "Input should be less than or equal to 100"
    }
  ]
}
```

handler 统一移除 `input` 和 `ctx`，不读取或记录 request body。字段位置、错误类型和
安全消息足够调用方定位问题，同时不泄露 query。

## 上游错误响应

已分类的 Embedding/Qdrant 失败使用统一结构：

```json
{
  "code": "qdrant_timeout",
  "detail": "Vector database query timed out.",
  "retryable": true
}
```

三个字段含义：

| 字段 | 用途 |
|---|---|
| `code` | 稳定机器码，客户端应按它分支，不解析 detail |
| `detail` | 安全的人类可读概述，不含第三方原始异常 |
| `retryable` | 不修改请求时稍后重试是否可能恢复，不代表自动重试 |

HTTP 映射：

| HTTP | code | 含义 | retryable |
|---:|---|---|---:|
| 503 | `search_runtime_unavailable` | ASGI lifespan Runtime 不可用 | false |
| 502 | `embedding_authentication_failed` | 服务到 Ollama 的认证配置失败 | false |
| 503 | `embedding_unavailable` | Ollama 连接不可用 | true |
| 504 | `embedding_timeout` | Ollama 请求超时 | true |
| 503 | `embedding_model_not_found` | 配置模型不存在 | false |
| 502 | `embedding_response_invalid` | Vector 数值/维度契约损坏 | false |
| 502 | `qdrant_authentication_failed` | 服务到 Qdrant 的认证配置失败 | false |
| 503 | `qdrant_unavailable` | Qdrant 连接不可用 | true |
| 504 | `qdrant_timeout` | Qdrant query 超时 | true |
| 503 | `qdrant_target_missing` | current Alias/Collection 不存在 | false |
| 503 | `qdrant_configuration_invalid` | Vector 配置不兼容 | false |
| 502 | `qdrant_response_invalid` | Point/Payload 响应契约损坏 | false |
| 502 | `qdrant_service_error` | 其他 Qdrant 上游失败 | true |

这里的 502 认证错误不是“HTTP 调用方未登录”。它表示本服务访问 Ollama/Qdrant 时的
上游凭据失败，所以不能返回 401 误导调用方更新自己的凭据。

已知错误只记录 Python 异常类型，不调用 `str(error)`。未知异常不转换为空列表或通用
200，而是原样交给 FastAPI 作为 500，保留编程错误的可观测性。

## VectorSearchRuntime

阶段 3 的 `DocumentIndexingRuntime` 同时持有 lifecycle、Store 和索引 Service，适合
索引 Worker，但权限面大于只读 HTTP API 所需。阶段 4 新增最小 Runtime：

```text
VectorSearchRuntime
    -> AsyncQdrantClient
    -> OllamaEmbeddingProvider
    -> VectorIndexSpec
    -> QdrantVectorSearch
    -> VectorSearchService
```

它没有：

```text
QdrantCollectionLifecycle
QdrantChunkStore
DocumentIndexingService
ensure_ready()
PostgreSQL AsyncSession
```

因此 API 进程不能通过 Runtime 创建 Collection、切 Alias、upsert/delete Point 或修改
`processing_status`。这是结构上的最小权限边界，不只是一条注释约定。

## FastAPI lifespan

`main.create_app()` 在 ASGI lifespan 启动时：

1. 读取缓存的 `QdrantSettings` 和 `OllamaEmbeddingSettings`；
2. 构造 `VectorSearchRuntime`；
3. 放入 `app.state.vector_search_runtime`；
4. 开始接收请求。

启动不会调用：

- `ensure_ready()`；
- `collection_exists/get_collection/get_aliases`；
- `create_collection/create_payload_index/update_collection_aliases`；
- Ollama Embedding；
- PostgreSQL 查询。

qdrant-client 1.19.0 默认在构造远程 client 时启动后台服务端版本探测线程。项目统一
`build_qdrant_client()` 显式设置 `check_compatibility=False`，避免构造 Runtime 产生
不可等待、不可分类的隐式网络 I/O。真正兼容性由显式 lifecycle 或 query 响应验证；
升级 client/server 时仍需在部署验证中确认版本组合。

关闭时先尽力关闭 Ollama/Qdrant Runtime，再 dispose SQLAlchemy Engine。任一关闭失败
都会传播；若两者都失败，保留 Runtime 失败为根因，并用 exception note 记录 Engine
失败类型。

## 依赖注入与并发边界

`get_vector_search_service()` 只从 `request.app.state` 读取共享 Service，不创建新 client。
如果应用没有经过 lifespan 启动，返回 503 `search_runtime_unavailable`，并继续使用
`code/detail/retryable` 契约，而不是在请求中临时构造 Runtime。

一个进程复用一套 Ollama/Qdrant 连接池；每个请求的 request、query Vector、Filter 和
结果只存在于该协程局部变量。Provider 的维度记录有异步锁，网络请求不在锁内串行化；
Qdrant component 无请求级可变状态。因此可以并发处理多个请求，但实际吞吐仍受
Ollama、Qdrant、反向代理和 Worker 数限制。

本阶段不加入应用内 semaphore、队列或分布式限流，因为没有真实容量数据。生产应先
在网关设置请求体上限、连接数与速率限制，再根据 p95/p99 延迟、timeout 和 Ollama
资源使用量决定是否需要进程内并发上限。

## 当前认证边界

平台后续已为 `POST /vector-search`、文档搜索和全文读取增加 FastAPI Users Cookie
认证。登录使用 PostgreSQL `users/access_tokens`，普通有效账号可以读取；未登录返回
401。`QDRANT_API_KEY` 与 `OLLAMA_API_KEY` 仍只是服务访问上游的凭据，不能混用。

部署要求：

- 外部访问必须使用 HTTPS；
- 生产保持 `AUTH_COOKIE_SECURE=true`，不开放注册；
- 网关不要记录 request body；
- 对登录配置速率限制，并为搜索配置请求体、并发和 timeout 保护；
- 按新闻正文授权边界决定哪些调用方可读取 `page_content`；
- 不要仅依靠前端 Router 或难猜 URL 作为认证。

当前不使用自创 API Key 或 Local Storage JWT。Cookie、DatabaseStrategy、401/403、建号
CLI 与测试见 [`08_local_password_auth.md`](08_local_password_auth.md)。

## OpenAPI

启动服务后可查看：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/openapi.json
```

OpenAPI 明确声明：

- `POST /vector-search` 请求为 `VectorSearchRequest`；
- 200 为 `VectorSearchResult[]`；
- 422 为请求校验错误；
- 502/503/504 为 `VectorSearchErrorResponse`；
- `/health` 仍是独立 PostgreSQL 健康检查。

`/health` 不调用 Ollama 或 Qdrant，避免每次探活都产生模型推理。搜索上游是否可用由
真实请求和指标观察；未来若需要 readiness，应设计无 Embedding 或低成本的明确探针。

## 本地调用

启动：

```powershell
uv run uvicorn agent_lab.main:app --reload `
  --loop agent_lab.runtime:selector_loop_factory
```

PowerShell 请求：

```powershell
$body = @{
  query = "央行近期是否调整利率？"
  top_k = 10
  filters = @{
    source_provider = "freshrss_main"
    labels = @("宏观", "利率")
  }
} | ConvertTo-Json -Depth 4

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/vector-search `
  -ContentType application/json `
  -Body $body
```

API 不会自动准备索引。若 current Alias 不存在，会明确返回 503
`qdrant_target_missing`；应由独立部署/索引步骤修复，而不是让 HTTP 请求创建资源。

## 离线测试

阶段 4 聚焦测试：

```powershell
uv run pytest -q tests/test_vector_search_api.py tests/test_qdrant_runtime.py
```

测试使用：

- fake Search Service/Runtime；
- `httpx.AsyncClient` + `ASGITransport`；
- 显式 `app.router.lifespan_context(app)`；
- 完全内存的 JSON 请求和响应。

当前 Starlette 1.6.0 的同步 `TestClient` 会提示弃用并建议另一个包；项目不为测试新增
依赖，而是直接使用已经安装的异步 httpx。测试不访问 Ollama、Qdrant、PostgreSQL，
并覆盖成功、空结果、组合过滤、422 脱敏、全部错误类别、Runtime 缺失、未知异常、
OpenAPI 和关闭生命周期。

完整回归仍运行：

```powershell
uv run pytest -q
```

## 可选真实验证

HTTP 路径本身（状态码、错误码契约、422 query 脱敏）由 `tests/test_vector_search_api.py`
用 fake service 完整覆盖，不需要真实 Ollama 或 Qdrant。真实外部系统的契约测试见
`docs/learning/04_vector_search.md` 的「可选真实只读验证」一节。

若要验证部署进程，启动本地服务后发送一条短 query；当前没有已确认的远程 Qdrant
current Alias 时，不应把 503 伪造成成功。

## 常见故障

### 422 且没有 input

这是预期的安全响应。根据 `loc/type/msg` 修正对应字段；服务有意不回显原值。

### 502 embedding_response_invalid

Ollama 返回了错误维度、非有限或零范数 Vector。检查当前模型是否仍为
`bge-m3:567m`，不要在 API 层补零或截断。

### 503 qdrant_target_missing

current Alias 或目标 Collection 不存在。运行独立索引部署准备流程，不要在 HTTP
请求里调用 lifecycle，也不要把物理 Collection 名写入 API 配置。

### 504 timeout

区分 `embedding_timeout` 和 `qdrant_timeout`。前者检查 Ollama 负载与输入长度，后者
检查 Qdrant 查询和过滤性能。`retryable=true` 只提示稍后可能恢复，客户端仍需有限
次数、退避和总时限，不能无限重试。

### 应用启动后没有访问 Qdrant

这是设计行为。Runtime 构造没有隐式探测，只有真实 `POST /vector-search` 才进行
Embedding 和 query。启动成功不等于 current Alias 已准备好。

## 完成标准

- `POST /vector-search` 通过 Pydantic 接收阶段 3 完整请求；
- query 非空且最多 4096 字符，422 不包含 input/ctx；
- 成功返回有类型结果数组，空结果为 200 `[]`；
- 502/503/504 使用稳定 code/detail/retryable；
- 已知错误不泄露 query、密钥、Vector、Payload 或第三方正文；
- 未知错误不静默转换为空结果；
- FastAPI lifespan 复用并关闭最小只读 Runtime；
- Runtime 不含 lifecycle、Store、索引 Service、ensure_ready 或 PostgreSQL Session；
- client 构造不启动隐式 Qdrant 兼容性网络探测；
- OpenAPI 包含 200/422/502/503/504；
- API 测试完全离线，不新增依赖；
- 没有实现认证、限流、Retriever、LLM、Agent、Hybrid Search、reranker 或 RAG。

## 阶段 5/6 写入执行衔接

阶段 5 已在独立 CLI 中实现 `sync-news`、`index-pending` 和 `run-once`，见
[`06_news_pipeline_execution.md`](06_news_pipeline_execution.md)。阶段 6 另以独立
``PipelineWriteRuntime`` 增加同步、有界的 ``POST /pipeline/run-once``，见
[`07_incremental_sync_content_quality_and_manual_api.md`](07_incremental_sync_content_quality_and_manual_api.md)。
写路由没有把组件注入本章的 ``VectorSearchRuntime``；``/vector-search`` 仍不持有
lifecycle、Point Store、索引 Service 或 PostgreSQL Session，因此搜索请求不能触发
同步或写入。FastAPI startup 也不会自动调用写 Runtime factory。

## 后续阶段建议

HTTP API 可用后，优先级应由真实部署决定：

1. 采集不含 query 正文的请求量、延迟、错误 code 和结果数指标；
2. 在网关设置登录速率、请求体、并发和 timeout 限制；
3. 建立真实 query/Chunk 相关性评测，调整 Top-K 和可选 threshold；
4. 有证据后再讨论 document 去重、相邻 Chunk、Hybrid Search 或 reranker。

这些事项仍应先于生成式 LLM/RAG；阶段 4 没有提前实现后者。
