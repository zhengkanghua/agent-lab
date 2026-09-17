# 库表不建数据库级外键，引用完整性由业务层维护

2026-09-17，老板确认把「库表不建数据库级外键」立成明确规范：**库表可以有外键「字段」，
但不建外键「约束」，也不靠数据库做级联。** 本次用一条总迁移把存量 16 处约束全部拆掉，
并把连带语义搬到业务代码里。

## 背景：一条已经存在、但没有出处的旧规范

项目里本来就有「新表新列一律不加外键、关系在业务层维护」的说法。审查时发现它**只活在
别人的引用里**——出现在 `docs/specs/0001`、`docs/specs/0002`、`docs/adr/0027` 三处，
三处**全是转述**，不在根 `AGENTS.md`、不在 `CONTEXT.md`、不在任何 ADR 正文里。一条规则
没有出处却已被引用三次，后来的人只能靠转述拼凑它的含义。

更要紧的是它**被解释窄了，而且这个窄解释正好用反了**。ADR 0027 当时把它读成「只针对
跨存储系统的关系」，于是得出「同库同域的表反而应该加外键」的结论，并据此批准给
`user_preferences.user_id` 加 `CASCADE`。老板的原话里没有任何「跨存储」限定，那个限定
是后来加上去的。

本次做的是把这条规则**立成明确的一条、扩大它、并用一次迁移把存量对齐**——是「两条规则
合并成一条」，不是新增限制。

## 决策 (Decision)

1. **库表可以有外键「字段」，不建外键「约束」。** `document_id`、`user_id` 这些列保留，
   类型、`nullable` 都不变；去掉外键不等于去掉这列。
2. **不靠数据库做级联。** 库上不再有 `FOREIGN KEY` 约束，随之没有
   `ON DELETE CASCADE / RESTRICT / SET NULL`。级联删、置空、防孤儿全部由业务代码在
   **同一事务**内显式完成。
3. **索引一个都不动。** 所有为查询而建的索引——单列的和复合的——原样保留。（术语澄清见下。）
4. **连带语义的落点：Repository 与 Service，不新建抽象模块。** 「删 documents 要连带删
   哪些子表、按什么顺序」这类知识放在各聚合自己的 Repository 里；删账号要跨三个聚合，
   所以落在 `UserAdminService`。
5. **规范写进根 `AGENTS.md` 的「业务与数据约束」**，因为它是每次动库表都要守的常驻规则；
   「删父表必须连带子表」这条义务写进 `backend/AGENTS.md`，让改代码的人能看见。
6. **本决策取代 ADR 0027 中「同库同域应当加外键」那段论证**，见下。

## 术语澄清：「外键索引」指的是约束，不是索引

老板澄清过，「外键索引」指的是**把两张表连起来的那种约束**，不是普通索引。这一条必须写下来，
因为按字面理解很容易变成「把所有外键列上的索引也删掉」，那是另一回事，而且会造成性能退化。

保留索引的理由逐条记录，它们都是既有查询的依据，与「这列是外键」无关：

- `agent_threads` 的 `(user_id, last_active_at DESC)` 复合索引服务会话列表「我的 + 按最近活跃
  倒序」；只留单列索引的话，PostgreSQL 得把该用户的全部会话取出来排完再分页。模型注释里
  已写明该索引专为此设计。
- `access_tokens.user_id` 索引服务「按 `user_id` 批量撤销 Token」这条既有写路径；删掉索引
  会退化成全表扫描。删账号清理 Token 走的正是这条路。
- `scheduled_job_runs` 的 `job_id`、`retry_of`、`expires_at` 索引分别服务「按配置查历史」
  和待办扫描。

## 拆掉了什么：16 处约束

16 处列级外键分布在 8 张表上，涉及 6 个模型模块。按 `ondelete` 归类有助于理解它们各自的
连带语义迁到业务层之后意味着什么：

- **`CASCADE` 五处**（`document_processing_records.document_id`、`document_versions.document_id`、
  `document_review_records.document_id`、`agent_threads.user_id`、`access_tokens.user_id`）：
  删除时必须由业务代码显式删子表。
- **`RESTRICT` 六处**（`documents.knowledge_base_id`、`documents.source_id`、
  `document_versions.processing_id`、`document_review_records.processing_id`、
  `sources.knowledge_base_id`、`scheduled_job_runs.retry_of`）：原本靠数据库拒绝删除来保护，
  拆掉后业务代码必须自己判断并拒绝，否则会删出孤儿。
- **`SET NULL` 五处**（`documents.current_version_id`、`documents.latest_processing_id`、
  `documents.draft_processing_id`、`document_review_records.actor_id`、
  `scheduled_job_runs.job_id`）：原本由数据库自动置空，拆掉后业务代码要显式置空。

`write_operations.run_id` 和 `document_deletions.document_id` 不是外键（都是裸 `Uuid` 列），
是原本就刻意不加外键的先例，本次不动。

### 各连带语义的业务层等价物

`RESTRICT` 的六处里，只有两处需要新写判断，其余四处的删除动作**本就不存在**：
`knowledge_bases` 和 `sources` 没有物理删除路径（`knowledge_bases.py` 的注释明写「不提供物理
删除」），所以它们被 `RESTRICT` 引用的场景当前不可达。这两处拆掉后**暂无业务层等价物**，
这是有意的——为不可达的场景写判断只是在给以后的人留一段没人能验证的代码。

- `documents` 的删除路径**已经完备**：`DocumentRetentionRepository.finish()` 在同一个方法里
  完成「断开三个 `SET NULL` 指向 → flush → 按序删 `DocumentReviewRecord`、`DocumentVersion`、
  `DocumentProcessingRecord` → 删主表」。它是全仓删除这三张子表的**唯一**路径。拆约束后这段
  逻辑照旧成立，只是顺序的理由从「外键顺序」改成了「业务层约定的删除次序」。
- `scheduled_job_runs.retry_of` 的 `RESTRICT` 等价物**已经存在**：`TaskStore.prune_history`
  用 `~exists().where(child.retry_of == JobRunRecord.id)` 保护仍被重试关联的失败记录。这是
  「把 RESTRICT 搬到业务层」的既有先例，无需新增代码。
- `scheduled_job_runs.job_id` 的 `SET NULL` **是本次新发现的缺口**：`delete_job()` 此前是裸
  `session.delete()`，而模型侧写的是 `passive_deletes="all"`，两者合起来意味着这次删除完全
  靠数据库的 `SET NULL` 兜住。本次在 `ScheduledJobRepository.delete_job` 补上了置空那一步。

### 补上的删账号路径

审查确认 `UserAdminService` 此前**没有删除账号的方法**，全仓唯一真正执行 `session.delete(UserRecord)`
的地方在测试文件里。因此拆掉 `CASCADE` 与 `SET NULL` 之前，必须先补一条业务层删账号路径，
在**同一事务**内按顺序处理三张表，最后删账号：

1. `agent_threads`：显式删该账号的会话归属记录（原 `CASCADE`）。
2. `access_tokens`：显式删登录 Token（原 `CASCADE`）。
3. `document_review_records.actor_id`：显式**置空**（原 `SET NULL`）。

第 3 条指向的表**不是审批流**，而是既有的换版决策留痕表：它记录「某份 Document 的新候选正文
要不要替换当前正式正文」这个决定。处理方式要与其原语义对齐——置空 `actor_id`，但决策记录本身
和 `content_snapshot` 全部保留。历史换版决策是客观事实，不因为操作者账号消失而失效，只是
「是谁做的」不再可回溯。这与数据库 `SET NULL` 的原行为完全一致，**不新增任何删除限制、
不引入审批、不改用户可见行为**。

删账号**不扩大清理范围**：现状是数据库级联只清 `agent_threads` 业务行、不碰 checkpointer，
残留历史由 `prune-orphan-threads` 回收；补的这条路径与现状一致。

## 为什么不去走第三方认证库的删除钩子

补删账号路径时有一个看似更省事的选择：把清理逻辑挂到 fastapi-users 的删除钩子上。放弃的理由
是**把业务逻辑寄存在别人拥有的钩子里**，与既有 ADR 拒绝「把安全字段放进第三方 schema」是同一类
错误：钩子的调用时机、是否被绕过、升级后是否还在，都由上游决定，而我们要守的是
「删账号不留孤儿」这条业务义务。义务放在自己的 Service 里，改代码的人搜得到。

## 为什么拆约束会连带改模型：`relationship` 的 join 推断

这是本次**最容易漏、且漏了就直接起不来**的一步。`ForeignKey` 在 SQLAlchemy 里同时干两件事：
一是生成库上的约束，二是**给 `relationship` 提供 join 条件**。去掉 `ForeignKey` 会一并拿走
第二件事。

关键点：**即使某个 `relationship` 没有被业务代码使用，只要它存在，mapper 配置期就会失败**——
不是「用到才炸」，是应用第一次触发 ORM 映射就炸，而 mapper 配置是**全局且懒触发**的：导入
全部模型只注册、不配置，第一次 ORM 操作才触发，那一次会把 16 个 mapper **一次性全部配置**。
所以报错会出现在**看似无关的测试**里（实测：摘掉 `scheduled_job_runs.job_id` 的外键后，
`tests/test_scheduler_runner.py` 报 `NoForeignKeysError`），而且一次只报第一个，8 处会逐个暴露。

16 处外键里有 5 列是 8 个 `relationship` 的推断依据，全部补了 `primaryjoin` + `foreign_keys`：

| 外键列 | 依赖它的 `relationship` |
| --- | --- |
| `documents.source_id` | `DocumentRecord.source`、`SourceRecord.documents` |
| `documents.knowledge_base_id` | `DocumentRecord.knowledge_base` |
| `documents.current_version_id` | `DocumentRecord.current_version` |
| `sources.knowledge_base_id` | `KnowledgeBaseRecord.sources`、`SourceRecord.knowledge_base` |
| `scheduled_job_runs.job_id` | `ScheduledJobRecord.runs`、`JobRunRecord.job` |

两个未被业务使用的 `relationship`（`ScheduledJobRecord.runs` / `JobRunRecord.job`）也一并修了，
没有以「没人用」为由删掉——删掉会改变 ORM 行为面，与「不改用户可见行为」的边界不符。

**验证这条改动是否生效的办法**：跑一次任意 ORM 查询（或 `configure_mappers()`）。只
`import agent_lab.main` 是不够的——导入不触发 mapper 配置。

## Consequences

**数据库不再有任何兜底。** 这是本次最主要的代价，老板已知悉并接受。今天写错删除顺序，数据库
会直接报错拦住；以后会静默留下孤儿数据。风险有两层，都要如实记住：

1. **写错删除顺序** → 静默产生孤儿。缓解手段是「删父表必须连带子表」这条义务写进了
   `backend/AGENTS.md`，以及连带逻辑集中在各聚合自己的 Repository/Service 里，路径唯一、
   可读可测。
2. **改错模型** → 把 `ForeignKey` 从模型里去掉会顺带打断 `relationship` 的 join 推断
   （`NoForeignKeysError`，应用起不来）。这是这条规范第一次落地时最容易被低估的代价，
   也是为什么模型层修复必须与迁移同一次做完。

**「库上无外键约束」不固化成长期回归断言。** 它是一次性迁移的验收条件，用 `pg_constraint`
在迁移测试里查一次即可。把它固化成长期断言等于把实现细节变成需要永久维护的测试，与
「不测实现细节」相矛盾。

**`user_preferences` 的连带义务。** ADR 0027 批准给 `user_preferences.user_id` 加 `CASCADE`
的那段论证随本决策失效。将来实现该表时**不建外键**，删账号清偏好必须接进本次补的这条
`UserAdminService.delete_user` 路径、同一个事务。理由与应用到 `agent_threads`/`access_tokens`
的完全相同；配置尤其不能指望运维命令兜底——会话有 `prune-orphan-threads`，配置没有对应的
清理命令。

**孤儿清理命令的定位不变。** `agent-lab prune-orphan-threads` 服务的场景是「功能上线前的历史
数据没有归属记录」，**不应**被当成日常删账号的清理手段。正常路径靠业务代码在同一事务内清理干净。

**删账号入口是随后单独确认新增的。** 拆外键本身只需要补一条**业务层**删账号路径，所以第一轮
落地时只加了 `UserAdminService.delete_user`，没有动接口和页面——开一个删账号的 HTTP 路由是
**新增用户可见能力**，不该顺手夹带在拆外键里。

老板随后确认要这个入口，于是补上了 `DELETE /admin/users/{user_id}` 与账号管理页的「删除账号」
键。它与拆外键是两件事，只是碰巧同批交付：拆外键给出的是**能力**，入口是**要不要暴露这个能力**
的独立决定。记在这里是为了让后来的人知道：这条路由不是因为拆外键而必然存在的。

## 被取代 / 需要对齐的既有文档

- **ADR 0027**：保留原文论证不动，在正文里加取代说明。被取代的是它对「跨存储才不加外键」
  的窄解释，以及 `：28-29` 那条批准 `CASCADE` 外键的决策条目。ADR 0009 的跨存储论述
  （「checkpointer 那四张表没法建指向 `users` 的外键」）在新规范下**依然成立**——那是真的跨存储，
  不是本次要动的对象。
- **`backend/docs/architecture.md`**：`agent_threads` 那句「有指向 `users` 的外键
  （`ON DELETE CASCADE`）」以及 `scheduled_jobs` 那句「配置删除只清空可选外键」都要改，
  否则拆完就成假话。这份文件是后端内部实现的首要入口，比 specs 更容易被读到。
- **`docs/specs/0002`**：它是历史留档（文件头写明「不要按它实现」），其中
  「表上已有的 `user_id` 外键按既定决定保留不动」与新规范冲突，标注取代即可，不改正文。
