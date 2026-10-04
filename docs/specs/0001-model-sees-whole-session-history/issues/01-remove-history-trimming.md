# 01: 撤掉历史裁剪：模型每次调用收到整份上下文记录

**要交付什么：** 模型不再只看得见用户问过什么——历史回答、历史工具结果、历史摘要，全部进上下文。同时系统提示词那句不再自相矛盾，而每次运行的消息上都稳定带着运行标识（会话历史那张表要用它切分）。

**本组与另外两组一起部署**（模型目录、会话历史落表）。

**被谁阻塞：** 无（可立刻开工）

**状态：** 已完成

- [x] 历史回答、历史工具结果、历史摘要都出现在模型收到的消息里（今天只有提问进得去）
- [x] 系统提示词说明「能看到完整往来，但历史里的回答、摘要与引用都不是本次事实的依据，本次事实性结论必须来自本次工具返回的内容并原样引用本次标识」，**而且历史里标着 `[出处已失效]` 的地方不要复用**
- [x] 上面那半句在**没有知识库范围的运行里也出现**——今天它嵌在「有范围」那一段里
- [x] 没有知识库范围的运行，提问消息与助手消息上都带上运行标识，助手消息上还带着「这一轮答完没有」的完成态标记
- [x] **摘要中间件那道「运行中不压缩」的守卫也去掉按范围判断的条件**：今天没有范围时守卫整段跳过，运行中途也可能压缩。观察：没有知识库范围的会话，一轮进行到工具调用之后，压缩不再触发（用摘要模型被调几次看）
- [x] 那两条既有断言（「旧秘密不进模型输入」「近期工具结果不进模型输入」）改成断言它们**确实进去了**，不是删掉

## 验收记录

改动落在 `backend/src/agent_lab/agent/middleware.py` 与 `backend/src/agent_lab/agent/streaming.py`，测试落在
`tests/test_agent_middleware.py` 与 `tests/test_agent_evidence_scope.py`。

| 勾选项 | 观察 |
|---|---|
| 三类历史内容进模型输入 | `test_agent_evidence_scope.py::test_scope_switch_keeps_old_answers_tools_and_summary_in_model_input` 断言 `摘要中的旧秘密`／`旧工具秘密`／`旧回答秘密` 都 `in received`；`test_real_summarization_keeps_recent_long_turn_whole_and_current_evidence_valid` 断言 `any("近期资料" in message.text ...)` |
| 提示词重写 | `middleware.py` 的 `select_system_prompt` 把「历史内容不是本次证据」整段移出范围分支、无条件追加；断言见 `test_agent_middleware.py::test_history_is_not_evidence_rule_is_injected_with_and_without_a_scope`（并反向断言旧句 `历史问题只帮助理解意图` 已不存在） |
| 无范围也出现 | 同上测试对 `AgentContext()`（scope 为 `None`）逐句断言；同时反向断言 `应用规定的资料边界` 仍只在有范围时出现 |
| 无范围仍盖运行标识 | `test_agent_middleware.py::test_every_run_stamps_its_identity_even_without_a_scope`：从 checkpoint 读回，提问行 `agent_run == {"run_id": ...}`、助手行 `== {"run_id": ..., "completed": True}` |
| 运行中不压缩的守卫 | `test_agent_middleware.py::test_summarization_waits_for_the_next_question_even_without_a_scope`：无范围上下文下，收尾是本次工具结果时 `abefore_model` 返回 `None` 且摘要模型调用数 0；换成新提问才压缩，整条用例 `summarizer.call_count == 1` |
| 两条既有断言改成断言进去 | `test_agent_evidence_scope.py:247`（由 `not in` 改 `in`）与 `:280`（由 `not in` 改 `any(... in ...)`） |

**证伪**：把 `middleware.py`／`streaming.py` 还原到改动前、测试文件保持新版，`uv run pytest -q tests/test_agent_middleware.py -k "stamps_its_identity or summarization_waits or history_is_not_evidence"`
得 `3 failed`，`tests/test_agent_evidence_scope.py` 的两条得 `2 failed`——新断言确实钉在本次改动上。还原后复跑全绿。

**已跑**：`uv run pytest -q tests/test_agent_middleware.py tests/test_agent_evidence_scope.py tests/test_agent_replay.py tests/test_agent_streaming.py` → 85 passed；
`uv run pytest -q tests/test_agent_chat_api.py tests/test_agent_threads_api.py tests/test_agent_runs.py tests/test_agent_handover.py tests/test_agent_usage_recording.py tests/test_agent_tools.py tests/test_agent_thread_service.py tests/test_agent_thread_ownership_integration.py` → 140 passed, 4 skipped；证伪实验还原代码后又复跑 `tests/test_agent_middleware.py` → 21 passed。

**未跑 / 残余**：frontend 未动，故未跑前端检查。`test_agent_middleware.py` 里 `test_prompt_selection_covers_every_context_shape` 由「整体相等」改为
`startswith`（末尾多了一段无条件规则），对新追加段的断言由新用例单独承担——这是本次唯一被放宽的既有断言，已在此注明。

