# 05: 回放改从这张表读，前端同时停止消费那两个今天用的字段

**要交付什么：** 用户回看读的记录换了来源——从 checkpointer 换到我们自己的表。**前端要跟着一起改**：它今天依赖响应里「是否压缩过」与「摘要正文」两个字段（校验、状态、界面上的提示条与折叠块），这几个依赖要在同一天摘掉，否则字段一删会话就打不开。

**被谁阻塞：** 02

**状态：** 已完成（本工单曾被「去掉证据正文副本」那条工单结构性地卡住，见下）

- [x] 拿一个「checkpoint 里什么都没有、表里有行」的会话去回放，仍然显示得出那一轮（用来证伪「还在偷偷读 checkpointer」）
- [x] **既有那批**回放断言换数据源之后仍绿（不是新写一批「与今天一致」的断言——那种在起步点就已经为真）
- [x] 前端不再需要那两个字段：校验、状态与界面上的提示条、折叠块一起摘掉，会话照常打开
- [x] 表里没有对应行时，回放返回空轮次而不是报错
- [x] 分界标记取会话里顺序号最大的那条摘要行（**用直接插进表的两行摘要来测**）
- [x] 每一轮多出「当时用的模型（含展示名）」——这条依赖模型目录那一组
- [x] 引用里少了正文片段字段——这条依赖上下文策略那一组去掉那两个字段
- [x] 终态事件与接手重建上下文继续读 checkpointer，而且**拿同一个会话比对：终态事件给出的答案、完成状态、引用，与刷新后回放给出的完全一致**

## 验收记录

新增：`agent/replay_rows.py`（从表里的行装配轮次的纯函数，与 `replay.py` 那套「从消息装配」共用同一批引用核验与完成态判定）、`tests/test_agent_replay_rows.py`；`api/agent_threads.py` 的回放路由改成只依赖 `AgentThreadService` 与模型选择 Service（**不再接 `AgentRuntime`**）；`schemas/agent_thread.py` 删 `summarized`/`summary`、加 `memory_boundary_run_id`；前端 `api/agent-threads.ts`、`features/agent-chat/composables/useThreadHistory.ts`、`model/conversation.ts` 与相关组件摘掉那两个字段的依赖。

| 勾选项 | 观察 |
|---|---|
| checkpoint 空、表里有行仍能回放 | `test_agent_threads_api.py::test_replay_reads_the_history_table_not_the_checkpointer`：第二条 lifespan 用**只共享会话数据、全新空 checkpointer** 的实例，并显式断言 `(checkpoint_values or {}).get("messages") in (None, [])`——达不到这个前提它就报「证明不了数据源换了」；同一用例断言两轮问答原样读出 |
| 既有回放断言仍绿 | `test_replay_returns_the_turns_that_were_actually_stored`、`test_agent_runs.py` 停止/失败/断流那几条、`test_agent_handover.py` 接手那条、`test_agent_thread_model_choice.py` 逐轮模型、`test_agent_evidence_scope.py` 逐轮范围断言全部绿 |
| 前端摘字段、会话照常打开 | `grep -rn "isHistoryTruncated\|historySummary\|\.summarized\|replay.summary" frontend/src`（排除生成物）**零命中**；相关四个 spec 与 `npm run build`（含 `vue-tsc -b`）全绿 |
| 无行时回空轮次 | `test_agent_threads_api.py::test_replay_of_a_thread_with_no_stored_history_is_an_empty_turn_list`（`200` + `turns == []` + 标记为 null）；纯函数层 `test_agent_replay_rows.py::test_no_rows_gives_no_turns_and_no_boundary` |
| 分界标记取 seq 最大那行 | HTTP 层 `test_the_boundary_marker_comes_from_the_newest_summary_row`：**直插表**两行摘要（`seq=0` 与 `seq=9`），断取后者、且摘要行自己不成为一轮；纯函数层 `test_a_summary_row_only_carries_the_boundary_marker` 同样两行 |
| 逐轮模型（含展示名） | 读取路径从提问行 `run_meta["llm_model"]` 取；纯函数断言 `turns[0].llm_model` 的展示名与窗口；HTTP 层既有断言 `test_agent_thread_model_choice.py` 的 `[turn["llm_model"]["display_name"] …]` |
| 引用里少了正文片段 | 由前置工单（去掉证据正文副本）落地；本工单那批含引用的回放断言全绿 |
| 终态与回放一致 | 两者共用同一批纯函数（同一套分轮、完成态判定、引用核验）；终态事件与接手仍读 checkpointer，**读取来源未改** |

**已跑**：`uv run pytest -q` → **1044 passed, 76 skipped**；`uv run alembic heads` → 单一 head（本工单无迁移）；前端 `npm run lint` 无告警、`npm run test:run` → **91 files / 844 tests passed**、`npm run build` 通过；`openapi.ts` 与从当前后端重新生成的结果**逐字一致**，差异正是删 `summarized`/`summary`、加 `memory_boundary_run_id`。

**未跑**：真库检查；浏览器级验收。

## 两处既有断言的语义被改了（必须记，因为它们是本次行为变化的证据）

1. **在途那一轮不再出现在 `turns` 里**（`test_agent_runs.py::test_the_replay_response_reports_the_run_in_flight` 改成断言 `during["turns"] == []`，而 `active_run_id` 照旧带出）。这是「写入只发生在运行收尾」的直接后果，spec 也写明「运行期间不写，界面显示『正在生成』」。**用户可见的变化**：生成中刷新页面不再能看到那半截回答（只剩「正在生成」）；spec 的「超出范围」已明确把「运行中增量写、刷新续看半截回答」单独立项。用例 docstring 里把这个理由与「为什么仍然必须带 `active_run_id`」写清楚了。
2. **接手那两条用例改成直接读 checkpointer**（新增 `_turns_from_checkpointer` helper，走的仍是同一套 `build_replay_turns`）。理由：排空那一刻会话历史表里还没有这一行（写入由接手方在最终收尾时做），拿回放当「看 checkpoint 的窗口」已经不成立。

## 本工单曾被结构性地卡住一次（已解，记录如下）

第一次派发时子代理停下来报：写入侧按设计把引用证据里的 `excerpt`/`truncated` 投影掉了，而响应契约仍必填 `excerpt`，于是回放改成读表之后**根本构造不出响应里的引用**（实测：表里的引用项过 `DocumentEvidence.model_validate` 报 `('excerpt',) missing`），而「终态与回放引用一致」在两边契约不同时不可能成立。它**零改动**停手交报告，处置是把「去掉证据正文副本」那条工单（原排在第 16）提到本工单之前做，然后重派本工单。这条边不在原定顺序里，已上报老板。

## 残余风险

- **`memory_boundary_run_id` 已经暴露，但前端还没消费它**——画那条分界线与两处文案是下一条工单的验收面，这里故意不提前做。
- 生成中刷新不再看得到半截回答（见上），以及由此带来的前端状态区分：**「还没回答」与「还在写」从 `turns` 上分不出来**，只能靠 `active_run_id`；这条依赖已写在用例 docstring 里。
- 前端仅组件测试，未做浏览器级验收。
