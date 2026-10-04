# 07: 三个删除出口都连带删这张表

**要交付什么：** 会话被删掉时，它在表里那些行也一起消失。三个出口都要接上：用户删会话、清理旧会话的运维命令、清理孤儿会话的运维命令。

**被谁阻塞：** 02

**状态：** 已完成

- [x] 用户删一个会话之后，表里那一会话的行也没了（与删会话行同一个事务）
- [x] 清理旧会话的运维命令删掉的那些会话，表里的行也跟着没
- [x] 清理孤儿的命令能删掉「**只有表里有行、checkpointer 那边什么都没有**」的残余（候选集从两边取并集）
- [x] 跨会话删除只动自己那一会话的行

## 验收记录

改了什么：`services/agent_thread_service.py` 新增 `list_message_thread_ids()`（列出表里出现过的 thread_id）与 `delete_thread_messages(thread_ids)`（按 thread_id 批量删本表行），并把连删接进 `delete_thread_record()` 与 `delete_threads()`；`cli.py` 的孤儿命令把候选集改成**两边取并集**（`orphans = sorted((stored | recorded) - known)`）并在清完 checkpointer 后按 thread_id 删本表行；`backend/docs/architecture.md` 那张「删除入口清单」三行补齐。

| 勾选项 | 观察 |
|---|---|
| 用户删会话、只动那一会话 | 真库层 `test_agent_thread_messages.py::test_deleting_a_thread_removes_only_that_threads_rows`（删 A 后 A 空、B 逐行未变）；HTTP 层 `test_agent_threads_api.py::test_deleting_a_thread_clears_the_history_the_history_table_and_the_ownership_row`（走真实 `DELETE`，且删前先断言过非空）；语句顺序 `test_agent_thread_service.py::test_deleting_threads_removes_history_rows_before_the_ownership_rows`（先 `DELETE FROM agent_thread_messages`、再 `DELETE FROM agent_threads`，一次 commit） |
| 清理旧会话也跟着没 | `test_cli.py::test_prune_old_threads_deletes_the_history_rows_of_the_sessions_it_removes`（真实 Service + SQLite，只把 checkpointer 两个函数换成替身：`stale_rows == []`、新会话的行不动）；服务层 `test_deleting_old_threads_removes_their_rows_and_spares_the_rest` |
| 孤儿命令能删「只有表里有行」的残余 | `test_cli.py::test_prune_orphan_threads_deletes_a_leftover_that_only_the_history_table_has`（表里 1 行、**无归属行**、checkpointer 空：预演报出 1 个候选且一行未删，`--yes` 后表里读空）；替身层另三条（预演报出并集候选、`--yes` 真删、有归属行的会话永不被当候选） |
| 跨会话隔离 | 上面两条的双会话比对；另加反向边界 `test_deleting_a_thread_that_is_not_yours_keeps_its_rows`（不属于自己时整体回滚，别人的历史一行不少）；HTTP 层把既有那条「删别人的会话不泄露」扩展成同时断言「他的历史表行与删之前一致」 |

**既有行为保住**：孤儿命令**默认预演不删**（预演也把并集算出来的候选报出来），已有用例。

**已跑**：`uv run pytest -q` → **1074 passed, 76 skipped**（比上一条 +13）。

**未跑**：前端（未碰）；真库集成测试（运维命令那两条出口在本工单里用「真 Service + SQLite + checkpointer 函数替身」验，真库行为仍靠部署时的实际命令）。

**证伪（我自己做的）**：把候选集改回只看 checkpointer（`stored - known`）→ 孤儿用例立刻红（1 failed）；还原后 1074 passed。

## 一处需要记下的设计取舍（子代理主动写的）

`delete_thread_record` 里删消息行那一步**不带账号条件**（只按 `thread_id`），靠「删不到归属行就整体回滚」保证跨账号隔离。账目在实现注释与那条「删别人的会话时他的行一行不少」的用例里——如果将来把那个整体回滚改成「删不到就静默跳过」，消息行那一步就会真删别人的。这条是本工单里最容易在将来看丢的耦合。

**残余风险**：孤儿命令先清 checkpointer、再删本表行；后一步失败时那批行会留成残余，但命令是幂等的（再跑一次就能收），所以没加重试。
