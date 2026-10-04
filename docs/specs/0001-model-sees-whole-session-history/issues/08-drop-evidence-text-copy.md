# 08: 去掉证据里的正文副本（引用机制本身保留）

**要交付什么：** 点开引用时不再显示「当时引用的片段」那个区块——那段正文在工具轨迹里本来就有、而且是完整的。引用机制本身照旧：答案里的标识、点开引用、无法核验的提示、文档归属与正文版本、来源链接、正文改没改过的提示，全部保留。

**与 07 改的是同一个函数的相邻几行，顺序上让 07 先落**，免得同一处改两遍。

**本组与另外两组一起部署。** 会话历史那张表不在，被压缩过的旧轮次连「当时引用的片段」也没有了。

**被谁阻塞：** 无（可立刻开工，但按上面的顺序说明排）

**状态：** 已完成

- [x] 点开引用看到文档、版本与来源，以及「正文改没改过」的提示；「当时引用的片段」区块不再出现
- [x] 接口契约里没有那个正文片段字段，「是否被截断」也一起去掉；前端校验与自动生成的类型跟着改
- [x] 那条已交付的前端断言删掉
- [x] 流程文档里「阅读器显示当时取得的片段」与架构文档里两处提到「保存／展示当时片段」的句子改掉
- [x] **ADR 一律不动**：那两份相关的 ADR 已经同步过，另一份含同类字句的已被取代、按仓库约定不顺手改

## 验收记录（本工单被提前到 0003/05 之前做，理由见下）

改了什么：`agent/evidence.py`（`DocumentEvidence` 删 `excerpt` 与 `truncated`）；`tools/read_document.py` 与 `tools/search_documents.py`（**交给模型的工具正文改成用本地原始数据拼**，取值与删字段前逐字相同）；`schemas/agent_chat.py` / `schemas/agent_thread.py` 引用同一模型，OpenAPI 三处一起变；前端 `api/agent-evidence.ts`（删 `hasText(value.excerpt)` 与 `truncated` 校验）、`shared/ui/DocumentReader.vue`（删「当时引用的片段」区块、`evidence` prop 与 `EvidenceSnapshot` 类型及专用样式）、`pages/AgentChatPage.vue`（删只服务那个 prop 的 `selectedEvidence`）、`AgentChatPage.spec.ts`（那条已交付断言）、`api/agent-chat.fixture.ts` 与 `api/agent-chat.spec.ts`（同源的载荷与断言）；文档三处：`docs/flows/agent-answer-evidence.md:13`、`backend/docs/architecture.md:352` 与 `:454`。

| 勾选项 | 观察 |
|---|---|
| 区块不再出现、保留项都在 | `frontend/src/pages/AgentChatPage.spec.ts:492-495`：断言弹窗里有文档标题、来源（`source_name` 为空时回退 `upload_filename`）、「原文已更新」，且 `not.toContain('当时引用的片段')` |
| 契约没那两个字段 | `list(DocumentEvidence.model_fields)` = 11 个（`citation_id`/`document_id`/`knowledge_base_id`/`knowledge_base_name`/`title`/`content_hash`/`source_name`/`upload_filename`/`url`/`published_at`/`kind`）；`openapi.ts` 的差异**只有** `DocumentEvidence` 里的 `excerpt: string` 与 `truncated: boolean` 两行被删 |
| 已交付断言删掉 | 除主断言外，还清掉了两处同源件：`agent-chat.fixture.ts` 里那两个键（留着 `vue-tsc` 会红）、`agent-chat.spec.ts`「拒绝契约漂移」里拿 `{...agentEvidence, excerpt: ''}` 当非法载荷那条（字段与校验都没了，那个载荷已合法） |
| 文档三处 | `grep -n 片段 docs/flows/agent-answer-evidence.md backend/docs/architecture.md` 只剩三处与本字段无关的「片段」（工具结果里的命中片段、检索接口的 `matches_per_document` 说明）；`architecture.md:454` 的措辞对齐了 ADR 0044 那句「引用证据（文档归属、`content_hash`、来源）」 |
| ADR 不动 | `git status --short docs/adr/` 只有**老板之前自己改的** 0021/0031，没有任何新增修改 |

**已跑**：`uv run pytest -q` → **1031 passed, 76 skipped**；前端 `npm run lint` 无告警、`npm run test:run` → **91 files / 846 tests passed**（比上一条少 1 条，就是那条被删的已交付断言）、`npm run build`（含 `vue-tsc -b`）通过；`openapi.ts` 与从当前后端重新生成的结果**逐字一致**。

**未跑**：真库检查；浏览器级验收。

## 主代理复核时格外看的一件事（工具正文不能被改掉）

删字段时有个陷阱：那两个工具函数**同时用 `item.excerpt` / `item.truncated` 拼「交给模型的工具正文」**。删得不好的话，模型的正文（也就是会话历史表里存的那一份）会静默变样。复核：`tools/read_document.py` 改成 `body_text = content[:READ_DOCUMENT_MAX_CHARS]` 与 `if len(content) > READ_DOCUMENT_MAX_CHARS`（与原取值完全相同），`tools/search_documents.py` 直接用 `match.page_content.strip()`；并且 **`backend/tests/test_agent_tools.py` 本次未被改动、全绿**（它断言了正文已截断到上限、截断说明「未读取」在）。

## 为什么把它提到 0003/05 之前（记录一下，免得后人以为跳了工单）

工单原文写的是「与 07 改的是同一个函数的相邻几行，顺序上让 07 先落」。**这句本次被反过来了**：回放接口要从 checkpointer 换源到业务表（0003/05），而表里的引用证据按设计**不含正文片段**，契约里却仍必填 `excerpt` —— 不先删掉，回放那条路**构造不出响应里的引用**，而「终态事件与回放引用一致」也不可能成立（实测：表里的引用项过 `DocumentEvidence.model_validate` 报 `('excerpt',) missing`）。本工单自己的被谁阻塞写着「无（可立刻开工）」，所以把它提前。

代价与应对：**07 届时会在同一个函数里改上限值**，所以本工单的改动刻意就近、只做删字段与改拼接数据来源两件事（没有顺手改上限、没有重构那两个函数）。

## 残余风险

- 前端仅组件测试，未做浏览器级验收。
- 引用里不再有正文副本后，想对照「当时引用的那段原文」只能看同一轮卡片下的工具轨迹（那是设计意图：轨迹里的正文比片段更全）。
- 工具正文与证据字段现在各由本地数据供值，两者不再互相验证；如果将来有人只改一边（例如改截断上限却不改工具正文的取值方式），**没有测试能发现不一致**。本次是靠 `test_agent_tools.py` 里对正文的断言兜的。
