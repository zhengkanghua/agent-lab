一、先看"整个项目"长什么样（前后端全景）
 ```
   ┌─────────────────────────────────────────────────────────────────────┐
   │                       前端 frontend/ (React + TypeScript + Vite)      │
   │  只做两件事：发 HTTP 请求、渲染结果                                       │
   │  · 通过同域相对前缀 /api 访问后端                                       │
   │  · 用的 API 类型定义是从后端 /openapi.json 自动生成的（不能手改）        │
   │  · 浏览器里不直接碰 PostgreSQL / Ollama / Qdrant（AGENTS 约束）          │
   └──────────────┬──────────────────────────────────────────────────────┘
                  │ HTTP + JSON（OpenAPI 契约）
   ┌──────────────▼──────────────────────────────────────────────────────┐
   │                      后端 backend/ (FastAPI + Python 3.12)           │
   │  main.py (组合根) → 装配 Runtime → 挂路由 → lifespan 管理生命周期      │
   │                                                                    │
   │  外部依赖三件套：                                                    │
   │   · PostgreSQL：业务事实源（documents/sources 表）                    │
   │   · Ollama：Embedding 模型（文字→向量）                               │
   │   · Qdrant：向量库（存向量 + payload，按相似度查）                    │
   │   · FreshRSS：RSS 阅读器（新闻来源）                                 │
   └─────────────────────────────────────────────────────────────────────┘
 ```

 核心约束（AGENTS.md）：前端只通过 HTTP 契约访问后端，不直接连 PostgreSQL/Ollama/Qdrant——这是前后端解耦的根。

二、后端分层架构（你学的"书架"）

 ```
   api/          路由层（薄胶水：解析请求、DI注入service、异常映射成HTTP）
   services/     业务编排层（总调度：串起多个零件）
   repositories/ 持久层（封装对表的SQL操作 + 状态机）
   models/       ORM模型（表结构）
   schemas/      Pydantic DTO（请求/响应契约 + 校验）
   domain/       内部领域模型/枚举（无DB细节）
   config/       pydantic-settings（读环境变量）
   db/           engine连接池 + session工厂
   pipeline/     RAG管道：切块 + embedding
   qdrant/       向量库：spec/lifecycle/payload/store/search
   ingestion/    摄取：FreshRSS client + mapper + content_quality
   auth/         认证
   main.py/cli.py/runtime.py  装配根 + 入口
 ``


三、两条河流（整个项目的灵魂）
 ### 写路径（低频、有副作用、需要 superuser）

 ```
   FreshRSS(聚合RSS)
      │  ingestion/freshrss_client.py（Google Reader API 拉取）
      ▼
   FreshRSSItem（外部协议对象）
      │  freshrss_mapper.py（清洗HTML/时间/标签→内部模型）
      ▼
   SourceDocument（内部统一模型）
      │  DocumentRepository.upsert（幂等存 PostgreSQL，标 pending）
      ▼
   PostgreSQL documents 表（事实源）
      │  DocumentBuilder（ORM → LangChain Document）
      ▼
   LangChain Document
      │  DocumentChunker（递归按 token 切块，稳定ID）
      ▼
   LangChain Chunk
      │  OllamaEmbeddingProvider（chunk正文→1024维向量）
      ▼
   [(chunk_id, vector)]
      │  QdrantPayloadMapper（chunk→payload）
      ▼
   QdrantChunkStore.replace_document_chunks（稳定ID幂等 upsert）
      ▼
   Qdrant（current Alias → 物理Collection）
 ```

 谁来触发写？ 两种入口殊途同归：
 - CLI：sync-news / index-pending / run-once
 - HTTP：POST /pipeline/run-once（需 superuser）
 - 都汇到 NewsPipelineExecutionService → DocumentIndexingService（逐篇索引）

 ### 读路径（高频、只读、所有登录用户可用）

 ```
   浏览器 query
      │  api/vector_search.py（POST /vector-search）
      ▼
   VectorSearchRequest（DTO校验）
      │  VectorSearchService.search（总调度）
      ▼
   OllamaEmbeddingProvider.embed_query（query→向量）
      ▼
   QdrantVectorSearch.search（Qdrant current Alias 查Top-K）
      ▼
   VectorSearchResult（DTO强类型结果）→ JSON → 前端
 ```



四、一张你能带走的"总地图"（浓缩版）

 ```
                        ┌─────────────── 后端 Agent Lab ───────────────┐
                        │                                              │
     FreshRSS ──► ingestion ──► PostgreSQL ──► pipeline(切块+embed) ──► Qdrant
     (RSS聚合)    (拉取/清洗)   (事实源/状态机)   (RAG处理)          (向量库)
                        │        │      ▲            │                ▲
                        │        │      │            └────────────────┘
                        │        │      │                   写路径
                        │        │   index_revision / indexed_* (版本对照)
                        │        │
                        │        ▼  读路径（高频只读）
                        └────► api ──► VectorSearchService ──► Ollama ──► Qdrant ──► 前端
                                 (路由)      (编排)          (query向量) (查Top-K)
 ```

 一句话总结这个项目：一个"从 FreshRSS 摄取新闻 → 用 LangChain 切块 + Ollama 向量化 → 存进 Qdrant → 提
 供语义检索"的后端服务，用分层架构 + 依赖注入 + 幂等乐观锁 + 一致性双保险 + 脱敏纪律把"状态管理（PG）
 、语义处理（Ollama/LangChain）、向量存储（Qdrant）"三个领域干净地组织在一起，并以"PostgreSQL 为事实
 源、Qdrant 为可重建派生副本"的方式保证数据可靠性。
