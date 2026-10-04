# 04: 先清后压

**要交付什么：** 压缩触发之后、调摘要模型之前，先把较旧的工具正文清一遍——清够了就不必花那次压缩调用、也不必把整段问答折成摘要。被清的是**模型那一侧**看到的旧工具正文，本轮自己刚取到的证据一个字不动。

**本组与另外两组一起部署。** 会话历史那张表不在，被清掉的轮次在界面上就只剩余占位文字。

**被谁阻塞：** 03

**状态：** 已完成

- [x] 只清「本次提问之前」的那些工具结果，**本轮自己取到的证据一字不动**
- [x] 从最旧的开始清；越新的越晚清
- [x] 每条正文只保留头 512 字符与尾 256 字符，中间换成占位文字（这个串会被用户看到，钉一次）
- [x] 正文长度不超过头尾之和时原样保留，不清理（工具失败时返回的几十字安全文案属于这一类）
- [x] 清够（计量降到触发线以下）时**不调摘要模型**，而模型随后收到的旧工具正文已经是占位文字
- [x] 工具调用与工具结果仍然成对
- [x] 压缩切点仍落在一轮的提问处；切点按**清理之后**的计量算
- [x] 清理只动工具消息正文，引用证据原样留着（回放的引用核验照旧通过）
- [x] 这一整套放在**既有的那个摘要中间件**里，图的入口节点名没有变（不得新增带 before_model 的中间件）

## 验收记录

改了什么：`agent/limits.py` 新增三个常量（`PRUNED_TOOL_RESULT_HEAD_CHARS = 512`、`PRUNED_TOOL_RESULT_TAIL_CHARS = 256`、`PRUNED_TOOL_RESULT_PLACEHOLDER = "[... tool result middle pruned ...]"`，位置 `:49-51`）；`agent/middleware.py` 新增 `_pruned_tool_content()`（短于头+尾之和返回 `None` = 原样保留）与 `_prune_old_tool_bodies()`，并把 `RunSafeSummarizationMiddleware.abefore_model` 重构成**先清 → 再计量 → 最后才决定要不要摘要**。

| 勾选项 | 观察 |
|---|---|
| 只清历史、本轮一字不动 | `test_agent_middleware.py::test_the_current_run_tool_results_are_never_cleared`：状态里同时有历史长正文与本轮刚取到的长正文，断言本轮那条**逐字相等**、历史那条已换占位。**证伪**：把运行中守卫短路后变红 |
| 从最旧开始、越新越晚清 | `test_clearing_goes_from_the_oldest_and_stops_once_it_is_enough`：两条都够长，只有旧的那条被换。**证伪**：把循环改成 `reversed(range(boundary))` 后变红 |
| 头 512 + 占位串 + 尾 256 | 用例的期望值由一个**刻意不引用常量**的 `pruned_body()` 给出（把 `512`、占位串、`256` 写成字面量，逐字符相等）——常量被顺手改掉时必红；占位串因此被钉住 |
| 短正文原样保留 | `test_short_tool_bodies_are_left_exactly_as_they_were`：几十字的工具失败安全文案逐字不变、没被拼长 |
| 清够就不调摘要（两条同时断言） | 直调接缝 `test_clearing_old_tool_bodies_is_enough_to_skip_the_summarization_call`：`summarizer.call_count == 0`、`result is not None`（清理确实写回了 state，不是当成没发生）、占位文字已在、且重新计量 < 触发线；开头还断言「清之前确实越线」防空转。端到端接缝 `test_the_model_receives_the_cleared_body_and_the_citation_still_resolves`：真图里**模型收到的最后一份消息里就是占位文字**，且这一轮没有摘要伪提问。**证伪**：把「清够了」那一支改成 `return None`，两条同时红 |
| 工具调用与结果成对 | 端到端用例断言调用消息在结果之前且 `tool_call_id` 对得上；直调用例断言重建后消息**类型序列与原来一致**（一条不少、顺序不变，只换了 content） |
| 切点按清理后计量 | `test_clearing_not_enough_still_summarizes_from_the_cleared_measurement`；既有 `test_agent_evidence_scope.py::test_real_summarization_keeps_recent_long_turn_whole_and_current_evidence_valid` 仍绿（切点仍落在提问处） |
| 引用证据原样 | 清理只 `model_copy` 换 content，`artifact` 不动；含引用的回放断言仍绿 |
| 入口节点名未变 | **我实测的**：`build_offline_graph(...).get_graph().nodes` 里带 `before_model` 的只有 `RunSafeSummarizationMiddleware.before_model`（在它之前没有新的同类中间件），与 spec 里记录的那个节点名一致 |

**已跑**：`uv run pytest -q` → **1050 passed, 76 skipped**（比上一条 +6）。

**未跑**：前端（本工单未碰）；真库集成测试。

## 一条值得记的障眼法（子代理自己也撞上了，并处理对了）

端到端接缝上**数不到摘要调用次数**：中间件为了补占位窗口会 `model_copy` 一份客户端，摘要那次调用落在副本上。子代理的处理是两条接缝合起来用——「一次都没被调」的**确切计数**由直调那条给，「占位文字真的送到了模型」这个**效果**由端到端那条给。这与我之前在压缩窗口那条工单上踩的是同一个坑（计数类观察在有对象副本的链路里会骗人）。

## 残余风险

- 只覆写了异步的 `abefore_model`（与改动前一致）：走**同步** `before_model` 的调用点会压缩但**不会先清**。本项目的图一律用 `astream`，所以这是个理论边界，但记在这里。
- 占位文字会被用户看到（它落在 checkpoint 里，而同一条工具正文在**会话历史表**里是原样完整的）——这正是 spec 要的「模型那侧清、用户那侧不变」，但两侧不一致是预期代价，不是缺陷。
