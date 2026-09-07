# 通用知识库架构与数据模型升级

> **施工状态（2026-09-07 第一阶段收尾）**：本规格「分阶段交付边界」定义的第一阶段已完成，并经过 [0002 审查与修复规格](0002-knowledge-base-phase-one-review.md) 修复及再次核验。本次提交前后端 637 项、前端 696 项测试通过，前端 lint、format:check、build（含类型检查）通过；此前另行通过 15 项真实 PostgreSQL/Qdrant 隔离集成。第二阶段尚未实施，先讨论再施工。按老板要求保留全部相关 spec。
>
> 已完成：KnowledgeBase 配置表与 `news` 种子迁移；Source/Document 归属外键与通用字段迁移（`mime_type`、可空 source/external_id/url、`other` 类型）；`knowledge/` 内部组件；知识库管理与来源绑定 API 及前端页面（后台导航）；FreshRSS 同步未绑定来源只登记不拉取；Qdrant Payload 通用契约、`knowledge_base_id` 精确过滤与 Payload 索引；检索接口 HTTP 边界缺省解析 `news`；清理任务 `knowledge_base_ids` 范围参数（缺省只清新闻库、恢复待办同范围）与表单多选；Collection 命名去新闻语义（`knowledge_chunks_*`）、Schema 版本升 v2；CONTEXT.md、ADR 0019/0020、FEATURE_MAP、后端架构说明同步。
>
> 后续范围仅为本规格「分阶段交付边界」明确排除的检索页面知识库选择器和 Agent Tool 可选 `knowledge_base_id`。停用库的稳定错误契约属于本轮，现已实现：未知库 404、停用库 409，均在 Embedding/Qdrant 请求前拒绝。
>
> 应用环境（2026-09-07）：共享数据库已升级至 `b38f9a7c6d21`。按老板最新要求清空旧知识数据，使用全新 v2 索引完成一条真实 FreshRSS 数据的端到端验收；当前为 1 篇 Document、4 个 Point、6 个已绑定 Source。`http://127.0.0.1:5175` 的管理与检索页面可用，两条自动任务保持停用。线上程序发布未执行，后续恢复任务须先升级执行端；其他环境操作步骤见 [后端 README](../../backend/README.md#知识库升级与索引重建)。

## 分阶段交付边界

- **第一阶段，已完成**：通用知识库模型、知识库与来源管理、Source 绑定与同步准入、通用 Document/Payload、按库检索与清理、内部应用组件及重建入口。普通 HTTP 已支持显式单库参数，检索页面和 Agent Tool 当前仍默认新闻库；应用环境的单条真实链路也已验收。
- **第二阶段，待讨论与实施**：检索页面增加知识库选择器并记录每次查询范围；Agent Tool 增加可选单库参数，不传时查询所有启用库。范围草案与待讨论事项见 [0003](0003-knowledge-base-search-scope.md)。
- 线上程序发布和恢复自动任务属于部署操作，不计入第二阶段新功能；当前任务保持停用，发布当前代码及 v2 配置后再恢复。

## 第一阶段提交记录

老板授权按职责分批 commit，保留原规格、审查规格和第二阶段草案；本轮不 push 或发布。

| 批次 | 内容 | 提交 |
|---|---|---|
| 后端 | 模型迁移、应用用例、API、索引与清理、测试及后端说明 | `0f8751c` |
| 前端 | OpenAPI、管理页面、来源绑定、清理范围及测试 | `57f8a8d` |
| 文档 | 术语、ADR、流程、验收记录和阶段边界 | 随本次文档提交收录 |

提交前在已有 `90a44b2` 测试精简提交的基础上验证：后端 `637 passed, 19 skipped`（44.85 秒），前端 65 个文件 `696 passed`，lint、format:check、build 均通过。较早的后端 638 项结果作为历史验收保留，本次没有为降低数量删除测试；门控跳过项不计为通过。根 `AGENTS.md` 的既有协作规则改动保留工作区，未混入知识库提交。

## 应用环境修复（2026-09-07，已完成）

**最终执行结果**：老板要求中断耗时重建、清空开发阶段旧知识数据，仅用一条新数据验收完整链路，取代原先保留 975 篇并全量重建的方案。重建进程已停止，本项目旧 Document、Source、游标及旧 v1/未完成 v2 Collection 已删除；KnowledgeBase 配置、账号、会话、checkpointer 和任务配置保留。单条真实链路验收完成，自动任务保持暂停，后续稳定后再由新版任务执行端录入。

老板确认本地与线上使用同一 PostgreSQL、Qdrant 等服务，并授权直接处理数据库升级和本地后端重启。此项是代码验收后的追加环境操作，两份 spec 继续保留。

- [x] 查明 `http://127.0.0.1:5175` 的 `/api` 指向本地 8000 旧后端；知识库与来源接口实际返回 404，前端将其显示为服务暂时不可用。
- [x] 操作前共享数据库为 `f1a8c3d9e602`，没有 KnowledgeBase 表；业务 Qdrant 为 `news_chunks_langchain_current` / v1。操作前最后检查为 975 篇 Document、6 个 Source，无在途任务、processing 文档、写占用或删除待办。
- [x] 两条自动任务已暂停，配置版本均为 2；确认无在途执行后，在 sync/index 占用及事务锁保护下升级至 `b38f9a7c6d21`。975 篇 Document、6 个 Source 已回填 news；迁移前后原字段及账号/会话/checkpointer/任务等 12 张表的数量和内容指纹一致。
- [x] 按老板最新要求中断全量重建，已核实重建进程退出；不再尝试重建旧数据。
- [x] 删除 975 篇旧 Document、6 个旧 Source/游标及旧 v1、未完成 v2 Collection，确认重建进程退出和 Collection 删除完成后释放遗留写占用；10 张保留表的数量与内容指纹一致。
- [x] 本地 8000 已启动当前代码，调度关闭、HTTP Cookie 仅进程级覆盖；经 5175 真实登录，知识库与来源列表均为 200，返回 news 和 6 个已绑定来源。桌面 1440×1000、手机 390×844 的真实浏览器登录、列表、刷新、打开/关闭编辑表单验收通过，无脚本错误和横向溢出。
- [x] 从空知识数据开始，真实发现 6 个未绑定来源，文档/游标/Point 均为零；绑定一个来源后同步并索引 1 篇文档。停用库不导入/不推游标/搜索 409、未知库 404、有文档来源解除绑定 409 均通过；随后将 6 个来源绑定到 news，自动任务保持暂停。
- [x] 真实默认/显式单库文档检索、Chunk 检索及全文读取均为 200；仅 1 篇 Document、4 个 v2 Point，逐个完整 Payload 与 PostgreSQL 派生值一致，知识库 UUID 索引及 current Alias 正确。
- [x] 当前单条样本的桌面/手机真实浏览器检索、打开全文、关闭全文均通过；6 个来源和知识库页面复验通过，无脚本错误或横向溢出。
- [x] 重复执行 `index-pending --batch-size 1` 返回成功、候选/新增索引/失败均为零，仍为 1 篇 Document、4 个 Point；写占用、删除待办及在途任务均为零，5175 代理健康检查为 200。
- [x] 已记录恢复条件：两条任务按老板要求保持停用；线上 backend/scheduler 程序及 v2 配置升级后再恢复自动执行。共享数据库升级不等于线上程序发布，本次通过已有连接直接处理，未使用 SSH。

任务暂停前配置：`freshrss-sync`（`6b2f1c8e-3a4d-4e5f-9a0b-1c2d3e4f5a6b`），启用，cron `*/10 * * * *`，参数 `{"limit_per_source":2}`；`index-pending`（`7c3e2d9f-4b5a-4f60-ab1c-2d3e4f5a6b7c`），启用，cron `*/5 * * * *`，参数 `{"batch_size":20,"stale_after_minutes":60}`。两者原配置版本均为 1。迁移后旧写程序不传必填 `knowledge_base_id`，不能直接重新启用交回旧执行端。

最终 Alias 为 `knowledge_chunks_langchain_current`，指向全新 `knowledge_chunks_langchain_v2_001`；仅包含单条新样本生成的 4 个 Point。旧新闻 Alias/Collection 已删除。另一个项目的 `news_chunks_dense_v1` 不属于本次清理范围，保持不动。真实页面截图为 `output/playwright/{knowledge-bases,sources,search,reader}-live-{desktop,mobile}.png`。本轮使用已有 HTTP Pipeline、绑定、检索、全文接口及索引 CLI 完成验收，没有增加生产代码或临时导入接口。

## 最终验收对照

| 当前要求 | 结果与主要证据 |
|---|---|
| KnowledgeBase 管理与稳定 news 归属 | 已完成；`test_knowledge_bases.py`、管理页面测试与真实迁移 |
| Source 单目标绑定、冲突保护及新同步基线 | 已完成；`test_source_binding.py`、真实 KnowledgeBase 行锁竞争 |
| 新来源只登记、未绑定/停用不拉文章、不推游标 | 已完成；`test_freshrss_incremental_sync.py`；已发现来源元数据更新不依赖新文章 |
| 通用 Document、可空 Source/URL/外部 ID、MIME、幂等及 revision | 已完成；构建/索引测试、`test_generic_document_roundtrip.py`、真实 PG 持久化与全文读取 |
| 共享 v2 Collection、Payload 归属/格式/版本及精确范围过滤 | 已完成；Qdrant 测试、真实双知识库重建与检索；UUID Payload 索引已在真实服务核验 |
| 普通 HTTP 缺省新闻、显式单库、未知/停用错误及阶段一 Agent 范围 | 已完成；`test_knowledge_search_scope.py`、HTTP/Agent 回归 |
| 清理参数 JSONB、同类型多任务、单/多库范围及待办恢复 | 已完成；任务注册表/API、清理测试、真实 JSONB 与跨存储恢复 |
| 核心用例通过 DTO/端口调用，API/CLI/Job 复用生产装配 | 已完成；导入、索引、搜索、清理和绑定均不接收具体基础设施客户端；替身与生产适配器回归通过 |
| 全量重建先验收再切 Alias、失败保留旧索引、非知识数据保留 | 已完成；`test_index_rebuild.py`、真实迁移/checkpointer 保留及 PG 到 Qdrant 重建 |
| 前端页面、OpenAPI、空来源全文、桌面/移动交互 | 已完成；696 项测试与 1440×1000、390×844 浏览器验收，截图见 `output/playwright/` |
| 文档导航、部署步骤、消融与后续范围记录 | 已完成；ADR、架构、能力地图、流程和 README 同步；后续事项见 [0003](0003-knowledge-base-search-scope.md) |

后端默认全量测试中的 19 项门控跳过没有算作通过，其中 15 项随后在随机隔离资源中独立通过。
其余 4 项既有账号、Agent 归属和 Ollama 环境测试文件未单独运行，不计作通过。
后续本节上方的应用环境验收已实际调用 FreshRSS/Ollama、升级共享数据库，并通过真实后端完成浏览器验证；
它补充了此前合成 HTTP 响应及随机存储资源验证的运行环境证据。随后按老板授权分批本地 commit；push 与线上程序发布未执行。

## 问题陈述

当前系统围绕 FreshRSS 新闻建立，虽然已经具备 Document、Chunk、PostgreSQL、Qdrant、索引状态和定时任务等基础能力，但业务边界仍然把“新闻”当成了“所有可检索知识”的默认前提。

现有实现存在以下需要收敛的问题：

- 数据模型缺少独立的 KnowledgeBase，无法明确区分新闻、技术资料和其他知识内容。
- Source 与 Document 的关系仍然按新闻来源理解，不能表达来源数据最终进入哪个知识库。
- Qdrant Payload 带有新闻假设，不能稳定承载没有来源、URL 或发布时间的通用 Document。
- API、FreshRSS 同步、索引、清理和 Agent 检索需要统一使用 KnowledgeBase 范围，否则多库后容易发生混查、误删或错误索引。
- 现有实现分散在新闻 Pipeline、Repository、Qdrant 和定时任务中，未来抽离知识库能力时会牵动较多调用方。
- 定时清理目前缺少 KnowledgeBase 范围参数，无法为不同知识库配置独立的保留策略。

本项目仍处于开发阶段，现有 PostgreSQL 知识数据和 Qdrant 数据允许清空并重新建立，因此本次可以直接升级模型和索引契约，不需要为旧新闻 Payload 保留长期兼容读取路径。

## 解决方案

将当前新闻语义检索链路升级为通用知识库链路，同时保持现有 HTTP、Agent、FreshRSS、索引和定时任务的主要使用方式。

系统使用一个 PostgreSQL 和一个统一的 `documents` 表管理多个逻辑 KnowledgeBase。KnowledgeBase 之间通过 `knowledge_base_id` 做业务隔离，而不是拆成多个数据库或多个 Document 表。

具体 FreshRSS 订阅源绑定一个目标 KnowledgeBase。一个 FreshRSS 实例可以有多个订阅源，不同订阅源可以分别进入不同 KnowledgeBase。新发现但尚未完成绑定的订阅源只登记来源元数据，不拉取文章、不创建 Document。

Qdrant 按 Embedding 和索引规格共享 Collection。每个 Point 的 Payload 写入与 PostgreSQL 相同的 `knowledge_base_id`；普通检索和清理显式限制 KnowledgeBase，Agent Tool 允许在所有启用中的 KnowledgeBase 范围内检索。

知识库能力先作为后端内部业务组件存在，通过清晰的领域、契约、应用、端口、适配器和装配边界组织代码。暂不拆成独立网络服务，但实现必须让未来服务化只替换适配器和部署方式，不重写核心用例。

现有定时任务表和调度器继续使用。允许相同任务类型配置多个任务实例，通过任务参数指定 KnowledgeBase 范围和保留周期。

## 用户故事

1. 作为知识库使用者，我想让系统把不同 FreshRSS 订阅源分别归入不同 KnowledgeBase，以便新闻、技术资料和其他内容不会混在一起。
2. 作为知识库使用者，我想让一个 FreshRSS 实例下的多个订阅源分别绑定不同 KnowledgeBase，以便不需要为每个知识库部署独立的 FreshRSS 实例。
3. 作为系统维护者，我想在 Source 列表中看到所有已发现的订阅源及其 KnowledgeBase 绑定状态，以便及时发现尚未配置的来源。
4. 作为系统维护者，我想为尚未绑定的 Source 指定一个 KnowledgeBase，以便下一次同步可以正式拉取该订阅源的数据。
5. 作为系统维护者，我想让未绑定的 Source 只被登记、不拉取文章内容，以便错误配置的订阅源不会污染知识库。
6. 作为系统维护者，我想在前端管理页面配置 Source 到 KnowledgeBase 的绑定，以便不必直接修改数据库。
7. 作为系统维护者，我想创建、查看、编辑和启停 KnowledgeBase，以便不依赖数据库手工维护知识库配置。
8. 作为系统维护者，我想在 KnowledgeBase 停用后保留其数据并可重新启用，以便停用操作不会造成不可恢复的数据删除。
9. 作为后续知识库能力的开发者，我想让 Document 模型允许没有外部 Source 或 URL，以便未来接入人工笔记和文件资料；本轮不开发这些资料的创建入口。
10. 作为知识库使用者，我想让每条 Document 明确保存自己的 KnowledgeBase ID，以便数据归属不会依赖 Source 当前配置的动态反查。
11. 作为知识库使用者，我想让同一 Source 下相同外部 ID 的 Document 重复同步时覆盖更新，而完全相同的重复同步不产生无意义变更。
12. 作为知识库使用者，我想让正文或其他可索引字段变化后自动重新切 Chunk、生成 Embedding 并更新 Qdrant，以便检索结果跟随最新内容。
13. 作为知识库使用者，我想让不同 KnowledgeBase 的检索结果严格隔离，以便查询一个库时不会看到另一个库的内容。
14. 作为知识库使用者，我想在旧接口没有传入 KnowledgeBase ID 时继续查询新闻知识库，以便前端逐步升级期间现有功能不立即中断。
15. 作为知识库使用者，我想让新接口可以指定 KnowledgeBase，以便后续前端能够在不同知识库之间切换。
16. 作为后续 Agent 能力的开发者，我想让检索 Tool 支持可选的 KnowledgeBase 范围，以便 Agent 可以按需限定单库，或在不传范围时检索所有启用中的 KnowledgeBase。
17. 作为系统维护者，我想让 Qdrant 的 Chunk Payload 使用通用 Document 字段，以便未来接入政策、报告、笔记或其他文档时不被新闻字段限制。
18. 作为系统维护者，我想让文档业务类型与文件格式分开保存，以便 `document_type` 表达内容性质，`mime_type` 表达文本、PDF 或其他格式。
19. 作为系统维护者，我想保留索引状态、当前业务 revision 和最近成功索引快照，以便识别索引是否落后，同时不保存正文历史。
20. 作为系统维护者，我想让索引 Chunk 继续作为 Qdrant 派生数据存在，以便正文变化时可以重新切分，不维护第二套 PG Chunk 事实。
21. 作为系统维护者，我想配置多个相同类型的清理任务，以便新闻库、技术库和其他库可以分别按照不同周期清理。
22. 作为系统维护者，我想在一个清理任务参数中指定多个 KnowledgeBase，以便使用同一保留策略的多个库可以由一条任务统一处理。
23. 作为系统维护者，我想让未指定清理范围的旧任务默认只作用于新闻知识库，以便升级后不会意外清理所有新知识库。
24. 作为系统维护者，我想让清理任务只处理已明确指定的 KnowledgeBase，以便没有保留策略的资料不会被新闻规则误删。
25. 作为系统维护者，我想保留当前定时任务的执行记录、认领、并发协调和人工恢复语义，以便知识库改造不会破坏现有任务安全边界。
26. 作为开发者，我想通过稳定的内部知识库接口调用导入、索引、检索和清理能力，以便 HTTP API、Agent 和定时任务不再直接耦合新闻实现。
27. 作为开发者，我想让知识库核心用例不直接依赖 FastAPI、SQLAlchemy、Qdrant 或 FreshRSS，以便未来可以把它部署为独立服务。
28. 作为发布维护者，我想在开发阶段清理旧知识数据、建立新的通用 Qdrant 索引并重新同步 FreshRSS，以便一次性消除旧新闻 Payload 的遗留假设。
29. 作为发布维护者，我想在重建过程中保留账号、会话和定时任务等不属于知识数据的内容，以便重建知识库不会扩大数据清理范围。

## 实现决策

### 业务关系

- KnowledgeBase 是独立的逻辑知识库实体，不代表独立 PostgreSQL 数据库、独立 Schema 或独立 Qdrant Collection。
- `sources` 仍然一行代表一个具体外部来源；对于 FreshRSS，具体来源是一个订阅源，FreshRSS 实例由 `provider` 标识。
- `sources.knowledge_base_id` 和 `documents.knowledge_base_id` 都是指向 `knowledge_bases.id` 的外键，表达同一个 KnowledgeBase 标识。
- `sources.knowledge_base_id` 表示来源绑定的目标库；`documents.knowledge_base_id` 表示 Document 的实际归属。两者不是两套 ID，也不允许在正常同步结果中不一致。
- 一个 KnowledgeBase 可以绑定多个 Source；一个具体 FreshRSS 订阅源第一版只绑定一个目标 KnowledgeBase。
- 一个 FreshRSS 实例可以通过不同订阅源进入多个 KnowledgeBase；不支持同一订阅源第一版同时复制到多个 KnowledgeBase。
- 新发现的 Source 可以暂时没有 `knowledge_base_id`，该空值只表示“尚未配置”。一旦绑定，正常运行状态下必须有一个明确目标库。
- Source 到 KnowledgeBase 的配置以 PostgreSQL 为事实来源，不通过环境变量硬编码 RSS 到知识库的映射。

### Document 数据模型

- PostgreSQL 只使用一张统一的 `documents` 表，不为新闻、技术资料或个人资料分别建表。
- `documents.knowledge_base_id` 对所有 Document 必填，并且外键指向 `knowledge_bases.id`。
- `documents.source_id` 按场景可为空；手动 Document 可以没有 Source。
- 外部来源稳定键按来源场景可选。Source-backed Document 使用来源内稳定外部键做幂等；没有稳定外部键的文档不猜测合并关系。
- 同一 Source 下相同外部 ID 的记录覆盖更新；完全相同的重复同步保持幂等；不同 Source 即使正文相同，第一版也不按正文 Hash 自动合并。
- Source-backed Document 的业务唯一性继续围绕 `source_id + external_id`，不因同一 PostgreSQL 中有多个 KnowledgeBase 而创建多套新闻唯一键。
- 标题和规范化正文是最小 Document 必填内容；URL、Source、外部键、作者和发布时间按场景可选。
- `document_type` 表达业务内容类型，使用受控的通用类型集合并保留现有新闻类型的兼容语义；增加 `other` 作为无法归入既有类型时的受控兜底值。`mime_type` 表达内容格式，二者不混用。
- 作者和标签保存为明确的数组字段；少量不稳定的扩展信息才使用 JSONB，不把核心查询字段塞进 JSONB。
- `content_hash` 继续保存规范化正文的 SHA-256，用于判断正文是否变化和索引是否需要重建。
- 保留当前 `revision`、`indexed_revision`、最近成功索引的内容 Hash、索引 Schema 版本及相关处理状态字段。这些是索引一致性和诊断信息，不是正文历史。
- 不创建 `DocumentVersion` 表，不保存正文历史版本，不增加独立的 `content_revision` 版本线。
- 不创建 PostgreSQL Chunk 表；Chunk 从当前 Document 正文和索引配置派生。

### KnowledgeBase 初始数据和配置

- 初始化至少创建稳定标识为 `news` 的新闻知识库，作为旧接口和旧任务的兼容默认目标。
- KnowledgeBase 的稳定业务键不可依赖展示名称；展示名称可以修改，稳定键供默认解析、任务配置和迁移使用。
- KnowledgeBase 保存 `is_active` 布尔字段，默认值为启用；本轮不增加父子层级、归档状态或通用字典字段。
- KnowledgeBase 提供管理接口和前端管理页面，支持创建、列表、查看、编辑名称/描述和启停；本轮不开放物理删除，停用由 `is_active` 开关表达。
- KnowledgeBase 管理接口提供 `GET /knowledge-bases`、`POST /knowledge-bases` 和 `PATCH /knowledge-bases/{knowledge_base_id}`；不提供物理删除接口。Source 配置和任务表单通过 KnowledgeBase 列表接口获取可选项。
- `is_active = false` 表示 KnowledgeBase 暂停使用：不能绑定新的 Source，已绑定 Source 不再同步新 Document，不出现在普通检索选择范围，也不能被 Agent 的跨库检索命中；已有 Document 和 Qdrant 数据保留，明确指定该库的维护清理仍可执行，重新启用后恢复正常使用。
- KnowledgeBase 物理删除不属于本轮；未来只有在没有 Source、Document、删除待办、Qdrant 数据和任务配置引用时才可单独设计删除行为。
- Source 配置页面需要读取可选 KnowledgeBase 列表，并能展示和修改 Source 绑定。
- 后端提供 `GET /sources` 和 `PATCH /sources/{source_id}/knowledge-base`，返回 Source 的 provider、外部 ID、展示信息、同步状态和当前 KnowledgeBase 绑定状态；绑定接口在存在 Document、删除待办或未完成写入时返回冲突错误。
- Source 管理接口提供列表和 KnowledgeBase 绑定更新能力；不提供直接创建 FreshRSS Source 的页面，Source 仍由同步发现流程登记。
- Source 绑定操作只能设置一个目标 KnowledgeBase；不能通过同一个 Source 配置多个并行目标。
- Source 已有 Document、删除待办或未完成写入时，绑定接口拒绝修改或解除绑定并返回冲突错误；没有这些数据时才允许绑定、改绑定或解除绑定。
- Source 绑定发生变化时不自动迁移历史 Document。由于本轮采用清空知识数据后重新建立的方式，切换绑定前应清理知识数据并重置同步游标；绑定变化成功后同步游标从新的基线开始。
- 如果未来需要不清理历史数据直接改变归属，应另行设计显式批量重分类能力，本轮不通过修改 Source 绑定隐式实现。

### FreshRSS 同步

- FreshRSS 同步继续按订阅源作为同步和 checkpoint 边界；同步游标仍属于 Source，不扩展为全局游标。
- 同步可以读取 FreshRSS 的订阅元数据以发现新 Source，但未绑定 KnowledgeBase 的 Source 不进入文章 ID 拉取和文章内容拉取流程。
- 未绑定 Source 的同步结果只报告来源待配置，不创建 Document，不推进该 Source 的文章数据处理游标。
- Source 绑定的 KnowledgeBase 处于停用状态时，该 Source 不拉取新的文章内容；Source 绑定关系保留，KnowledgeBase 重新启用后恢复同步。
- 已绑定 Source 的每条输入 Document 使用该 Source 的 `knowledge_base_id` 写入 PostgreSQL。
- Source 到 KnowledgeBase 的分流按具体订阅源维护，现有 FreshRSS 分类白名单仍是同步筛选条件，不替代 KnowledgeBase 绑定关系。
- FreshRSS Mapper 只负责把外部协议转换为通用 Source 和 Document 输入，不在 Mapper 内写死新闻知识库。
- 现有增量同步、来源级事务隔离、稳定外部 ID 和 checkpoint 条件更新语义继续保留。

### Qdrant 和 Payload

- Qdrant 使用通用的知识片段 Collection 命名，不继续把 Collection 命名绑定为新闻语义。
- Collection 按 Embedding 模型、向量维度、距离度量、切分配置和索引 Schema 兼容性划分；同一规格下多个 KnowledgeBase 共享 Collection。
- 每个 Point 的 Payload 必须保存 `knowledge_base_id`，并且其值与对应 PostgreSQL Document 的值相同。
- 每个 Point 必须能定位到 `document_id`，并保存 Chunk 顺序、Chunk 总数、正文 Hash、Embedding 模型和索引 Schema 版本等一致性字段。
- Chunk 文本、标题、Document 类型、格式、可选 URL、可选发布时间、作者、标签和可选来源展示信息按检索响应需要保存。
- `source_id`、来源提供方、URL 和发布时间在 Payload 中按场景可为空；没有 Source 的 Document 不能因为 Payload 契约而被拒绝。
- 检索结果和全文详情中的来源字段与 URL 改为可空；现有前端在没有来源或 URL 时显示明确的“未指定来源”状态，不把空值当成接口错误。
- Point ID 继续作为 Chunk ID，不额外重复存储同一个 `chunk_id` 字段，除非未来有明确的跨系统兼容需求。
- 普通 HTTP 检索使用明确的 `knowledge_base_id` 过滤；Source、类型、标签和时间等条件在此基础上继续组合。Agent Tool 单独支持可选 KnowledgeBase 过滤：传入时限定目标库，不传时检索所有启用中的 KnowledgeBase。
- Qdrant Point 写入不是 PostgreSQL 事务的一部分，仍通过 Document revision、索引状态、Schema 快照和删除待办处理跨库不一致。
- 开发阶段新建通用 Collection generation，完成索引后切换 current Alias；旧新闻 Collection 不作为新 Payload 的长期读取来源。

### HTTP 和 Agent 契约

- 面向普通 HTTP 检索的内部应用接口要求调用方传入明确的 `knowledge_base_id`，内部不依赖隐式新闻默认值；Agent 的跨库检索用例是有意保留可选范围的例外。
- 现有 HTTP 接口第一阶段对 `knowledge_base_id` 保持可选兼容；请求缺省时由 HTTP 边界解析为 `news` KnowledgeBase。
- 普通搜索接口第一阶段支持显式传递 KnowledgeBase 范围；Agent Tool 的可选范围能力放在后续阶段实施。
- 按全局 Document ID 读取单篇 Document 的接口不因读取动作强制增加范围参数，但返回数据必须带有实际 `knowledge_base_id`。
- FreshRSS Source 未绑定时不能因为 HTTP 接口的“缺省新闻库”兼容规则而自动进入新闻库；接口默认值和来源自动发现是两条不同规则。
- 前端第一阶段保持现有新闻检索行为；KnowledgeBase 管理页面、Source 管理页面和清理任务范围配置属于本轮前端范围。检索页面的显式多库切换放在后续阶段。
- 后端 OpenAPI 契约、前端生成类型和 Source 配置页面必须保持一致；后续移除兼容默认值时只收紧契约，不重写知识库核心用例。

### 分阶段交付边界

- 当前施工阶段（阶段一）包含知识库内部组件化、PostgreSQL 与 Qdrant 模型升级、KnowledgeBase 管理接口和页面、Source 列表与绑定接口和页面、清理任务 KnowledgeBase 范围参数，以及普通 HTTP 检索对显式 `knowledge_base_id` 的后端兼容支持。阶段一的现有检索页面和 Agent 页面继续默认使用新闻知识库。
- 后续阶段记录包含检索页面 KnowledgeBase 选择器，以及 Agent Tool 的可选 `knowledge_base_id` 参数。Agent Tool 传入该参数时限定单库，不传时检索所有启用中的 KnowledgeBase；后续阶段不为 Agent 页面增加 KnowledgeBase 选择器，也不把 KnowledgeBase 固定到 Agent 会话。

### 内部知识库组件边界

- `domain` 只放 KnowledgeBase、Source、Document、身份幂等、内容 Hash、索引状态等业务规则，不依赖 Web 框架和外部基础设施。
- `contracts` 放可稳定传递的输入输出 DTO，包含导入、检索、索引、清理和 Source 配置所需的契约。
- `application` 编排导入、索引、检索、Source 绑定和清理用例，负责事务边界和业务规则，不直接创建 SQLAlchemy、Qdrant 或 FreshRSS 客户端。
- `ports` 定义 Document Repository、Source Repository、Embedding、Chunker、Vector Store、外部来源适配器等抽象。
- `adapters` 放 PostgreSQL、Qdrant、FreshRSS 和现有 LangChain/Ollama 实现；外部技术变化不向领域模型扩散。
- `composition` 负责 API、CLI、Job 和运行时装配，保证手动入口和定时入口使用同一套知识库应用服务。
- 迁移顺序采用“先定义公共契约，再迁移具体实现，最后删除新闻专用转接层”，不一次性搬动全部目录。
- 未来拆分独立服务时，优先复用 `contracts` 和 `application`，替换持久化、向量库和来源接入适配器；本轮不启动网络微服务。
- `knowledge_base_id` 在 Source、Document 和 Qdrant Payload 中按正常写入约定保持一致；本轮保留各自指向 `knowledge_bases.id` 的普通外键，不增加 Source 与 Document 之间的组合外键，也不增加覆盖所有未来写入口的通用 Service 校验层。Source 绑定接口针对已有数据的冲突检查是单独的配置操作规则。

### 定时任务

- 继续使用现有 `scheduled_jobs` 和 `scheduled_job_runs`，不新增第二套 Job 表，不重构为通用任务平台。
- 相同 `task_type` 可以存在多条任务记录；实例 `key` 仍然保持唯一，用于区分不同计划。
- 文档清理任务参数增加 KnowledgeBase 范围列表；列表中的多个库共用同一个 `retention_days`。
- 不同保留周期通过多个清理任务实例表达，而不是在同一个任务参数中为每个库维护不同策略。
- 清理任务缺少范围参数时，为兼容旧配置只默认新闻 KnowledgeBase，不解释为全库清理。
- 清理任务明确只处理参数列出的 KnowledgeBase，并继续只选择满足既有安全条件的已完成索引 Document。
- 定时任务前端只在文档清理任务表单中展示 KnowledgeBase 多选；FreshRSS 同步按 Source 绑定分流，待索引任务继续处理统一 Document 表中的候选记录。
- `freshrss_sync` 通过 Source 的 KnowledgeBase 绑定分流，不使用清理任务的范围参数替代来源配置。
- `index_pending` 继续从统一 Document 表处理待索引数据；是否待索引由 Document 状态和已有写协调机制决定。
- 同步、索引和清理的互斥、写资源占用、删除待办、任务执行快照、心跳和人工恢复语义沿用现有决策。
- 任务类型的旧标识先保持兼容，主要修改参数模型、执行 Service 和数据筛选条件，使描述从新闻专用语义改为通用 Document 语义。

### 数据重建和迁移顺序

- 保留 Alembic 历史，新增迁移建立 KnowledgeBase、外键、通用 Document 字段、索引和约束，不修改旧迁移文件。
- 迁移首先建立 `news` KnowledgeBase，并为现有来源和现有 Document 准备新闻库归属；开发阶段随后可以清空知识数据。
- 知识数据清理范围包括现有 Document、Source 的同步游标、旧索引状态和旧 Qdrant Collection/Point；账号、Agent 会话归属、checkpointer 历史和任务配置不因知识重建自动删除。
- 清理、同步和索引前必须停用或协调活动写任务，确认没有未决的 Qdrant 写入和删除待办；不得在旧写入口和新 Payload 契约下无保护混跑。
- Qdrant 先创建新的通用 Collection generation 并准备 Payload 索引，再在数据补齐并验证后切换 current Alias。
- 重建后先配置 Source 到目标 KnowledgeBase 的绑定，再从 FreshRSS 建立新的同步基线，随后执行 Chunk、Embedding 和索引。
- FreshRSS 历史数据由老板负责清理；系统在新的同步基线后正常补齐可读取数据，不在本轮设计额外的历史回灌平台。
- 迁移和重建完成后，旧新闻 Payload 不再作为业务查询来源，旧 Collection 可以按发布流程单独清理。

### 文档和决策同步

- 更新根术语表，使 Document、Source、KnowledgeBase、Chunk、content_hash 和 revision 的定义不再默认只有新闻。
- 修订受本次数据模型、共享 Qdrant Collection、Source 路由和定时清理范围影响的 ADR；不得用代码默默违反旧 ADR。
- 更新后端架构说明、能力地图、跨模块流程和前端 OpenAPI 生成契约，说明 HTTP 兼容默认值与内部必传边界。
- 将后续阶段单独记录为 [0003 检索范围规格](0003-knowledge-base-search-scope.md)，不把后续设想当作当前实现任务。
- 按老板最新要求，本规格与审查规格在验证后保留，暂不删除；稳定运行说明已经同步至 README、架构说明和 ADR。

## 测试决策

- 测试优先验证外部行为和跨模块边界，不把目录名称、私有方法或具体 ORM 拼接方式作为主要断言对象。
- 复用现有 Repository 测试接缝，验证 KnowledgeBase 外键、Source 绑定状态、Document 幂等唯一性、可选 Source、正文变化后的 revision 和索引状态更新。
- 复用 FreshRSS fake client 接缝，验证新 Source 被发现时只保存来源元数据，不调用该来源的文章 ID和内容接口，不写入 Document，也不推进文章处理游标。
- 使用一个非新闻、无 URL、无 Source 的合成 Document 验证通用数据模型能够完成持久化、Chunk、Embedding、Qdrant 写入和全文读取。
- 使用至少两个 KnowledgeBase 和同一 Qdrant Collection 的合成数据验证检索、按 Document 分组、Source 过滤和全文读取不会跨库混淆。
- 验证 Qdrant Payload 中的 `knowledge_base_id`、`document_id`、Chunk 顺序、正文 Hash、模型和索引 Schema 与 PostgreSQL 及当前索引规格一致。
- 验证 Qdrant 缺少 KnowledgeBase 或使用错误 KnowledgeBase 的结果不会被错误返回；普通搜索请求缺省范围时只落到新闻库，Agent Tool 的跨库行为留给后续阶段验证。
- 验证同一 Source 下相同外部 ID 的新增、更新、完全重复同步和跨来源相同正文分别符合既定幂等规则。
- 验证 Source 配置接口能够列出未绑定来源、完成单目标绑定并返回更新后的状态；绑定后的下一次同步才导入 Document。
- 验证 Source 已有 Document、删除待办或未完成写入时绑定变更返回冲突；空 Source 可以绑定、改绑定或解除绑定，且绑定变化后游标从新的同步基线开始。
- 验证 KnowledgeBase 管理页面能够创建、读取、编辑和启停 KnowledgeBase；停用库不出现在普通选择范围，重新启用后恢复；本轮不提供物理删除。
- 复用现有 HTTP API 测试接缝，验证 `knowledge_base_id` 缺省兼容、显式传值、未知 KnowledgeBase、Document 详情和错误契约。
- 后续阶段复用现有 Agent Tool 测试接缝，验证 Tool 能够把可选 KnowledgeBase 范围传给检索应用服务；不把该阶段测试作为当前施工阶段的验收条件。
- 复用现有任务注册表和任务执行接缝，验证同一清理任务类型可以创建多个实例，参数经过规范化和校验，清理只作用于指定 KnowledgeBase。
- 验证一个清理任务指定多个 KnowledgeBase 时共用同一保留周期，不同周期需要多个任务实例；缺省范围不会扩大为全库。
- 验证清理的删除意图、Qdrant 删除确认、PostgreSQL 条件删除和现有写资源协调语义在指定 KnowledgeBase 后仍然成立。
- 验证 Alembic 可以从当前开发数据库升级到新模型；知识数据重建只影响约定范围，账号、会话和任务配置不被误删。
- 前端 Source 管理页面通过 OpenAPI 生成类型完成列表、绑定、成功、失败、空状态和未配置状态测试；不通过浏览器直接访问 PostgreSQL、Qdrant 或 FreshRSS。
- 前端 KnowledgeBase 管理页面覆盖创建、编辑、启停、空状态、加载失败和冲突反馈；定时任务页面覆盖清理范围的多选、参数回填和缺省新闻库兼容。
- 使用无 Source 或无 URL 的内部合成 Document 验证持久化和索引契约；不因该测试增加人工创建 Document、上传或文件解析入口。
- 验证检索结果和全文详情在 Source 或 URL 为空时仍符合 OpenAPI 契约，前端能够渲染未指定来源状态。
- 完成后运行后端针对性测试、后端完整测试、前端类型检查和前端测试/构建；涉及 Qdrant 与 PostgreSQL 的真实集成验证使用隔离的开发数据和随机资源。

## 超出范围

- File System Access API、本地连接器、MCP、本地代码执行、安装开发环境和 Agent 写入用户机器。
- OpenHands、Multica、自部署多租户、RBAC、组织权限、项目权限和新的身份模型。
- Agent Run、消息归档、LLMOps 平台、独立观测服务和其他 Agent 运行记录重构。
- Redis、Celery、持久任务队列、通用任务插件市场和定时任务独立微服务化。
- 同一个 FreshRSS 订阅源同时复制到多个 KnowledgeBase 的扇出同步。
- Source 绑定变化后的自动历史迁移或批量重分类能力。
- PDF、DOCX、代码项目、对象存储、S3、文件上传、文件解析和任意文件生命周期实现。
- Document 正文历史、DocumentVersion 表、PG Chunk 表和为每个 KnowledgeBase 创建独立 Qdrant Collection。
- KnowledgeBase 物理删除；本轮使用 `is_active` 停用代替删除。
- 搜索页面 KnowledgeBase 选择器、Agent 页面 KnowledgeBase 选择器和 Agent Tool 的可选范围实现（这些属于后续阶段，不属于当前施工阶段）。
- 新闻之外的新业务数据源适配器；本轮只保留 FreshRSS 适配器边界，不预建空适配器。
- 生产数据库清空、生产 Qdrant 删除、生产服务切换和发布执行；本规格只定义开发阶段重建顺序和安全前置条件。

## 补充说明

本规格的核心不变量如下：

- 每个已入库 Document 必须属于一个 KnowledgeBase。
- 每个 Qdrant Point 必须携带与 Document 相同的 KnowledgeBase ID。
- Source 只能有一个当前目标 KnowledgeBase；未绑定 Source 不产生 Document。
- 普通 HTTP 检索和清理必须先限定 KnowledgeBase，再应用其他过滤条件；Agent Tool 是有意的跨库检索例外。
- KnowledgeBase ID 在 Source、Document、Qdrant Payload 和任务参数中的取值语义统一，都是 `knowledge_bases.id`。
- 旧 HTTP 接口可以暂时缺省新闻库，但内部知识库应用服务不接受隐式范围。
- 清理任务的执行范围来自任务参数；未配置的知识库不继承新闻保留策略。
- Agent Tool 是有意的例外：不传 `knowledge_base_id` 时检索所有启用中的 KnowledgeBase，传入时只检索指定的启用库。
- `is_active` 关闭表示暂停使用而不是删除数据；重新打开后恢复绑定来源、检索和 Agent 使用能力。
- `revision` 是当前 Document 与索引协调所需的技术版本，不是正文历史版本。

本轮消融结论：删除 DocumentVersion、PG Chunk 表、每库独立 Collection、第二套 Job、`content_revision` 和消息队列不会削弱当前目标，反而减少跨模块状态；不能删除 Document 和 Qdrant Payload 上的 `knowledge_base_id`，否则无法可靠实现多库隔离，也不能删除现有 revision 与写协调状态，否则旧索引写入可能覆盖新数据。
