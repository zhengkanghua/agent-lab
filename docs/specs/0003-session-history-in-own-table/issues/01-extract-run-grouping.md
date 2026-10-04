# 01: 把「按运行分组」抽成一个纯函数，回放与写入方共用

**要交付什么：** 消息按运行分组这件事，从回放里抽出来变成一个独立的纯函数：给它一串消息，它给出每一组的运行标识与该组的原始消息（按顺序）。回放和后面新加的写入方共用这一个，两边不会各切一套。

**为什么先做：** 规格要求写入方复用回放的分轮逻辑，并写明两边「不得各写一套」；但那套分轮今天内嵌在回放函数里，写入方拿不到每组的原始消息（它要 role、顺序号、正文与证据）。不先抽出来，写入方要么复制一套、要么先成型再拼回去——两边就此分叉。

**被谁阻塞：** 无（可立刻开工）

**状态：** 已完成

- [x] 既有回放断言全部仍绿——分轮结果一字不变
- [x] 新函数能直接给出「每一组的运行标识 + 该组的原始消息（按顺序）」，写入方不需要自己再切一次

## 验收记录

改动落在 `backend/src/agent_lab/agent/replay.py`（新增 `RunMessageGroup` 与 `group_messages_by_run`，`build_replay_turns` 改为逐组定型）与
`backend/tests/test_agent_replay.py`（新增一条用例）。

| 勾选项 | 观察 |
|---|---|
| 回放分轮结果不变 | `test_agent_replay.py` 原有 8 条用例（问答对、tool_call_id 配对、失败标记、无结果的调用、无回答的轮、多模态只留文本、首条提问前的模型消息被丢、真实中间件产的摘要被认出）逐条未改且全绿；`build_replay_turns` 的签名与返回三元组未变，`AgentReplayTurn` 字段未变 |
| 新函数给出「运行标识 + 原始消息」 | `test_agent_replay.py::test_group_messages_by_run_gives_run_id_and_raw_messages_in_order`：`assert all(message is expected for message, expected in zip(groups[0].messages, messages[1:5], strict=True))` 用**对象同一性**而不是字段比较，且 `strict=True` 锁住条数；第一组首条就是那条提问消息；摘要那条不在任何一组里；末尾再用 `build_replay_turns` 对照证明两者同一份口径 |

**证伪**：先落测试再写实现时 `ImportError: cannot import name 'group_messages_by_run'`；实现后把分组改回旧口径（提问消息不进组）同一条用例 `1 failed`，`tests\test_agent_replay.py:72 AssertionError`。

**已跑**：`uv run pytest -q tests/test_agent_replay.py tests/test_agent_evidence_scope.py tests/test_agent_streaming.py tests/test_agent_threads_api.py tests/test_agent_runs.py tests/test_agent_handover.py tests/test_agent_middleware.py` → 131 passed。

**未跑 / 残余**：frontend 未动。本次是纯重构，未新增对外字段或接口；摘要消息不属于任何一组这一点保持原样（“摘要行写进表”是后面另一条工单的事）。
