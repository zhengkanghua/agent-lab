# KnowledgeBase 从配置切片扩展为通用应用组件

2026-09-06 先交付 KnowledgeBase 配置管理；2026-09-07 随 Source/Document 归属与检索、清理过滤成组接入，扩展到完整知识库应用边界。以下当前决策替代初次切片中“只交付配置、停用搜索检查延期”的临时边界。

## 决策

- 新增 `knowledge_bases` 表，初始化稳定键为 `news` 的启用库；稳定键是业务身份，展示名称和说明可以修改。
- 通过 `knowledge/` 内的领域对象、契约、应用服务、端口和 PostgreSQL 适配器提供配置用例。HTTP、CLI 和后续任务入口从装配层取得同一个应用服务，不把 ORM 对象传入领域层。
- 管理 API 由超级用户创建、编辑和启停 KnowledgeBase；列表默认只返回启用库，读取停用配置必须明确传参。当前不提供物理删除。
- 导入、索引、检索、Source 绑定和清理用例依赖纯数据契约与端口，PostgreSQL、FreshRSS、Qdrant、LangChain/Ollama 留在适配器与装配层。DocumentSnapshot 在数据库事务结束前构造，不让 ORM 生命周期进入索引用例。
- 停用库拒绝新增绑定、来源导入和检索，已有数据保留且可显式维护清理。同步网络前检查一次，保存时在短事务中锁定并复核；Source 绑定参加 sync/index 持久写协调。
- 普通内部搜索必须明确范围；HTTP 缺省与当前 Agent Tool 都明确传 news。范围不存在返回 404，停用返回 409，均在 Embedding/Qdrant 之前失败。检索页面选择器和 Agent 可选跨库参数仍属后续交付。
- 索引和 Payload 采用 v2；多个 KnowledgeBase 共用同规格 Collection。重建占用 sync/index，完整构建并验收新 generation 后发布 current Alias，之后条件更新成功快照。构建失败保留原 Alias，发布或确认不确定时保留占用供人工核实；旧 Collection 不自动删除。

## 原因与边界

KnowledgeBase 的配置是所有数据隔离的共同前置。来源页面、清理表单与检索共用同一身份与启停规则，避免配置已经停用但写入或查询仍继续。保留内部组件形态，不增加网络微服务、消息队列或历史回灌平台。

停用是可逆的配置变化，不删除已有数据。更新使用行锁和只修改明确字段的请求，重复提交相同值不刷新更新时间；稳定键从更新契约中排除，避免任务参数或来源映射失效。

## 当前进度

检索边界切片已接入：`POST /vector-search` 和 `POST /document-search` 在 HTTP 边界把缺省范围固定解析为 `news` 的稳定 UUID；调用方可以显式传入 `knowledge_base_id`，文档级请求的顶层范围会与 Qdrant 过滤器合并。两种请求都拒绝顶层范围与嵌套过滤范围不一致的输入。

Qdrant 搜索过滤器把 `knowledge_base_id` 编码为精确匹配，搜索响应要求每个 Point 携带该字段；范围端口统一查询数据库启用状态。未知 UUID 与停用目标都明确失败，不退化为全库查询。合成多库用例覆盖普通搜索和阶段一 Agent 的新闻范围。

归属、配置页面、清理范围及通用 Payload 已接入；`mime_type` 与可空 Source/URL 贯穿构建、Payload 和读取响应。验证与部署状态以测试记录和施工规格为准，ADR 不把离线测试通过解释为真实数据库迁移或生产重建完成。

## 消融结论

删除旧 FreshRSSImportService 转接层及未使用的写入口后，生产装配与行为回归保持通过；不需要并存两套导入服务。日常索引与重建共用切分、Embedding、Point 写入步骤，只有重建适配器能够显式指定新 generation。归属字段、条件版本更新和持久写协调仍是必要边界。
