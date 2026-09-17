# 后端架构说明

本文描述后端**当前真实的**内部结构与对外契约：数据怎么分层、Chunk 怎么切、错误怎么映射、
读写权限怎么隔离。安装与运行看 [`../README.md`](../README.md)。

某处「为什么是这样、当时放弃了什么」记在平台根 [`docs/adr/`](../../docs/adr/)。本文只说是什么。

## 对外 HTTP 接口

```text
GET  /health                              应用与 PostgreSQL 连通性（无需登录）
POST /auth/login                          账号密码登录，签发 HttpOnly Cookie（无需登录）
POST /auth/logout                         撤销当前 Token
GET  /auth/me                             当前账号的最小身份与权限字段
GET  /knowledge-bases                     知识库配置列表（默认只列启用库）
POST /knowledge-bases                     创建知识库（超级用户）
PATCH /knowledge-bases/{kb_id}            改名称、说明或启停；无物理删除（超级用户）
GET  /sources                             外部来源列表与绑定状态（超级用户）
PATCH /sources/{source_id}/knowledge-base 修改来源的知识库绑定（超级用户）
POST /vector-search                       Chunk 级只读语义检索
POST /document-search                     文档分组只读语义检索
GET  /documents/{document_id}             按需读取 PostgreSQL 完整正文
GET  /file-documents                      分页列出文件资料与处理状态（超级用户）
POST /file-documents                      上传文本或 Markdown（超级用户）
PUT  /file-documents/{document_id}/file    按 ID、正式及管理修订替换文件（超级用户）
DELETE /file-documents/{document_id}       完整删除文件原件、历史与索引（超级用户）
GET  /document-management                分页筛选文件与 FreshRSS 文档（超级用户）
GET  /document-management/{document_id}   查看候选、草稿、结构及 Chunk（超级用户）
POST /document-management/{document_id}/draft  开始人工复核（超级用户）
POST /document-management/{document_id}/use-latest-source  明确换用最新来源（超级用户）
PUT  /document-management/candidates/{processing_id}/draft  保存草稿（超级用户）
POST /document-management/candidates/{processing_id}/preview  后台生成预览（超级用户）
POST /document-management/candidates/{processing_id}/adopt    确认采用（超级用户）
POST /document-management/candidates/{processing_id}/reject   停止使用（超级用户）
POST /document-management/candidates/{processing_id}/retry    核对或重试失败阶段（超级用户）
GET  /document-management/candidates/{processing_id}/original 下载原件（超级用户）
GET  /document-management/{document_id}/candidates  处理记录（超级用户）
GET  /document-management/{document_id}/versions    已采用版本列表（超级用户）
GET  /document-management/{document_id}/versions/{version_id}  已采用历史详情（超级用户）
GET  /document-management/{document_id}/reviews     审核结论（超级用户）
DELETE /document-management/{document_id}          完整删除文档（超级用户）
POST /pipeline/run-once                   持久受理手动 Pipeline，返回 202 和执行编号（超级用户）
POST /agent/chat                          Agent 对话，SSE 流式
GET  /agent/default-prompt                默认系统提示词
GET    /agent/threads                     列出自己的会话，分页
GET    /agent/threads/{thread_id}/messages 回放一个会话的历史问答
PATCH  /agent/threads/{thread_id}/scope    保存会话知识库选择
DELETE /agent/threads/{thread_id}         删除会话及其历史
GET  /auth/me/preferences                 读取当前账号的个人偏好
PUT  /auth/me/preferences                 整体覆盖当前账号的个人偏好
GET    /admin/users                       账号列表（超级用户）
POST   /admin/users                       创建账号（超级用户）
PATCH  /admin/users/{user_id}             改启用状态与超级用户位（超级用户）
DELETE /admin/users/{user_id}             删除账号，连带清会话归属与登录 Token（超级用户）
POST   /admin/users/{user_id}/password    重置密码（超级用户）
DELETE /admin/users/{user_id}/sessions    撤销该账号全部登录会话（超级用户）
GET    /scheduled-jobs                    定时任务列表，含下次执行时间与最近一次执行（超级用户）
POST   /scheduled-jobs                    创建定时任务（超级用户）
GET    /scheduled-jobs/{job_id}           单个任务详情（超级用户）
PATCH  /scheduled-jobs/{job_id}           改 cron、参数或启停（超级用户）
DELETE /scheduled-jobs/{job_id}           删除周期配置，保留已受理执行和历史（超级用户）
POST   /scheduled-jobs/{job_id}/trigger   持久受理一次当前配置的执行，返回 202（超级用户）
GET    /scheduled-jobs/{job_id}/runs      任务执行历史（超级用户）
POST   /scheduled-jobs/validate-cron      校验 cron 并预览未来 3 次执行时间（超级用户）
GET    /scheduled-jobs/task-types         已注册类型、参数 schema 与周期可配置性（超级用户）
GET    /task-runs                         周期与一次性执行列表（超级用户）
POST   /task-runs                         受理一次性任务，返回 202（超级用户）
GET    /task-runs/{run_id}                 独立执行详情，配置删除后仍可读（超级用户）
POST   /task-runs/{run_id}/cancel          取消尚未开始或等待重试的执行（超级用户）
POST   /task-runs/{run_id}/retry           使用原失败参数新建关联执行（超级用户）
GET    /task-policy                       默认重试与历史保留策略（超级用户）
PUT    /task-policy                       修改之后受理的默认策略并留痕（超级用户）
GET    /task-policy/changes               最近的策略修改记录（超级用户）
```

除 ``/health`` 和 ``/auth/login`` 外都需要有效登录 Cookie。搜索、全文、``/auth/me/*``
与 ``/agent/*`` 要求普通启用账号；``/pipeline/run-once``、``/admin/users``、``/scheduled-jobs``、
``/task-runs``、``/task-policy``、``/knowledge-bases`` 的管理写接口、``/sources``、
``/file-documents``、``/document-management`` 要求 ``is_superuser=true``。**没有 ``/auth/register``**，
账号只能由超级用户或 CLI 创建。

``/agent/*`` 曾经也限超级用户，理由是「每次对话是真金白银的模型调用」和「自定义提示词等于
让调用方改模型行为」。两者都已不成立：前者是成本不是访问控制，后者戴着护栏（中间件在选定
提示词后无条件追加「自定义提示词不能扩大」的资料边界段）；而真正需要挡的「跨账号读对话」由
会话归属单独解决，它按 ``user_id`` 判断、与角色无关。代价如实记录在
[ADR 0030](../../docs/adr/0030-agent-open-to-all-accounts.md)：模型额度对全部登录账号共享。

## KnowledgeBase 范围

`knowledge_bases` 表按稳定业务键管理多个逻辑知识库，Source 与 Document 通过
`knowledge_base_id` 逻辑外键归属（库上无约束，见 [ADR 0028](../../docs/adr/0028-drop-database-foreign-keys.md)），Qdrant 每个 Point 的 Payload 保存同一个 ID，共享
Collection 靠它过滤隔离。知识库用例的契约、端口和适配器组织在 `knowledge/`，
导入与重建应用在其中，索引、搜索、清理和 Source 配置复用 `services/` 中的端口用例；HTTP 与任务入口从 composition 装配取服务，
领域层不依赖 FastAPI、SQLAlchemy 或 Qdrant。

两个搜索接口兼容旧 HTTP：省略 `scope` 时缺省到稳定键 `news`，返回旧数组；显式单库请求
仍有效。新页面发送所有启用库或非空 ID 集合，返回 `{scope, results}`；scope 包含本次实际
知识库展示快照。新旧范围并存必须一致，空集合和显式 null 不退化成全库查询。

`knowledge/` 内部应用服务不接受隐式范围。范围解析先核验 PostgreSQL 目录，不存在返回
404 `knowledge_base_not_found`，停用或无启用库返回 409；此时不请求 Embedding 或 Qdrant。
Qdrant 按解析集合过滤并统一分组排序，不逐库分页拼接。

Agent 新会话默认所有启用库，迁移旧会话保留 news；每次运行冻结实际范围，Tool 只能缩小。
`prune_old_documents` 缺省仍只作用于新闻库，候选和定时删除待办恢复使用同一范围，且排除
人工文件删除待办。停用库仍可显式维护清理，Source 只允许新绑定启用库，已有绑定保留。

## 文件资料与统一审核

`knowledge/file_application.py` 负责文件身份、知识库归属、上传和替换，复用持久接收能力。
上传原件先通过格式与大小校验，再保存接收意图、写入并核对 S3 对象，确认待办后返回；
同名文件创建独立 Document，文件不要求 Source 或外部 URL。解码与内容异常在原件保存后由后台处理。

替换保留 ID 与 KnowledgeBase，同时核验正式 `revision` 和 `management_revision`。
新来源只产生候选，不提前覆盖已采用正文和正式 revision。管理修订与候选修订分别防止并发操作及迟到计算误写。

`knowledge/processing/review.py` 承载统一审核用例，PostgreSQL 逻辑留在 `adapters/review.py`。
管理读取分别提供当前已采用版本、最新人工草稿和最新来源；保存草稿使预览失效，重新预览在后台完成。
采用绑定候选修订、预览 fingerprint 和处理规格；打开复核、编辑和预览都不会撤下旧版。
已有人工版本或草稿时，来源更新进入复核，换用来源必须显式选择。

原件只经超级用户接口下载，HTML 使用附件响应并禁止缓存和内容类型猜测。审核结论保存当时正文依据，
已采用历史冻结正文、结构、Chunk 清单、元数据及原件引用；编辑草稿仅保留最新，已被决定或冻结的依据不会改写。

完整删除由 `knowledge/deletion.py` 统一执行：先停止使用并冻结清理依据，删除指定 Document 在本环境
各 generation 的 Point，再逐项删除原件，最后清除数据库正文、候选、历史及审核记录。每个远端步骤有确认进度，
失败保留待办；文件与统一审核入口都能继续同一目标的删除。拒绝只停止使用，保留管理资料。

## 账号与权限

登录、退出由 FastAPI Users 的 Cookie backend 提供，``api/auth.py`` 只额外挂一个安全的
``GET /auth/me``。Token 是 DatabaseStrategy 保存在 ``access_tokens`` 的可撤销随机值，
浏览器只拿到 HttpOnly Cookie。

应用启动时在构造搜索 Runtime 之前同步 ``.env`` 中唯一的保底超级管理员
（``sync_configured_environment_admin()``）。该账号带环境托管标记，**不能**通过 API 停用、
降级或重置密码（``environment_admin_protected``）；网页创建的其他账号可正常管理。
``UserAdminService`` 另外保护「最后一个超级用户」（``last_superuser_protected``），并对
重复邮箱和弱密码返回 ``user_already_exists`` / ``invalid_password``。

## FreshRSS 增量同步

`SourceImportService.import_recent_per_source()` 按分类白名单处理已绑定启用 KnowledgeBase 的来源。
首次保存最近的有界基线，之后从已提交 checkpoint 按旧到新追赶；新 Source 只登记，停用库保留绑定和位置。
FreshRSS 适配器选择 `content` 或 `summary` 的一份原始 HTML，不拼接两份正文，也不按文章 URL 抓取页面。

接收分为意图、对象保存和来源确认。意图先入 PostgreSQL，S3 请求在事务外执行；随后锁定 Source 和
KnowledgeBase 复核准入，以同一事务确认已保存原件对应的处理待办和 checkpoint。条件推进失败不倒退来源位置。
原件字节及可索引元数据相同保持幂等；标题、来源名称等索引字段变化仍产生候选，不提前改变正式全文的展示快照。

某条原件未可靠保存时不能越过它推进 checkpoint。原件已保存后的解析异常则保留记录，继续处理同页正常资料。
接收未决时复用同一个对象键核对，不用新键制造不可追踪对象；已保存意图与原件不会因后续来源确认失败而丢失。

## 正文质量规范化

FreshRSS 的规范化位于 Docling 结构转换之后，由 `knowledge/adapters/docling_html.py` 保留标题和阅读顺序，
处理实体、Unicode 与空白，以及首尾和标题一致的完整正文段、同一章节相邻且完全相同的正文段。
不同章节重复正文、正文中间的重复句子和非相邻重复段落不会被全局去重；首个标题之前的正文仍参与解析。

解码或解析失败、无有效正文或 Chunk、内容丢弃警告、无法满足标题与正文预算等结果进入人工处理。
合法短文、标题跳级和重复标题本身不构成异常。检查只拦截可检测问题，正常结果仍允许主动复核。
`ingestion/content_quality.py` 只提供标题和正文共用的块内文本规范化及标题比较键；正文质量与段落去重统一由 Docling 结构解析判断，不另设纯文本诊断流程或最小长度门槛。

## 数据对象分层

```text
FreshRSSItem / 文件字节
    → SourceDocument / TextFile：外部输入与身份元数据
    → SourceIntake + ObjectReference：接收意图及可核验的 S3 原件
    → ParsedDocument：项目正文、内容块、阅读顺序和标题父子关系
    → DocumentPreview：结构、Chunk 清单、质量原因和预览 fingerprint
    → IndexTarget：冻结候选、原件、元数据与独立索引实例
    → TextEmbeddings：只接收每个 Chunk 的 embedding_text
    → Qdrant Point：实例内稳定 Chunk ID、Vector 与 Payload
    → DocumentVersion + Document 当前指向：准备成功后采用
```

这些契约位于 `knowledge/processing/`，不暴露 Docling、SQLAlchemy 或 S3 SDK 对象。
存储适配器在短事务中构造独立快照；解析与网络调用不持有 ORM 会话。检索 query 独立向量化，
再由采用可见性组件与 Qdrant 查询返回结果。

## Docling 结构解析与 Chunk

`DocumentParser.parse()` 与 `StructuredChunker.build_chunks()` 是独立替换入口，
`DocumentProcessor` 组合两者并生成预览；`knowledge/composition.py` 负责选择实现。
MD 和 FreshRSS HTML 使用 Docling 文本后端，TXT 构造保留普通字符语义的结构对象，
井号、反引号和列表符号不会把 TXT 变成 Markdown。本期不接收 PDF、Word 或 HTML 文件上传。

`DoclingStructuredChunker` 使用 HybridChunker，优先尊重章节身份与内容块，默认不跨章节合并。
重复标题具有独立身份，不能凭标题文本或相同正文去重。长正文、代码和表格按锁定 token 预算继续拆分，
保留必要标题上下文和表头，无法完整满足预算时暴露质量原因，不静默删掉正文。

Chunk 的 `text` 用于展示，`embedding_text` 是确切的向量化输入，包含标题路径；同时保留块引用、
章节身份、阅读序号和 token 数。预算包括模型特殊 token，计数使用固定 revision、文件 SHA-256 校验的
BGE-M3 tokenizer，运行时只从本地加载。处理实现和预算版本集中在 `processing/specification.py`。

预览、自动采用、人工采用共用冻结清单；重建也直接复用已采用版本的清单。改变解析或切分规格必须重新预览、
采用。新索引实例拥有独立身份，Chunk ID 在实例内按序号稳定生成；重试复用冻结身份，不能覆盖旧版 Point。
原件保存后解析才发生，解析过程中不读取远程图片、本地引用、外链页面，也不启动 OCR 或生成式模型。

## Ollama Embedding

`pipeline/ollama_embedding_provider.py` 集中创建官方 `OllamaEmbeddings`，统一模型、认证、超时、
批量大小、异常分类与向量校验。独立 `OllamaEmbeddingSettings` 管理配置；query 与 document 使用同一模型。
Provider 接受确切文本列表，不了解文件、FreshRSS、解析、审核或数据库状态。

非空输入按批量配置保持顺序调用，空列表不访问网络。每批核验数量、非空向量、数值类型、有限数值和维度，
跨批次和 Provider 生命周期的维度保持一致。准备索引由 `CandidateIndexer` 编排，只有完成远端核验后，
采用应用才能更新正式版本；Provider 本身只返回内存向量。

## Qdrant 向量存储

官方 `qdrant-client` 负责 Point、Collection/Alias 与搜索，`langchain-qdrant` 不在依赖中。
向量已由独立 Embedding 组件生成，存储适配器不会再执行向量化。完整规格由
`qdrant/index_spec.py` 的 `VectorIndexSpec` 表达，包含模型、维度、距离、解析/切分版本、
tokenizer 身份及 revision、文本预算与 Payload 版本；当前采用 v3 规格。

`schema_version` 表示索引空间版本。模型、维度、距离、处理规则或 Payload 不兼容时必须建立新规格，
不能把 v2 Point 当成 v3 候选索引使用。物理 Collection 按环境、schema 和 generation 命名，
普通查询访问环境的 current Alias；日常候选准备写当前目标，重建写显式的新 generation。
显式写入口负责 `ensure_ready()`，搜索不会创建或切换 Alias。

Payload 保存独立 `index_instance_id`、Document/KnowledgeBase 身份、Chunk 正文与关系、标题路径、
文档展示元数据、正文 hash、模型和规格。字段及索引以 `payload.py`、`lifecycle.py` 为准。
候选准备按冻结身份写入并完整回读核验，失败或退休实例按 Document 与实例精确回收，覆盖本环境各 generation。

查询前排除未采用及退休实例，Qdrant 按 `index_instance_id` 分组，避免同一 Document 的不同版本混组。
查询后 PostgreSQL 批量核验当前正式指向与可用性，投影为每篇 Document 的公开结果；状态漂移则有界重查，
不能把无效结果简单删掉而损失文档名额。公开返回不暴露候选实例管理数据。

写入边界拒绝错误维度、非有限值和零向量。统一 client builder 使用 `port=None`，
让 HTTPS 反向代理 URL 保持原端口。Collection metadata 不匹配时停止，不能自动覆盖既有索引空间。

## 两个 Runtime：读写权限分离

`qdrant/runtime.py` 通过共享构造函数组装规格、客户端和 Embedding Provider，不以共同基类混合读写权限：

- `VectorSearchRuntime` 只持有 query Provider、Qdrant 查询与采用可见性组件。构造时必须显式传入知识库范围
  和 `DocumentVisibility`，没有生命周期管理、Point 写入或 `ensure_ready()`。
- `DocumentIndexingRuntime` 提供生命周期、候选 Point 存储和 `CandidateIndexer`，消费已冻结输入，
  不解析或重新切分正文。
- `PipelineWriteRuntime` 编排接收、处理批次与清理，按调用创建、结束关闭；`knowledge/composition.py`
  选择解析、对象存储、数据库与索引适配器，Worker 与 CLI 复用写装配；API 只装配受理与查询。

只读 Runtime 按进程共享，写客户端按工作生命周期创建。各客户端都尝试关闭，并保留首个关闭异常；
网络调用不占用业务长事务。

## Chunk 级语义检索：POST /vector-search

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
Python 中重排；同一 Document 的多个 Chunk 可以分别返回，不做 document 聚合或时间加权。成功返回
``VectorSearchResult[]``，空命中返回 200 ``[]``。

程序内构造只读 Runtime 时也必须显式提供知识库范围与已采用版本可见性端口，不能绕过 HTTP 后省略权限和版本核验。
生产装配由 `main.py` 完成；离线与隔离测试的端口注入见 `tests/test_qdrant_runtime.py`。

## 文档级语义检索：POST /document-search

`POST /document-search` 使用 Qdrant 的 `query_points_groups()`，按 Payload
`index_instance_id` 在服务端分组，再核验并投影为当前可用的 Document。它不先取 ``top_k`` Chunk 再由前端
去重，因此 ``document_limit`` 始终限制不同 Document 数量，``matches_per_document`` 始终限制
每篇 Document 返回的相关片段数量：

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

## Qdrant 响应的信任边界

``qdrant/search.py`` 是「Qdrant 响应可信度」的信任边界：Qdrant 返回的内容一律当作外部不可信
输入，Point/Payload 契约和文档分组的跨 Chunk 不变量都在这里一次验干净。``search_groups()``
保证：组非空、组内每个 Payload 的 index_instance_id 与 Qdrant 分组身份相同、Document 元数据一致、组内 chunk_id 互不
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

## 按需读取全文：GET /documents/{document_id}

用户从检索、Agent 引用或文件列表打开全文时，通过 ``DocumentRepository.get_with_source()``
加载可选 Source 与 KnowledgeBase，返回当前 ``content_text``、``content_hash``、``index_revision``
（响应字段名 ``revision``）、格式和展示元数据。搜索另做批量可用性核验，但不逐篇回查正文，不产生全文 N+1。

文档不存在、尚未采用、已拒绝或正在删除时返回固定脱敏 404；没有 Source 的已采用文件仍可读。知识库停用返回 409；数据库不可用返回 503；数据库记录违反公开契约返回
502（只记异常类型，不把字段值或正文写进日志）。前端比较搜索结果 hash 与详情 hash，不一致时
提示原文已更新，并使用 PostgreSQL 最新正文，不伪造历史版本。引用阅读另外展示当时取得的片段。

## Agent 对话：POST /agent/chat

一条独立于检索的生成链路：用户提问 → 模型自己决定要不要调工具 → 拿工具结果作答，全程以 SSE
增量返回。它**复用**只读的 ``VectorSearchService`` 与 ``DocumentRepository`` 作为工具实现，
不复制检索逻辑；但走自己的路由、权限和响应形状。

``agent/`` 的模块分工：

```text
config/llm.py       LlmSettings（LLM_ 前缀）与 LangSmithSettings（LANGSMITH_ 前缀）
agent/limits.py     一次运行的有界执行参数，全是代码常量、刻意不进 .env
agent/prompts.py    默认系统提示词与摘要压缩提示词
agent/chat_model.py 构造模型客户端（OpenAI 兼容协议，指向中转站 base_url）
agent/context.py    AgentContext：不可变 run_id、实际范围和自定义提示词
agent/tools/        两个只读工具：search_documents、read_document
agent/evidence.py   Tool artifact 与引用核验，SSE 和回放共用
agent/replay.py     从保留消息得到问答、范围、完成状态和引用
agent/middleware.py 中间件流水线；顺序有语义，见 ADR 0005
agent/runtime.py    组装根：编译一次图，进程级共享
agent/streaming.py  翻译 LangGraph 事件，从持久状态确定 Done
agent/checkpointer.py  四张 checkpointer 表名的唯一真源 + Alembic 的 include_object
agent/errors.py     本层的已分类异常（叶子模块，不 import 框架图相关模块）
```

图能进程级共享是因为它无状态：会话历史存在 checkpointer 里、按 ``thread_id`` 取；系统提示词
由 ``dynamic_prompt`` 每次从 ``AgentContext`` 读。所以「换会话」和「换提示词」都不需要重新编译。

Agent 装配失败**不致命**：lifespan 捕获、只记异常类型、``app.state.agent_runtime`` 留 ``None``，
于是只有 ``/agent/*`` 返回 503，检索和流水线照常。反过来会让一个缺失的 ``LLM_API_KEY`` 把整个
只读系统一起拖下线。关闭顺序上先关 Agent 再关检索 Runtime——Agent 复用后者的 Service。

有界执行参数（``agent/limits.py``，全部是代码常量）：

```text
MODEL_CALL_RUN_LIMIT = 8            达到后结束运行并返回已有内容
TOOL_CALL_RUN_LIMIT = 12            达到后只是不再允许调工具，模型仍能用已有材料作答
MODEL_RETRY_MAX / TOOL_RETRY_MAX = 2
SUMMARIZATION_TRIGGER / KEEP = 40 / 20   按消息条数触发，不按 token
MAX_USER_MESSAGE_CHARS = 4000       超过直接拒绝，不截断
MAX_SYSTEM_PROMPT_CHARS = 4000      同上：截断会把提示词砍成半句，行为更难预期
SEARCH_TOOL_MAX_DOCUMENTS = 5       给模型的上下文预算，不是给人看的分页上限
SEARCH_TOOL_MAX_MATCHES_PER_DOCUMENT = 2
READ_DOCUMENT_MAX_CHARS = 6000      这里截断是对的：正文是数据不是指令
SSE_HEARTBEAT_INTERVAL_SECONDS = 15
```

SSE 侧的两个实现约束：

- 响应类是 ``ServerSentEventResponse`` 子类，不是给 ``StreamingResponse`` 传
  ``media_type``。传参数只改真实响应头、不改 OpenAPI——FastAPI 按
  ``response_class.media_type`` 决定把 ``responses`` 里的模型挂到哪个 content key 下，
  否则事件 schema 会被挂到 ``application/json`` 上，而这个接口从不返回 JSON 响应体。
- 心跳用 ``asyncio.wait`` 而不是 ``wait_for``：后者超时会取消任务，等于每发一次心跳就丢掉一个
  正在生成的事件。``wait`` 超时后不取消，下一轮接着等同一次 ``__anext__``；``finally`` 里再
  收拾悬空的那次，否则客户端中途断开时会漏掉模型连接。

事件模型在 ``schemas/agent_chat.py``，用 ``event`` 字段做 discriminated union
（``AgentChatEventEnvelope``），所以生成的前端类型是可穷尽的联合：

```text
run_started  thread_id、run_id、本次实际范围
token        临时文本增量，工具调用参数不走这里
tool_call    模型决定调用工具，带 tool_call_id
tool_result  工具结果及实际范围；失败 content 为安全文案
done         持久化最终答案、完成状态、有效和无效引用，以及 thread_id
error        已分类的失败，同样带 thread_id（理由见下）
```

主模型每次只接收保留的历史问题与当前运行消息；旧答案、Tool 和摘要不作为新运行证据。
自定义提示词后仍追加应用的范围与引用规则。实际 Tool 结果带应用生成的引用标识及 artifact，
保存原文片段和对应 content_hash；只接受本次成功且未越界的证据，身份核验不等于事实正确性判断。

Done 与回放共用 `build_replay_turns`，避免上游重试临时文字、截断或预算耗尽被显示成完整答案。
压缩在新提问开始时进行，保留完整近期问答；前端结束后同步最新 checkpoint，早期消息及引用
不另行归档。设计取舍见 [ADR 0021](../../docs/adr/0021-agent-run-evidence-and-replay.md)。

失败为什么走事件而不是状态码：响应头在第一个 token 发出时就已发送，之后改不了状态码。所以流
开始之前的失败走 HTTP 状态码，开始之后只能走 ``error`` 事件——两条路径共用同一张规则表，同一种
失败在两处拿到同一个 ``code``。

``error`` 和 ``done`` 一样带 ``thread_id``，因为归属行在流开始之前就已写入（ADR 0010）：失败的
那一轮在服务端已经是一个存在的会话。不带的话前端无从知道它，用户点「重发」时请求里没有
``thread_id``，服务端只能当成新会话再建一行——同一次提问在会话列表里占两条，都是「有提问、
没答案」，重试几次就多几条。上游限流是最常撞见的失败，所以这条路径不是边角情况。

会话历史由 ``langgraph-checkpoint-postgres`` 存在四张 ``checkpoint*`` 表里，**不由 Alembic 管**
（ADR 0004）。建表是一次性运维步骤：``agent-lab init-checkpointer``。表名只写在
``agent/checkpointer.py`` 一处，``alembic/env.py`` 的 ``include_object`` 从那里取——漏改一处的
后果不是报错而是 ``--autogenerate`` 生成 ``op.drop_table('checkpoints')``，下一次迁移删掉全部
会话历史。

它走的是独立的 psycopg 连接池（同上 ADR），因此业务侧 Engine 的 ``pool_pre_ping`` 保护不到它，
必须自己配 ``check=AsyncConnectionPool.check_connection`` 做取连接前探活。少了它的表现值得记住，
因为它不像故障、像抖动：psycopg_pool 的 ``check`` 默认 ``None``，取连接时完全不验活，于是被
PostgreSQL 单方面掐掉的空闲连接（``idle_session_timeout``、中间代理的空闲回收、PG 重启）会被原样
交给 checkpointer，第一条 SQL 抛 ``psycopg.OperationalError``，日志里是一行
``discarding closed connection`` 加一条失败——而检索和文档接口全都正常，因为它们走 SQLAlchemy。
更糟的是坏连接不会自己消失：``max_lifetime`` 和 ``max_idle`` 只在连接**归还**时检查，不巡检躺在
池里的空闲连接，所以池里有几条死连接就要害几次提问失败才换干净。``min_size`` 也必须显式给（配置
允许 ``max_size`` 填到 1，而默认 ``min_size`` 是 4，相撞时构造直接 ``ValueError``），顺带让池真的
能收缩——两者相等时 ``_shrink_pool`` 的条件永远不成立。

## 集中的错误契约：api/error_contract.py

搜索、文档搜索、账号管理和 Agent 路由共用一个错误契约层，映射收在有序的
``ErrorContractRule`` 表里。Pipeline 的业务错误表继续供 Worker 生成脱敏结果；HTTP 受理及任务操作错误由 `api/task_routes.py` 统一映射，业务执行失败从任务详情读取。

三条必须长期保住的设计约束：

1. **只读异常的「类型」。** 全模块不出现 ``str(error)``、``error.args`` 或任何异常文本
   拼接，因此数据库 URL、API Key、用户 query、文档正文、Vector 和第三方原始响应都不可能
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

规则表与输出入口：

```text
VECTOR_SEARCH_ERROR_RULES   build_vector_search_error_response()
    search_runtime_unavailable 503 / embedding_authentication_failed 502 /
    embedding_timeout 504 / embedding_unavailable 503 / embedding_model_not_found 503 /
    embedding_response_invalid 502 / qdrant_authentication_failed 502 /
    qdrant_timeout 504 / qdrant_unavailable 503 / qdrant_target_missing 503 /
    qdrant_configuration_invalid 503 / qdrant_response_invalid 502 /
    qdrant_service_error 502

PIPELINE_ERROR_RULES        scheduled_tasks.classify_error()（进入任务详情）
    freshrss_authentication_failed 502 / freshrss_unavailable 503 /
    freshrss_timeout 504 / freshrss_response_invalid 502 / freshrss_sync_failed 502 /
    postgresql_unavailable 503 / embedding_* （与读链路共享四条 Ollama 规则）/
    embedding_failed 502 / qdrant_configuration_invalid 503 / qdrant_unavailable 503 /
    qdrant_write_failed 502 / pipeline_configuration_invalid 503 /
    pipeline_timeout 504

USER_ADMIN_ERROR_RULES      build_user_admin_error_response()
    user_admin_database_unavailable 503

AGENT_CHAT_ERROR_RULES      build_agent_chat_error_response()
    agent_runtime_unavailable 503 / agent_checkpointer_unavailable 503 /
    agent_checkpointer_connection_lost 503 /
    llm_authentication_failed 502 / llm_request_blocked 502 / llm_timeout 504 /
    llm_rate_limited 503 / llm_model_not_found 503 / llm_request_rejected 502 /
    llm_unavailable 503 / llm_response_invalid 502 / llm_service_error 502 /
    agent_internal_error 500

AGENT_TOOL_ERROR_RULES      sanitize_tool_error()（结果进模型上下文，不进 HTTP 响应）
    agent_tool_database_unavailable 503 / agent_tool_failed 500

UNCLASSIFIED_ERROR_RULE     手动流水线与检索的未分类兜底：pipeline_internal_error 500
INVALID_REQUEST_RULE        请求校验失败：invalid_request 422
```

两张 Agent 表各自以 ``Exception`` 结尾，也就是各带一条自己的兜底。这**违反**上面「全项目只保留
一条兜底」的写法，是刻意的：通用兜底的 code 是 ``pipeline_internal_error``（手动流水线的契约
值），漏到 Agent 接口上前端会按它查文案、查不到就把枚举名显示给用户。检索接口能声明自己捕获
哪些基类，Agent 面对的异常集合是开放的（模型 SDK、工具、框架内部都可能抛），所以必须有自己的
兜底码。

``AGENT_TOOL_ERROR_RULES`` 的输出方向和其余四张表不同：工具失败的文案会作为 ``ToolMessage``
回到**模型**手里（让它换个检索词重试或直说查不到），只在 ``tool_result`` 事件里顺带给用户看，
不构成 HTTP 错误响应。所以它查表得到的是安全中文文案，异常细节只进日志。

``AGENT_CHAT_ERROR_RULES`` 里 ``llm_*`` 那几条规则挂的具体异常类型只有一部分经过真实中转站
验证：已实测冒到我们这层并正确落到具体规则的是 ``PermissionDeniedError``（403）和
``APITimeoutError``；其余仍是照 openai SDK 文档写的。所以那条 ``Exception`` 兜底不只是形式——
真实调用报出没见过的错误时，应当核对它落到的是具体规则还是兜底，落到兜底就说明该补规则。
这也是为什么兜底码是 ``agent_internal_error`` 而不是复用检索链路的值：前端能按它查到文案，
不会把枚举名显示给用户。

``agent_checkpointer_unavailable`` 与 ``agent_checkpointer_connection_lost`` 同样是「状态码相同
但要动的东西不同」，分法按**什么时候发现的**：前者来自启动时连接池打不开（``AgentRuntime.open``），
是配置错或数据库不在，重启前重试无意义；后者是进程跑起来之后池里某条连接被服务端掐掉（空闲回收、
PG 重启、中间代理超时），重发同一个问题就能成功。后者挂的是**原生** ``psycopg.OperationalError``——
checkpointer 按 ADR 0004 走独立的 psycopg 池，不经过 SQLAlchemy，所以 ``SQLAlchemyError`` 那几条
规则对它无效；它也不是内置 ``ConnectionError`` 的子类，``llm_unavailable`` 同样捞不住。规则只挂
``OperationalError`` 而不是 ``psycopg.Error``：``ProgrammingError``（漏跑 ``init-checkpointer``
导致表不存在）必须留在兜底里报 ``agent_internal_error``，那种故障重试永远好不了。

``llm_authentication_failed``（401）与 ``llm_request_blocked``（403）分成两条，是被一次真实
排查逼出来的：两者曾合并在认证失败一条里，于是「中转站按 User-Agent 拦掉了 openai SDK 的默认
标识」被报成认证失败，排查从换 Key 开始，而 Key 一直是好的。状态码和重试语义相同不足以合并，
**要动的东西不同就得分开给码**——凭据问题改 ``LLM_API_KEY``，客户端身份问题改
``LLM_USER_AGENT``。

两处刻意保留的分叉，不能合并改值：读链路把 ``EmbeddingResponseError`` 归为
``embedding_response_invalid``，写链路归为 ``embedding_failed``；账号管理的
``user_admin_database_unavailable`` 与流水线的 ``postgresql_unavailable`` 是两个既有契约值。

搜索错误响应固定三字段 ``code``/``detail``/``retryable``（``retryable=true`` 只表示稍后重试
可能恢复，不代表服务会自动重试）。任务受理错误保留稳定 code、中文说明与适用的执行编号；业务错误只在执行详情记录脱敏统计和 Python 异常类名，不再返回旧同步 Pipeline 结果包装。

两条搜索路由通过共享的 ``SEARCH_UPSTREAM_EXCEPTIONS``（``OllamaEmbeddingError``、
``QueryVectorValidationError``、``QdrantVectorSearchError``）捕获已分类上游失败。它不含
``VectorSearchRuntimeUnavailableError``——那个由依赖注入在进入 endpoint 前抛出，endpoint 内的
try 接不到，统一由应用级 handler 映射成同一个 503。

## 422 脱敏的两个层级

``main.py`` 的应用级 ``RequestValidationError`` handler 是默认兜底：只保留字段位置、稳定错误
类型和安全消息，丢弃 ``input`` 和 ``ctx``（它们可能带着用户提交的完整 query）。

需要更强脱敏的路由改用 ``SanitizedValidationRoute``（``APIRoute`` 子类），把校验失败收敛成单一
``invalid_request``。做成 route class 而不是在装配根判断 URL 前缀，是因为脱敏是**路由自身的
属性**（它的请求体里有明文密码），不是 ``main.py`` 要维护的一串路径常量。文档管理、文件、账号等涉及敏感输入的路由均使用它；具体挂载以各路由声明为准。

## 共享的依赖注入与校验器

``api/dependencies.py`` 位于 FastAPI 边界层最底部，只从 ``application.state`` 取装配根放进
去的组件，不构造 Runtime、不做任何 I/O。``get_vector_search_service()`` 取进程级共享
Service，`get_task_service()` 取得公共任务受理与查询组件；API 不取得 Pipeline 写 Runtime。组件缺失分别抛 `VectorSearchRuntimeUnavailableError` 或 `SchedulerRuntimeUnavailableError`，在边界映射成稳定 503。存在的意义是让
``vector_search`` 与 ``document_search`` 这类平级路由都依赖公共模块，而不是互相 import。

``schemas/_query_validators.py`` 提供 ``require_non_whitespace_query()`` 和
``require_numeric_threshold()``，被 ``VectorSearchRequest`` 与 ``DocumentSearchRequest`` 共用。
两者是独立请求契约（字段集合不同，不能合并成一个模型），但对 ``query`` 和 ``score_threshold``
的要求必须完全一致——**包括错误文案**，因为 422 响应体里的文案属于对外契约且有测试直接断言。

## 文档处理与采用状态

Document 的正式可用性、候选处理阶段和管理修订分开。`documents` 保存已采用正文及当前版本/索引实例指向；
`document_processing_records` 保存接收、草稿、预览、领取代次和冻结写入目标；
`document_versions` 保存不可变已采用快照，`document_review_records` 保存决定及当时正文依据。
具体状态字段由模型和契约维护，不再用 Document 的旧索引状态独自代表整个流程。

处理应用先条件领取计算快照，在事务外读原件、解析与切分，再按领取代次和候选修订保存结果。
正常自动候选进入采用；人工草稿和质量异常等待人工确认。纯计算中断可以重新排队，明确失败不会无限自动重领。

采用先持久冻结候选、处理规格及索引实例，后台生成向量并完整回读 Point；再条件更新正式正文与实例指向，
保留新已采用版本和审核结论。准备失败、并发新来源、拒绝或迟到工作均不能覆盖旧正式版本。
失败重试复用冻结目标；需要新预览时创建新候选，同时保留旧目标的清理依据。退休向量回收不删除管理历史。

正式搜索、全文与 Agent 共用可用性规则。拒绝立即停止后续读取；尚未采用的候选只供管理使用。
兼容重建保留已采用快照和正文 revision，只改变实际索引实例；发布屏障覆盖 Alias 与数据库映射间的窗口，
不让中间态泄露到检索。

## 手动写入入口

CLI 与 Worker 中的 Pipeline、定时索引及文档任务共用 `DocumentProcessingBatch`，解析、采用、旧索引回收分别有界。
`sync-news` 只接收原始资料并确认来源位置，不生成向量；`index-pending` 消费已保存待办；
CLI `run-once` 直接执行一次同步和处理批次；`POST /pipeline/run-once` 持久受理后返回 202，由 Worker 执行相同业务顺序，结果从 `/task-runs/{run_id}` 读取。

参数边界在 `pipeline/limits.py`。回执区分已解析、待审核、已采用、跳过、失败和清理数量，
不把待审核当成已完成索引；仅输出安全统计和错误类型，不输出正文、Vector、完整异常或凭据。
来源接收失败不推进该来源 checkpoint，已可靠接收的其他资料仍可处理。

`rebuild-index --generation N` 遍历当前可用的已采用快照，使用新 generation 与独立索引实例，
复用冻结 Chunk 和 embedding_text，不重新解析。目标必须尚未存在；逐篇和全量核验成功后建立发布屏障，
切换 current Alias 并条件更新数据库索引映射，已采用历史和正文 revision 不变。

构建失败保留原 Alias。发布阶段中断时，`recover-index-rebuild --generation N` 核对实际 Alias
与已准备目标并恢复映射，不重新向量化。写占用仍需先确认旧进程和远端写入已停止后恢复。
普通候选写入跟随规格匹配的 current 目标；重建不会修改账号、会话、任务配置或 Source 接收位置。

## 公共任务组件

`tasks/` 负责注册、持久受理、cron、投递、领取、状态与策略，`task_assembly.py` 将业务注册项接到公共核心。注册项绑定参数模型、普通处理函数及必要的资源准备、错误分类、完成回调和恢复判断，不从数据库加载代码。`services/scheduled_task_registry.py` 注册三种周期类型，以及文档后台批次和手动 Pipeline；后两种不供用户配置 cron。决策见 [ADR 0019](../../docs/adr/0019-scheduled-execution-and-write-coordination.md)。

`ScheduledJobService` 管理周期配置；启用或执行期间可修改和删除，配置行锁与 Beat 的版本复核协调生效顺序。每次受理冻结参数、来源配置和执行策略。配置删除由 `ScheduledJobRepository.delete_job` 在同一事务里把历史执行的 `job_id` 置空；稳定来源身份、已受理工作和历史保留。数据库唯一约束保护同周期事件、同配置和业务声明的未结束执行名额。

`TaskService` 将执行、原请求回执和投递依据同事务保存。提交主体、操作及 `Idempotency-Key` 共同识别请求，摘要比较原请求内容；重发先查回执，再读取可能已修改或删除的配置。不同请求不因参数相同合并。同配置尚有未结束执行时，人工触发返回冲突编号，周期事件留下跳过记录。HTTP 在数据库提交后返回 202，业务状态由独立执行接口查询。

`TaskDispatcher` 在短事务中领取发布资格，事务外向 Redis 发送执行编号及投递代次；执行行自身保存补投时间和投递错误，无独立消息平台。发布成功后仍核对未领取工作，覆盖提交后退出、发布失败和消息丢失。Redis 不是受理或结果的事实来源。

Redis 是项目共用中间件，`config/redis.py` 提供统一连接配置；`config/task_queue.py` 只保存任务队列的执行参数。Celery 按队列名隔离消息与未确认消息等辅助键，后续缓存等使用方管理自己的键前缀。共享实例的持久化、内存策略与容量由部署管理，不写入业务策略或任务快照。

`PostgresScheduler` 是单个 Celery Beat 的动态适配器，直接读项目周期配置。启动和恢复只推进未来计划；受理复核版本、启用状态及计划时刻，保留原正常投递误差。`tasks/cron.py` 复用 `CronTrigger` 的五段式表达式计算，预览和真实计划使用同一入口，不再启动 APScheduler。Beat 维护还负责到期补投、失联执行核对与历史清理；慢发布不阻塞周期受理。

`TaskWorker` 先核验状态、投递代次及可开始时间，再原子领取。业务资源准备成功后，与取消竞争同一个“开始”条件；准备占用绑定领取 token，取消或未开始失联恢复只清理该次准备。重复、迟到、已取消或已结束消息不再调用业务。正常资源等待保存原因并让出 Worker 位置，不消耗业务尝试次数；待核实的旧占用仍阻止冲突写入。

自动重试由 PostgreSQL 的尝试次数、可开始时间和受理策略决定，沿同一个执行编号；不启用 Celery autoretry 或长期 ETA。人工重试创建关联新执行，保留原参数、采用当前策略。取消仅适用于尚未开始或等待重试的执行。结果按领取 token 条件保存；保存失败只重试收尾，不重做业务。从未开始的失联工作可补投，已开始的仅按业务恢复证据处理；没有足够证据时保留待核实，维护入口继续使用 `scheduler_maintenance`。

`write_coordination.py` 用 PostgreSQL 短事务咨询锁维护持久占用，CLI 与 Worker 共用。同步、索引及清理保持原业务互斥范围；Pipeline 同步阶段只占同步资源，之后释放并按处理批次原边界采用及回收。清理仍覆盖整次执行，只选择满足业务资格的已采用资料，保护待审核、失败和拒绝记录。`document_deletions` 逐项保存 Qdrant、S3 及数据库收尾依据，已完整完成的目标不再选中；远端确认丢失可以核对或再次删除同一剩余目标。

`knowledge/task_intake.py` 将文档业务待办与必要执行受理放在同一事务；正常完成时将本批收尾与必要续批一起提交。没有可处理待办时不续建，失败、取消、待核实不被补投循环重建。`DocumentProcessingBatch` 继续负责有界解析、采用和旧索引回收；原常驻消费者已移除。Docling、tokenizer 计数和同步 S3 SDK 在线程中执行。

API、Beat 和 Worker 不共享异步连接池。生产 Worker 使用 Linux prefork；`tasks/process.py` 在子进程中惰性创建持久事件循环和 Engine，并在同一循环中使用、关闭。业务 Runtime 按执行创建，Session/事务按短工作单元创建。消息完成后确认、预取量保持较小，visibility 超时重投仍受数据库领取保护。Windows 原生可运行页面、API、Beat 和 solo Worker，受理、补投、资源等待、业务处理与正常关停已验证；Linux prefork 的多进程与故障验收范围见后端 README。

任务管理分周期配置和全部执行，按编号独立查询，业务表单与统计显式适配。未知结果保留可识别信息，部分失败单独显示；请求身份和参数在提交前按账号保存，超时后显式确认原请求。默认策略由超级用户修改并留痕；普通终态详情按受理快照到期清理，未结束、待核实及重试原失败受保护，最小去重回执与业务恢复待办不随详情清理。

`tasks.status` 的本地文件反映 Beat 周期推进；Worker ping 反映消息连通。下次计划时刻、进程就绪和业务进展分别查看，不能互相代替。跨进程流程见[任务执行](../../docs/flows/scheduled-job-execution.md)，部署与真实队列、跨存储验证范围见后端 README。离线测试不能代替真实消息及数据库锁验收。

## 模块边界

`knowledge/` 组织知识库业务领域的契约、应用和端口；`knowledge/adapters/` 容纳 Docling 与 PostgreSQL
实现，`knowledge/storage.py` 容纳原件端口与 S3 适配器。第三方对象不进入 HTTP、ORM 快照或应用公开输入。
`knowledge/composition.py` 是实现选择点，不建设动态插件加载器。

接收、解析、Chunk、向量化、采用、审核和删除各自围绕自己的契约；调用方使用公开应用入口。
`ingestion/` 负责 FreshRSS 外部协议，`pipeline/` 保留运行装配与 Ollama Provider，
`qdrant/` 负责候选实例、检索与 Collection/Alias，`services/` 复用范围、查询和写协调能力。
旧 Builder、递归切分器及整篇覆盖索引服务已移除。

`agent/` 只消费 `VectorSearchService` 与 `DocumentRepository` 的正式只读能力，不反向参与索引。
`agent/checkpointer.py` 与 `agent/errors.py` 保持叶子依赖，避免迁移为表名导入完整 Agent 图。

API、CLI、Beat、Worker 通过装配使用业务能力。平级路由不互相 import，`api/dependencies.py` 和
`api/error_contract.py` 是共同边界；`main.py` 管 API 进程资源，`task_assembly.py` 接入公共任务，`pipeline/assembly.py` 复用业务写装配。
跨层类型仅用于注解时使用 `TYPE_CHECKING`，避免 `dependencies → agent.runtime → middleware → error_contract` 环。

## 数据库表

```text
sources          Feed、机构或其他文档来源，以及来源级 sync_checkpoint 与推进时间
knowledge_bases  逻辑知识库的稳定业务键、展示信息和启停配置
documents        已采用正文、归属、当前版本/索引实例、使用状态及正式/管理修订
document_processing_records  接收意图、原件引用、候选/草稿、预览与冻结索引目标
document_versions           已采用的正文、结构、Chunk、元数据、规格及原件快照
document_review_records     人工或自动审核结论及当时正文依据
users            内部登录邮箱、Argon2 密码 Hash、启用/超级用户状态和唯一环境托管标记
access_tokens    浏览器登录产生的可撤销随机 Token、创建时间和所属用户
agent_threads    Agent 会话的账号归属、选择范围、标题与最后活跃时间；不含任何消息内容
scheduled_jobs   周期配置：key 唯一、类型、cron、params、启停、配置版本与下一计划时刻
scheduled_job_runs  周期及一次性任务执行：受理快照、稳定来源、策略、状态、投递、领取与结果；
                    删除配置保留执行，普通终态详情到期清理，未结束和恢复依据受保护
task_requests    最小请求去重回执；详情过期后保留原执行编号与摘要
task_policy      当前默认重试与任务历史策略
task_policy_changes  默认策略修改留痕
write_operations   同步、索引、清理的持久资源占用；失联不自动抢占
document_deletions  独立删除待办：目标、修订、资格、原件引用、远端确认进度及脱敏错误
alembic_version  由 Alembic 维护当前迁移版本

以下四张由 langgraph-checkpoint-postgres 自建自迁移，Alembic 既不生成也不删除（ADR 0004）：
checkpoints、checkpoint_blobs、checkpoint_writes、checkpoint_migrations
```

`documents.content_text` 是当前已采用正文，原始字节在 S3，候选和历史快照在各自记录中。
Chunk 清单与结构作为预览和已采用快照保存在 PostgreSQL，向量仍只在 Qdrant，不另建 Chunk 或 Embedding 关系表。
作者、标签和图片 URL 使用 PostgreSQL `text[]`；所有时间带时区，数据库连接会话固定为 UTC。

Agent 的会话数据分在两处，边界是「内容 / 归属」：四张 ``checkpoint*`` 表存消息内容，
``agent_threads`` 存归属、展示元信息和下一次运行的选择范围。前者由第三方库管、不由 Alembic 管；后者是普通业务表，
``user_id`` 是指向 ``users`` 的**逻辑外键**（库上无约束，连带清理由
``UserAdminService.delete_user`` 在同一事务内显式完成，见
[ADR 0028](../../docs/adr/0028-drop-database-foreign-keys.md)），并有 ``(user_id, last_active_at DESC)`` 索引。
分开的理由见 [ADR 0009](../../docs/adr/0009-agent-thread-ownership-in-own-table.md)。

**归属校验是访问控制，不是凭据检查。** 每条 ``/agent/*`` 路由先经
``AgentThreadService`` 确认目标会话属于当前账号，不属于就 404；``WHERE user_id`` 只写在那一个
Service 里。``AgentChatRequest.thread_id`` 仍允许客户端填，但填别人的会拿到 404 而不是别人的历史。
「不存在」与「不属于你」刻意返回同一个 code：区分开就等于给出一个枚举有效 id 的预言机。

这一层不依赖「``/agent/*`` 只对超级用户开放」。那条权限将来放宽时，归属校验仍然成立——它是
按账号判断的，不是按角色。

``POST /agent/chat`` 的校验必须在**流开始之前**完成：响应头一旦发出，失败就只能是一个 SSE
``error`` 事件，拿不到 HTTP 状态码了。它也不能用请求级数据库 Session，否则一条业务连接会被整段
对话占住，几个并发就把连接池占空，而症状出现在检索页
（[ADR 0010](../../docs/adr/0010-sse-routes-use-short-lived-db-sessions.md)）。

归属记录在流开始前就写好，所以首轮失败会留下「有会话、没消息」的行；回放接口对它返回空轮次，
前端显示成一个可以接着聊的空会话。删除会话要动两个存储，跨两个连接池没有共同事务，顺序固定为
「先清历史、后删归属记录」：中途失败留下的是可自愈的「历史没了、归属还在」，反过来会留下查不到
也删不掉的孤儿。孤儿由 ``agent-lab prune-orphan-threads`` 回收，默认只预演。
