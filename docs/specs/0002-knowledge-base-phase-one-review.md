# 通用知识库阶段一交付审查与修复规格

> 状态：修复与再次审查完成。2026-09-07，R1 至 R11、D1 至 D4 及最终复审发现的来源元数据遗漏均已处理；当前施工范围未发现尚未修复的阻断项。按老板要求保留本规格与原规格，不删除。
>
> 以下问题与证据保留初次审查时的基线，当前状态以状态表、施工记录和最终验收为准。随机隔离资源验收已经授权并执行，应用数据库升级、业务索引切换和发布未执行。

## 审查基线

- 需求：[0001-knowledge-base-architecture-and-data-model.md](0001-knowledge-base-architecture-and-data-model.md)。重点核验其阶段一边界、停用语义、Source 绑定冲突、通用 Payload、清理范围与内部组件边界。
- 决策：[ADR 0019](../adr/0019-scheduled-execution-and-write-coordination.md)、[ADR 0020](../adr/0020-knowledge-base-management-slice.md)。原规格正文和 ADR 0020 进度声明之间的冲突须明确记录，不能用改写进度代替需求实现。
- 交付声明：后端 571 项通过、13 项门控跳过，前端 666 项通过，typecheck/lint 通过；数据库迁移与 Qdrant 重建未执行。
- 对照当前工作区所有相关修改及新增文件，不仅检查最后一批修改，也不依赖前序代理的完成声明。
- Git 基线为 `6fe984b7a6a441fd60cfe3477d763f00a9a6c047`，审查对象包含其上的未提交修改和新增文件。下文行号对应本次审查时的工作区。

## 初次审查结论

当前不能认定阶段一已完成验收。现有测试数量已复核，但遗漏了真实 JSONB 序列化、停用库的数据行为、未完成写入期间的 Source 配置冲突、无 Source 的完整读写链路和停用库维护范围。这些缺口能够导致任务执行失败、跨库命中或管理员看到的清理范围与实际提交范围不同。

本轮确认 11 项问题，另记录重建交付、文档口径与格式检查的待办。问题的修复要求是供后续开发执行的验收边界，不代表本轮已经实施，也不新增阶段二的页面、Tool 参数或文件导入入口。

## 审查进度

- [x] 亲读原规格、领域术语、架构说明与相关 ADR。
- [x] 核验前端 API 守卫、Source 展示和清理范围表单。
- [x] 核验 Source 同步准入与改绑的并发保护。
- [x] 本地复现 JSONB 参数序列化失败和无 Source Payload 读取失败。
- [x] 检查迁移、Qdrant 重建装配和受门控测试的契约变化。
- [x] 完成现有测试、类型检查、lint、格式检查及生产构建复跑，记录格式检查失败。
- [x] 用内存 Qdrant 和生产 Agent Tool 复现无范围跨库命中。
- [x] 用生产前端 API 客户端和合成响应复现空来源、空 URL 被拒。
- [x] 补齐每项问题的修复边界、验收条件和最终结论。

## 已确认问题

以下优先级表示修复紧迫程度，不代表已实施。P1 影响阶段一核心流程或数据操作范围，P2 为功能、架构交付和验证缺口。

| 编号 | 优先级 | 问题 | 状态 |
|---|---|---|---|
| R1 | P1 | 清理任务参数包含 UUID 对象，不能序列化到配置和执行快照的 JSONB | 已修复，真实 JSONB 配置/快照验收通过 |
| R2 | P1 | 已停用 KnowledgeBase 的 Source 仍继续抓取和写入 | 已修复，真实停用行锁竞争验收通过 |
| R3 | P1 | 普通检索没有核验 KnowledgeBase 的启用状态，保留的 Point 仍可命中 | 已修复，停用/恢复与 HTTP 错误回归通过 |
| R4 | P1 | Agent 当前检索没有阶段一的新闻范围约束 | 已修复，阶段一新闻范围与错误回归通过 |
| R5 | P1 | 无 Source 的合法 Payload 写得进但检索读取失败 | 已修复，真实 PG/Qdrant 通用文档闭环通过 |
| R6 | P1 | 前端 API 守卫拒绝合法的可空 Source 和 URL | 已修复，前端回归及浏览器全文验收通过 |
| R7 | P1 | Source 改绑绕过活动或未确认的写资源占用 | 已修复，持久协调和停用行锁验收通过 |
| R8 | P1 | 清理表单隐藏停用库的已选范围，显示与提交不一致 | 已修复，页面回归及浏览器维护多选验收通过 |
| R9 | P2 | Source 绑定库停用后，下拉框无法展示原绑定 | 已修复，页面回归与桌面/移动展示验收通过 |
| R10 | P2 | 门控集成测试尚未同步新表、必填字段与接口参数 | 已修复，随机 PG/Qdrant 集成及真实迁移通过 |
| R11 | P2 | 内部知识库组件只覆盖配置 CRUD，阶段一其余用例仍未接入 | 已修复，应用边界、生产装配与替身/真实适配器核验通过 |

### R1：清理任务参数不能写入 JSONB

**证据与触发。** [scheduled_task_registry.py](../../backend/src/agent_lab/services/scheduled_task_registry.py#L84) 将 `knowledge_base_ids` 定义为 `list[UUID]`，而同文件 `TaskTypeSpec.validate_params()` 第 145 行使用默认 `model_dump()`，返回的列表元素仍是 Python UUID 对象。[scheduled_job_service.py](../../backend/src/agent_lab/services/scheduled_job_service.py#L107) 将此结果交给配置写入；[scheduled_job_repository.py](../../backend/src/agent_lab/repositories/scheduled_job_repository.py#L290) 在认领任务时也重新校验并放进 `config_snapshot`。两处目标列均为 JSONB，当前数据库装配未提供 UUID JSON 序列化器。

新建清理任务、修改其参数，以及执行缺少范围字段的旧清理任务，都会在配置或执行快照的 JSONB 写入处失败。即使 HTTP 输入是 UUID 字符串，经过 Pydantic 校验后仍会变成 UUID 对象。

**最小修复。** 持久化用的规范参数必须是合法 JSON 数据，例如在统一参数规范化处使用 JSON 模式导出；执行时按参数模型恢复 UUID 类型。配置和快照共用同一规范，避免只修 API 响应。

**验收。** 覆盖缺省、单库、多库、重复 ID 的参数序列化；创建和修改任务、定时认领与手动执行均能保存配置/快照。空列表仍拒绝，缺省仍只解析到 `news`。至少一条验证经过真实 PostgreSQL JSONB 写入，纯字典 fake 不能作为这一问题的完成依据。

### R2：停用库仍继续同步

**证据与触发。** [freshrss_import_service.py](../../backend/src/agent_lab/services/freshrss_import_service.py#L231) 的同步准入只检查 Source 是否已存在、绑定 ID 是否非空；第 248 行后直接进入文章请求。第 609 行读取绑定并写 Document，第 637 行提交；调用链未检查目标 KnowledgeBase 的 `is_active`。

先把 Source 绑定到启用库，再停用该库，然后运行正常同步，仍会拉取文章、写入 Document 并推进 checkpoint。外键只保证库存在，不保证库启用。这违反原规格第 108、123 行的停用语义，且无需并发即可触发。

**最小修复。** 文章请求前检查绑定目标是否启用；停用时跳过文章 ID 和正文请求，保留绑定和 checkpoint。保存页面的短事务内再次检查，覆盖请求过程中停用的情况；停用、绑定和写入若需要锁定同一 KnowledgeBase，应采用一致锁序，避免检查与提交之间的竞争。

**验收。** 停用前已绑定的 Source 在下一次同步中无文章请求、无 Document 变更、checkpoint 不变；请求暂停期间停用后，恢复请求也不提交新数据；重新启用后从原游标继续。来源元数据发现仍允许正常进行。

### R3：停用库仍能被普通检索命中

**证据与触发。** [api/vector_search.py](../../backend/src/agent_lab/api/vector_search.py#L96) 和 [api/document_search.py](../../backend/src/agent_lab/api/document_search.py#L92) 只补齐范围 UUID，再交给 Service；[vector_search_service.py](../../backend/src/agent_lab/services/vector_search_service.py#L192) 和 [qdrant/search.py](../../backend/src/agent_lab/qdrant/search.py#L361) 只按 ID 过滤，没有读取启用状态。

停用操作保留原有 Point；因此，只要该库已有命中数据，显式查询它或在 `news` 停用后执行缺省新闻查询，仍会返回结果。交付总结中的“停用库返回空结果”不能由当前实现保证。没有 Point 的未知 UUID 返回空，与停用库不可查询是两个不同条件。

**最小修复。** 在查询 Embedding/Qdrant 前统一解析并校验目标范围，禁止停用库继续返回命中；明确定义不存在与停用库的 HTTP 结果，维持缺省新闻库和显式范围的同一规则。稳定错误文案可以另行安排，但阻止停用库被使用的业务行为不能以此推迟。

**验收。** 使用已有 Point 的库验证启用、停用、重新启用三种状态，两条 HTTP 检索均覆盖显式参数、嵌套范围和缺省 `news`。停用或未知范围绝不能退化为全库检索。

### R4：阶段一 Agent 实际在查询全部库

**证据与触发。** [search_news.py](../../backend/src/agent_lab/agent/tools/search_news.py#L100) 创建 `DocumentSearchRequest` 时不提供 KnowledgeBase 范围，`_time_filters()` 也只处理时间；[vector_search_service.py](../../backend/src/agent_lab/services/vector_search_service.py#L201) 接受空范围，Qdrant 无条件时不建立过滤器。

共享 Collection 存在新闻库和另一库的数据后，现有 Agent Tool 会将两者一起返回；停用状态也未参与过滤。内存 Qdrant 加生产 Tool 的调用已复现这一行为。这违反原规格第 155 行“阶段一现有检索页面和 Agent 页面继续默认使用新闻知识库”。后续阶段的有意跨库检索也只允许启用库，不能用该远期规则解释当前无限制检索。

**最小修复。** 在 Agent 装配或应用入口明确应用阶段一的 `news` 范围，并接入 R3 的启用检查；不提前增加模型可填的范围参数。内部普通检索入口应要求明确范围，避免其他调用方再次遗漏；将来有意跨库时再使用单独、明确的用例。

**验收。** 在共享 Collection 放入新闻库与其他库的相同向量，现有 Tool 在有、无时间参数时都只命中启用的 `news`；`news` 停用时不得返回任何库的内容。直接调用普通应用接口遗漏范围应明确失败。

### R5：无 Source Payload 的写入和读取契约冲突

**证据与触发。** [qdrant/payload.py](../../backend/src/agent_lab/qdrant/payload.py#L117) 已允许 Source 展示字段和外部 ID 为空，但 [schemas/vector_search.py](../../backend/src/agent_lab/schemas/vector_search.py#L429) 的两个外部 ID 仍是非空字符串；第 480 行起的共用 validator 对可空的来源字段直接调用 `.strip()`。

合法的无 Source Document 可以映射成 Payload，却在 `VectorSearchResult.model_validate()` 处抛出 `AttributeError`；只置空外部 ID 也会得到 `ValidationError`。文档分组检索共用 Chunk 解析，不能绕过此错误。原规格要求的非新闻、无 Source 合成 Document 尚未形成完整读写闭环。

**最小修复。** 对齐 Payload mapper、Chunk 响应和文档分组响应的可空语义；可空值不走必填字符串校验，真实非法字符串仍拒绝。随后从后端重新导出 OpenAPI 并生成前端类型。

**验收。** 一个非新闻、无 Source、无 URL、无外部 ID 的合成 Document 完成构建、Chunk、Payload 写入、Chunk 检索、文档分组检索与全文读取。另覆盖仅 Source 为空、仅 URL 为空、仅外部 ID 为空以及真正缺失必填字段，不新增人工导入页面。

### R6：前端仍把合法空来源和空 URL 当成错误

**证据与触发。** [document-search.ts](../../frontend/src/api/document-search.ts#L94) 的结果守卫仍要求 `isHttpUrl(value.url)` 和 `hasText(value.source_name)`；[documents.ts](../../frontend/src/api/documents.ts#L55) 的全文守卫也相同。生成的 OpenAPI 已允许这些值为空。

检索数组中任意一条合法空值结果就会使 `response.every(...)` 失败，整批报 `response_invalid`；全文也打不开。组件新增的“未指定来源”文案位于 API 守卫之后，无法弥补这一问题。生产客户端配合 stub fetch 已分别复现两种空值，正常响应对照通过。

**最小修复。** 手写运行时守卫与后端契约同步，允许合法 null/可省略字段；非空 URL 仍校验 HTTP(S) 规则，不整体取消响应校验。确认组件只在存在合法链接时渲染链接控件。

**验收。** 搜索结果混合正常项和空来源/空 URL 项时整批可用；全文可打开，卡片与阅读器显示正确兜底。保留非法 URL、错误类型、错误 document_id、缺失必填字段的拒绝用例。

### R7：Source 改绑没有参与写协调

**证据与触发。** [source_binding_service.py](../../backend/src/agent_lab/services/source_binding_service.py#L68) 只锁 Source 行、查询 Document 和删除待办，然后更新绑定；[api/sources.py](../../backend/src/agent_lab/api/sources.py#L20) 直接构造该 Service，未进入共享写协调协议。同步入口在 [news_pipeline_execution_service.py](../../backend/src/agent_lab/services/news_pipeline_execution_service.py#L127) 持有 `sync`，但 [freshrss_import_service.py](../../backend/src/agent_lab/services/freshrss_import_service.py#L345) 提取标量并 rollback 后才等待网络，期间不再持有 Source 行锁。

空 Source 已绑定 A，同步暂停在文章请求时，另一请求可以改绑 B 或解除绑定。恢复同步后，保存阶段重新读到 B，就把本次已启动的任务写入 B；若已解除绑定，则可能因 Document 归属非空约束失败并回滚整页。`uncertain` 或失联写占用也不能阻止绑定。这里的问题是未完成写入期间仍允许修改配置，不能夸大为保存事务内 Source 与 Document 一定归属不一致。

**最小修复。** 在检查和变更 Source 前进入与同步、索引、清理相同的写协调协议。占用检查与修改必须原子协调，不能只增加一次“查看占用表”的查询；继续遵守 ADR 0019 对 `uncertain`、失联占用和人工恢复的规则。

**验收。** 将同步暂停在网络请求处，改绑和解除均返回冲突且配置不变；活动或 `uncertain` 占用下不能绕过保护。写入结束后，有 Document 的来源仍拒绝变更；真正空闲且无数据/删除待办时允许变更并重置 checkpoint。现有不改变目标的幂等调用不应无故改写数据。

### R8：清理表单隐藏停用库的已选范围

**证据与触发。** [ScheduledJobsPage.vue](../../frontend/src/pages/ScheduledJobsPage.vue#L26) 调用 `listKnowledgeBases()`，默认只返回启用库；[useJobForm.ts](../../frontend/src/features/scheduled-jobs/composables/useJobForm.ts#L51) 保留参数中的全部 ID；[JobForm.vue](../../frontend/src/features/scheduled-jobs/components/JobForm.vue#L277) 只把返回的选项渲染成 checkbox。

已有任务参数为 `[A, B]`，B 随后停用：编辑时只能看到 A，但 B 仍留在表单状态。取消 A 后，隐藏的 B 仍满足非空校验并被提交。管理员可见的清理范围与实际参数不同，也无法通过表单把 B 移出范围。R1 修复后正常任务写入更会暴露这一独立问题。原规格第 108 行明确允许针对停用库维护清理。

**最小修复。** 维护清理的候选列表读取启用和停用库，清晰显示停用状态，允许管理员查看、勾选和取消。所有提交 ID 必须可见；无法解析的已有 ID 也须可辨认、可移除或明确阻止提交，不能藏在数组中。

**验收。** 参数同时包含启用/停用库时完整回填；只选停用库仍可配置维护任务；全部取消后非空校验失败。提交范围与可见勾选完全相同。缺少范围的旧任务仍不得被解释为全库。

### R9：Source 所属库停用后，页面看不到当前绑定

**证据与触发。** [useSources.ts](../../frontend/src/features/sources/useSources.ts#L44) 只读取启用库；[SourceDirectory.vue](../../frontend/src/features/sources/SourceDirectory.vue#L107) 仍以 Source 原来的 KnowledgeBase ID 作为 select value，但 options 中没有该停用库，其他单元格也不展示其 key/name。

合法保留的绑定显示为空白，管理员无法区分“未配置”和“绑定到停用库”。更换目标失败后恢复旧 value 时仍不能展示原状态。

**最小修复。** 保留当前停用绑定的展示项或单独显示其身份与停用状态；新绑定候选仍只允许启用库，不能为了显示旧状态而开放新的停用库绑定。

**验收。** 已绑定库停用、重新启用、绑定变更失败三种情况均能辨认当前目标；未绑定状态与停用绑定状态清楚区分。

### R10：门控集成测试已落后于新模型

**证据与触发。** [test_scheduler_postgres_integration.py](../../backend/tests/test_scheduler_postgres_integration.py#L77) 显式建表清单缺少 `KnowledgeBaseRecord`，Source/Document 的外键却已引用该表；第 488 行的 Document seed 缺必填 `knowledge_base_id`。即使补齐 fixture，第 514 行起的 `candidates()` 调用缺新增范围参数，第 523 行的 `upsert()` 也漏必传归属。[test_scheduler_retention_integration.py](../../backend/tests/test_scheduler_retention_integration.py) 复用这些 fixture 和 seed，同受影响。

[test_qdrant_remote_integration.py](../../backend/tests/test_qdrant_remote_integration.py#L42) 仍固定 v1，第 55 行起构造的 Chunk metadata 缺少必需 `knowledge_base_id`。子代理将其客户端替换为内存 Qdrant 后，原测试函数在 Payload 本地校验即报 `QdrantPayloadError`。

**最小修复。** 更新隔离建表和 seed、所有变化的调用签名，以及 v2 Payload 测试数据。迁移路径应使用 Alembic 自身验收，不能只靠 ORM `create_all` 声明模型正确。

**验收。** 经老板授权的隔离 PostgreSQL/Qdrant 环境中运行对应门控测试，覆盖调度快照和删除待办恢复。未接服务时至少完成无网络的 fixture/签名/Payload 核验，并保留“真实集成未验证”的标注，不能用 skipped 视为通过。

### R11：内部知识库组件边界只完成了配置部分

**证据与范围。** [knowledge/application.py](../../backend/src/agent_lab/knowledge/application.py#L14) 只有 KnowledgeBase 的 list/create/update，[knowledge/ports.py](../../backend/src/agent_lab/knowledge/ports.py#L12) 只有配置 Repository/UnitOfWork。Source 绑定直接依赖 `AsyncSession` 和 SQLAlchemy 查询；[pipeline/assembly.py](../../backend/src/agent_lab/pipeline/assembly.py#L15) 仍直接装配 FreshRSS、PostgreSQL、Qdrant 等具体实现。现有部分 Pipeline 抽象可以复用，但尚未形成原规格第 158 行起要求的公共导入、索引、检索、Source 配置和清理应用边界。

这不是要求目录机械搬家。缺失的结果是：调用方还不能通过稳定的知识库用例契约调用全部阶段一能力，Source 等核心编排仍会随存储技术变化而修改。ADR 0020 的“先交付配置”可以解释中间进度，不能支撑最终“阶段一内部组件全部完成”的声明。

**最小修复。** 按一个用例一批的方式收拢 DTO、应用编排与必要端口，复用已有 Repository、Embedding、Chunker 和 Vector Store 抽象，适配现有实现；HTTP、Agent、CLI、Job 通过装配取得同一应用服务。需要先补齐的共享范围解析和写协调，应与 R2/R3/R4/R7 共用，避免各入口各写一套规则。

**验收。** 核心用例不创建或接收 FastAPI、SQLAlchemy、Qdrant、FreshRSS 具体客户端；同一用例可通过 fake 端口验证事务、范围与失败边界，并由现有技术适配器接回真实入口。不增加独立网络服务、第二套调度器或无用途的空适配器。

## 交付补充项

下面保留初次审查的缺口依据；每项开头标记当前完成状态，最终证据见文末。

### D1：补齐可执行的开发重建交接与验证

**状态：已完成生产重建入口、部署文档与真实隔离验收。** 初次审查时，未执行数据库迁移和真实重建本身不作为代码缺陷，因为交付约定由老板执行；但当时的提醒不足以证明原规格要求的重建顺序可执行。

定位：[config/qdrant.py](../../backend/src/agent_lab/config/qdrant.py#L164) 更改 Alias 命名；[Document 迁移](../../backend/alembic/versions/b38f9a7c6d21_add_document_knowledge_base_fields.py#L34) 保留旧 Document 的已索引状态；[document_repository.py](../../backend/src/agent_lab/repositories/document_repository.py#L93) 只选择 pending/failed 候选。单纯升级后启动 `index_pending` 不会自动将旧 indexed Document 补进新 Collection。

此外，[lifecycle.py](../../backend/src/agent_lab/qdrant/lifecycle.py#L155) 在首次创建空 Collection 后立即绑定 current Alias，[store.py](../../backend/src/agent_lab/qdrant/store.py#L125) 固定写 current；`switch_current_alias()` 虽存在，但未见生产用例或命令调用完成“先写目标 generation，验证后再切换”的流程。

后续交付需列明可实际执行的入口、必要参数、知识数据清理/重置范围、Source 绑定与同步基线、目标 Collection 写入和核验、最终切换及失败恢复。原规格允许清空开发知识数据，无需增加无损历史迁移平台；应明确不能只跑 Alembic 后就假设旧 indexed 数据自动可查。若采用停服期间先绑定空库再补数据的不同顺序，须由老板决定并记录与原规格第 190 行的差异。

验收时保护账号、会话、checkpointer 和任务配置；确认无未决写入/删除待办。目标补齐和核验失败时不得对外宣称重建成功，成功后以实际检索和归属一致性验证，最终再决定旧 Collection 的清理。初次审查未执行真实操作；修复后已完成随机隔离验收，业务 Collection 未改动。

### D2：修正文档的完成声明与延期边界

**状态：已同步原规格、ADR 与运行文档。** 初次审查时，原规格第 3 行宣称全部完成，第 7 行把停用搜索错误列为已声明延期；但阶段划分正文只明确延期搜索选择器和 Agent 可选参数。ADR 0020 当时的进度与原规格停用要求冲突，交付总结“停用库为空结果”也与 R3 的实际链路不符。

修复后已区分普通测试、需求验收和实际部署状态。停用检查未延期；只将原定后续的选择器和 Agent 可选范围记录到 0003，未缩减本次范围。原 spec 按老板要求保留。

### D3：前端格式检查未通过

**状态：已修复，格式检查通过。** 初次 `npm run format:check` 返回失败，涉及以下 7 个文件：

- `frontend/src/api/sources.spec.ts`
- `frontend/src/api/sources.ts`
- `frontend/src/features/scheduled-jobs/components/JobForm.vue`
- `frontend/src/features/scheduled-jobs/model/job-validation.ts`
- `frontend/src/features/scheduled-jobs/tests/job-validation.spec.ts`
- `frontend/src/features/sources/SourceDirectory.vue`
- `frontend/src/pages/SourcesPage.spec.ts`

修复仅格式化相关文件，并重跑检查通过。

### D4：明确 Payload 格式与版本记录

**状态：已完成 MIME 全链路与 v2 版本验收。** 初次审查时 PG 已存 `mime_type`，但 [document_builder.py](../../backend/src/agent_lab/pipeline/document_builder.py#L80) 和 Payload mapper 未传递该字段。修复将格式贯穿输入、幂等判定、Snapshot、Chunk、Payload 和检索/全文 DTO，已重新生成 OpenAPI 类型。

[index_spec.py](../../backend/src/agent_lab/qdrant/index_spec.py#L43) 初次默认配置形成 `schema_version=v2, payload_schema_version=v1`；修复后均为 v2，真实重建和读取已核验。原版本记录缺口本身未被表述为已复现的读写故障。

## 初次审查验证

### 本轮已运行

| 验证 | 结果与边界 |
|---|---|
| 后端完整测试 | 571 passed，13 skipped；关闭外部集成开关，使用仓库虚拟环境，`-q --tb=short -p no:cacheprovider`，随机 `output/kb-review-pytest-<uuid>` basetemp，24.64 秒 |
| 前端完整测试 | `npm run test:run -- --reporter=dot`，65 个测试文件、666 项通过，27.37 秒 |
| 前端类型检查 | `npm run typecheck` 通过 |
| 前端 lint | `npm run lint` 通过 |
| 前端生产构建 | `npm run build` 通过 |
| 前端格式检查 | `npm run format:check` 失败，7 个文件，见 D3 |

后端 basetemp 用来绕过默认系统临时目录的访问问题；本轮未删除该目录或处理其他 Python 进程。上述完整测试结果证明现有覆盖通过，不覆盖全部新增业务条件。

### 定向复现

- 在本地使用生产任务参数模型、SQLAlchemy PostgreSQL JSONB binder 与 psycopg `JsonbDumper`，不建立数据库连接：`validate_params({})` 的范围元素类型为 `UUID`，序列化抛出 `TypeError: Object of type UUID is not JSON serializable`。
- 用生产 `QdrantPayloadMapper` 构造无 Source 的合成 Payload，再交给 `VectorSearchResult.model_validate`：写入映射成功，读取抛出 `AttributeError: 'NoneType' object has no attribute 'strip'`。单独将两个外部 ID 置空，还会得到 `ValidationError`。
- 使用 v2 配置、内存 Qdrant、fake Embedding 和生产 `build_search_news_tool()`；同一 Collection 分别写新闻库和另一库的合成 Point，Tool 返回两库标题。未访问真实 Qdrant/Ollama。
- 将生产两条搜索 router 装入最小 ASGI 应用，注入上述生产 Service，显式查询第二库均为 HTTP 200 并返回对应 Point。此验证没有读写真实 KnowledgeBase 的停用状态；R3 关于停用的结论另由完整调用链没有状态检查、停用保留 Point 两项事实支持，不冒充真实停用流程验收。
- 通过 Vite 的模块加载能力执行生产前端 `searchDocuments()` 和 `fetchDocument()`，stub fetch 提供正常、仅空 URL、仅空来源、两者皆空四组响应。正常响应均接受，其余六次调用均报 `response_invalid`。该验证未运行浏览器或对外监听服务。
- 子代理用离线 SQLAlchemy DDL 核验确认调度集成 fixture 不建 `knowledge_bases` 却生成指向该表的外键，并用内存 Qdrant 复现远程集成用例的缺失字段错误；主代理点验了源码位置与新签名。

### 已排除的疑点

- Source 绑定后 `refresh(source)` 会重载先前 selectinload 的 KnowledgeBase 关系；子代理的 SQLite 内存实验中首次绑定、改绑、解除三种返回均一致，不报“返回旧 knowledge_base_key”。
- FreshRSS 已在 rollback 前提取绑定、Source ID 和 checkpoint 标量；交付总结所述 ORM 过期修复在当前代码中存在，不重复列为未修 bug。
- Source 保存事务内重新读取当前绑定并持有行锁，R7 不表述为已经复现 Document/Source 在同一提交中的归属错配。
- 未增加 PostgreSQL Chunk 表、DocumentVersion 表或新业务资料创建入口；不把原规格明确排除的能力算作缺陷。

### 本轮未运行

- 真实 PostgreSQL/Alembic 升级、迁移回滚或数据库清理。
- 13 项受门控的真实服务集成；其中已确认的过时 fixture 见 R10。
- 真实 FreshRSS 同步、Ollama 请求、Qdrant 重建和 Alias 切换。
- Playwright 浏览器交互和桌面/移动端视觉验收。前端页面缺陷以组件与表单数据流核验为依据，API 空值缺陷另有生产客户端复现。
- Git 写操作、发布或真实服务启停。

## 后续修复顺序

以下每一批都必须在实施后更新本文进度与验证结果；当前进展见施工记录。

1. **任务可执行性：R1。** 先修参数持久化，补配置与执行快照的 JSONB 验收。
2. **停用与查询范围：R2、R3、R4。** 形成同一启用范围规则，恢复阶段一 Agent 的新闻范围；补停用和重新启用用例。
3. **通用读取链路：R5、R6。** 从内部合成 Document 到 Payload、HTTP、前端守卫和展示成组修复，再生成 OpenAPI 类型。
4. **配置与维护边界：R7、R8、R9。** 先解决写协调，再保证所有已选维护目标和当前 Source 绑定可见。
5. **交付收尾：R10、R11、D1 至 D4。** 集成 fixture 和组件边界可以按用例拆小推进；文档、格式和重建交接以最后实现为准。

R11 的最小应用边界可以随前四批逐步形成，不要求最后一次性搬目录。真实集成和部署步骤单独等待授权或由老板执行，普通单元测试通过不自动赋予这类操作权限。

## 消融与完成条件

初次审查只读，后续修复已完成实际消融：删除旧 FreshRSSImportService 转接层、无调用方的 `save_many` 写入口、前端闲置 `bindingId`、导入端口闲置查询和重复元数据写入；日常索引与重建复用 `write_snapshot`。删除后对应行为回归及最终全量测试通过。保留归属过滤、事务外 Snapshot、条件版本确认及持久写协调，因为它们分别保护多库隔离、ORM 生命周期和跨存储失败恢复。

- [x] 审查问题、证据、优先级和修复验收要求已成文。
- [x] 已复核测试通过数量并如实记录失败与未运行项。
- [x] R1 至 R9 的行为缺陷修复并完成相应回归验收。
- [x] R10 的集成测试契约与 R11 的应用边界完成修复和核验。
- [x] D1 至 D4 的交付缺口得到处理，原规格与 ADR 完成声明反映事实。
- [x] 后端相关测试、前端测试/typecheck/lint/format/build 通过；真实迁移、并发及重建验证有独立结果，未执行项如实记录。
- [x] 修复后完成消融；按老板最终要求保留两份 spec。

本文是审查与后续修复的工程记录。审查完成不等于缺陷修复完成，也不触发删除本文或原规格；两份 spec 均保留到验收后由老板确认删除。

## 施工记录

- 2026-09-07：老板授权实施 R1 至 R11 和交付补充项；增加完成条件：修复后再次逐项审查原规格，处理新发现的问题，并逐项记录测试证据，跳过项不得计为通过。
- R1：统一以 JSON 模式导出任务配置和执行快照参数；执行端按相同模型恢复 UUID。真实驱动 JSONB 序列化与任务执行参数类型回归已通过，连同任务注册表、调度、生命周期、安全与 API 共 78 项通过；真实 PostgreSQL 写入验收仍待运行。
- R3/R4：接入只读范围端口与统一启用规则；普通内部搜索拒绝遗漏范围，HTTP 缺省和阶段一 Agent 明确传 news；补充 404/409 及 Agent 脱敏错误。两条 HTTP 搜索、Agent、停用/恢复、缺省/显式/嵌套范围及已有搜索回归共 145 项通过。
- R2：文章请求前检查启用状态，保存页面时锁定 KnowledgeBase 再复核；只推进 checkpoint 的页面同样受保护。同步、Pipeline、CLI 等 78 项回归通过；真实行锁并发验证待运行。消融删除无生产或测试调用的 `save_many` 旧写入口，避免另一套绕过来源准入的导入路径。
- R7：Source 用例改为依赖纯快照、Repository/工作单元与写协调端口；配置变更非等待式取得 sync/index 资源，冲突立即拒绝，所有事务退出路径先关闭工作单元再释放占用。新增 16 项专项离线测试覆盖绑定生命周期、待办/Document 冲突、事务收尾、权限和错误、活动/排队/uncertain/失联占用；真实数据库并发仍待验收。
- R5/R6：统一外部 ID、Source 和 URL 的 null 语义，保留非法字符串/URL和必填字段校验，前端增加 KnowledgeBase UUID 守卫。新增非新闻 Document 的四类空值闭环测试，经过真实构建器、Chunker、Embedding 适配器、内存 Qdrant、HTTP 搜索与全文接口；持久化使用替身，不能替代 PostgreSQL 集成。连同 R7、搜索和既有回归，后端 128 项通过；前端 API 与展示组件 41 项通过。OpenAPI 生成与最终全量检查待后续批次统一运行。
- R8/R9：维护清理读取启用及停用库，未知已选 ID 明确展示、可移除，未解析前不能保存；Source 仅为当前绑定保留停用选项，新目标仍限定启用库。页面回归 22 项通过，覆盖全取消、仅停用库、未解析 ID、失败恢复和重新启用。消融删除未被任何调用方消费的 `bindingId` 状态。
- R10：隔离 PostgreSQL fixture 增加 KnowledgeBase 表和 news 种子，Source/Document seed 带归属，清理查询与写入签名同步；远程 Qdrant 样本采用 v2 并检查归属。真实服务未连接，仍待独立集成验收。
- R11：导入用例接入外部来源与 PostgreSQL 工作单元端口，FreshRSS 分页、白名单及协议映射集中到适配器；索引接收事务关闭后可用的 DocumentSnapshot，搜索和清理共用纯 DTO/端口。生产 HTTP、CLI、scheduler 装配已同步，相关 13 个后端文件共 193 项回归通过。删除旧 FreshRSSImportService，不保留重复转接层；格式贯穿、重建用例和最终边界核验继续推进。
- D1：新增 `rebuild-index --generation` 显式重建入口，复用 sync/index 持久协调；覆盖全部 Document（包含已 indexed），新 generation 逐篇回读 Payload 并核对总数、PG 版本，全部通过才切 Alias 和更新成功快照。失败保留旧 Collection；发布阶段不确定结果保留人工恢复占用。专项及已有 CLI/索引/生命周期共 81 项通过；真实服务尚未执行。
- D4：`mime_type` 已贯穿导入 DTO、幂等更新判定、Snapshot、Chunk、Payload、两条搜索响应和全文响应；索引与 Payload 默认版本统一为 v2。非新闻 Markdown Document 的可空字段离线闭环 13 项通过；OpenAPI 已由实际 FastAPI 应用重新导出并生成前端类型。
- 真实隔离验收入口：新增 PostgreSQL JSONB 配置/快照、停用行锁竞争、通用文档持久化及从前一版本升级的门控测试；Alembic 支持注入限定 search_path 的连接。离线收集正常，5 项按门控跳过，不计为验收通过。
- 2026-09-07 集成验收：获得随机测试资源授权后，知识库、调度、多进程协调和清理恢复共 14 项真实 PostgreSQL/Qdrant 验收通过。首次运行 13 passed、迁移 fixture 失败；补齐旧表 `created_at`、`stats` 测试值后，单条迁移重跑通过。迁移从 `f1a8c3d9e602` 升至 `b38f9a7c6d21`，完整保留账号、Token、会话归属、checkpointer 消息/待处理写入、任务配置和执行快照。另单独运行随机远程 Qdrant 生命周期验收，1 项通过。
- 整体回归基线：后端 635 passed、19 skipped，前端 65 文件 696 passed；前端 typecheck、lint、format:check、build 均通过。后端最后一批来源元数据修复后正在做最终回归；外部跳过项不计通过。
- 最后复审新增问题：订阅改名/地址变化遇到未绑定、停用或空增量页时，来源元数据未保存。发现事务改为幂等更新元数据后再判断文章准入，删除页面保存时重复的元数据写入与闲置查询端口；三种状态的改名回归及同步/CLI 共 49 项通过。不额外拉文章、不推进游标。

## 最终验收

| 检查 | 最终结果 |
|---|---|
| 后端完整离线回归 | `pytest -q --tb=short -p no:cacheprovider --basetemp <随机目录>`：638 passed、19 skipped，26.82 秒；本轮最后代码修改后执行 |
| 前端完整回归 | 65 个文件、696 passed；此后未修改前端生产代码 |
| 前端工程检查 | typecheck、lint、format:check、build 均通过 |
| 真实 PostgreSQL/Qdrant 隔离验收 | 15 项通过；升级、消息历史保留、JSONB、行锁、调度协调、清理恢复、全量重建、双库过滤及 UUID Payload 索引 |
| 浏览器 | 1440×1000 和 390×844：创建/编辑/启停知识库、绑定 Source、停用绑定展示、清理多选、空来源全文打开/关闭及页面无横向溢出通过 |
| 最终代码复审 | 核验生产装配、范围、事务收尾、重建失败边界；子代理窄审查发现来源元数据遗漏，主代理点验并完成修复；没有其他未解决的确定缺陷 |
| 文档与消融 | 原 spec、ADR、架构、流程、能力地图、README 已同步；已删除冗余转接/状态/写入口，保留必要一致性保护 |

D1 由 `rebuild-index --generation N` 和真实重建验收闭环；D2 完成原规格与 ADR 口径同步；
D3 格式检查已恢复通过；D4 的 MIME 贯穿链路与 v2 版本已验收。原规格的当前要求对照表
见 [0001](0001-knowledge-base-architecture-and-data-model.md#最终验收对照)，明确延期范围单独记录在 [0003](0003-knowledge-base-search-scope.md)。

最后来源元数据调整后的 `save_page` 真实行锁用例单独重跑通过（1 passed，43.10 秒），
未重复运行没有变化的其他集成用例。`git diff --check` 通过。

浏览器截图保存在 `output/playwright/knowledge-bases-{desktop,mobile}.png`、
`sources-{desktop,mobile}.png`、`retention-{desktop,mobile}.png`、`reader-{desktop,mobile}.png`。
浏览器使用合成 HTTP 响应，不冒充真实后端端到端验收。脚本初期有 mock 路径/cron 接口遗漏及定位器名称错误，
已修正并重新完成对应操作，没有计为产品缺陷或测试通过。

19 项默认门控跳过中，15 项已单独使用随机资源验证；其余 4 项既有账号、Agent 归属及 Ollama
环境集成未运行。真实 FreshRSS/Ollama 请求、应用数据库升级、业务 Collection 切换、旧业务数据删除、
发布与 Git 写入均未执行。内存 Qdrant 对 Payload 索引发出一条标准提示，实际 UUID 索引已由真实服务验收。

## 追加应用环境修复（2026-09-07）

老板反馈 5175 管理页不可用。真实链路检查发现本地 8000 是旧后端进程，两组新接口返回 404；共享应用数据库仍停在 `f1a8c3d9e602`，Qdrant 仍使用旧新闻 v1 Alias。此前合成浏览器及随机资源验收不能覆盖这一实际部署断点。

老板已授权共享数据库升级与本地后端重启。助手通过现有配置直连服务，无需 SSH；当时计划先暂停两条旧自动任务、等在途执行结束，再增量迁移、全量重建并验证真实页面，保留文档和旧索引。该保留数据方案随后被老板调整，最终状态和任务原配置见 [0001 应用环境修复](0001-knowledge-base-architecture-and-data-model.md#应用环境修复2026-09-07已完成)。线上旧调度程序不兼容新增必填归属字段，恢复自动执行必须先升级执行端；本次未授权 Git 发布。

两条自动任务已暂停并确认无在途执行；两个已核实的本地旧后端进程已停止。共享数据库已由 `f1a8c3d9e602` 成功升级至 `b38f9a7c6d21`，事务内核对 12 张原有表的内容指纹一致；975 篇 Document 和 6 个 Source 已正确回填 news。本地 `.env` 的 Qdrant Schema 配置升为 v2，继续执行全量重建和真实页面验收。

清理前真实页面验收通过：8000 后端启动成功；经 5175 正常账号密码登录后两组管理接口均返回 200，news 与 6 个已绑定来源可见。Playwright 在 1440×1000 和 390×844 检查列表、刷新、编辑表单打开/关闭，无脚本错误或横向溢出。使用真实后端，未拦截或替换 HTTP 响应。当时全新 v2 的重建仍在运行，随后按老板新决定中断，最终单条验收结果见下文。

**老板随后调整方案**：开发阶段旧数据均可丢弃，明确中断全量重建、清空知识数据，只写一条新数据验证全流程，后续稳定再启用任务录入。重建进程已停止，此新决定取代前述等待重建的操作计划。清理范围是本项目 Document、Source/游标及 v1、未完成 v2 业务 Collection；保留知识库配置、账号/会话/checkpointer/任务记录，不操作其他项目 Collection。继续完成真实发现、绑定、单条同步索引、检索、全文及相应失败边界验收。

清理及单条真实验收已通过：旧 975 篇 Document、6 个 Source/游标和两个业务 Collection 已删除，10 张保留表内容指纹一致；重建进程和旧 Collection 均已确认停止/删除后，通过维护入口释放遗留写占用。真实 `/pipeline/run-once` 首次发现 6 个未绑定来源，文档、游标推进及新 v2 Point 均为零。绑定一个来源后仅同步/索引 1 篇，数据库 Hash/revision/成功索引快照一致；停用库不导入、不推进游标且搜索 409，未知库 404，已有 Document 的 Source 解除绑定 409。默认 news/显式 news 文档检索、Chunk 检索及全文接口均为 200；4 个 Point 的完整 Payload 与当前 PostgreSQL 派生值逐个一致，UUID Payload 索引与新 Alias 正确。6 个来源已绑定 news，自动任务仍停用；最后补当前样本的真实浏览器检索/全文验收。

**追加环境任务已完成**：1440×1000 与 390×844 的真实浏览器管理、检索、打开/关闭全文验收通过，无脚本错误和横向溢出。重复索引返回成功且零候选/零新增/零失败，最终仍为 1 篇 indexed/v2 Document、4 个 Point、6 个已绑定 Source；Hash 和 revision 一致，写占用、删除待办、在途任务均为零。两条任务 `enabled=false`、配置版本 2，5175 代理健康检查为 200。旧新闻 Alias/Collection 已删除，其他项目 Collection 保留；未使用 SSH、未执行 Git 写入或线上程序发布。后续启用自动任务前，执行端须使用当前代码和 v2 配置。

本轮未改动生产代码，因此没有重复已通过的完整离线回归；新增验证集中在此前未覆盖的真实应用环境和单条端到端链路。原代码验收的 638/696 项及 15 项隔离集成结果仍按原记录保留，4 项未运行的环境测试文件未计为通过。两份 spec 按老板要求保留。

## 第一阶段提交收尾（2026-09-07）

老板确认先收尾第一阶段、分批 commit，第二阶段先讨论，所有相关 spec 保留。本次基于仓库已有 `90a44b2` 测试精简提交再次运行完整回归：后端 `637 passed, 19 skipped`，44.85 秒；前端 65 个文件 `696 passed`，lint、format:check、build（含类型检查）均通过。后端历史 638 项结果不覆盖本次真实计数，19 项门控跳过仍未计为通过。

后端提交 `0f8751c`，前端提交 `57f8a8d`，术语、ADR、流程、原 spec、review spec 及第二阶段草案随本次文档提交收录。第二阶段范围见 [0003](0003-knowledge-base-search-scope.md)，当前代码没有提前实现页面选库或 Agent 跨库能力。本次只有本地提交，未 push 或部署；根 `AGENTS.md` 的既有独立改动保留工作区。
