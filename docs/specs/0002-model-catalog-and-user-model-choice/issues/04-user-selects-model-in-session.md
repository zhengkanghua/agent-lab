# 04: 用户在会话里选模型，选择被记住、逐轮可见、失效时如实提示

**要交付什么：** 用户在会话里挑一个模型，这个选择被记住（下次提问不用再选），每一轮的记录都能看出当时用的是哪个；选中的模型失效时如实提示重选。

**选择解析的结果——当轮选定的模型条目，连它的上下文窗口——要放进本次运行的上下文。** 这是本组与上下文策略那一组之间唯一需要说清归属的一处：那边要拿这个窗口算压缩。

**被谁阻塞：** 03（目录里得先有可选的模型）

**状态：** 已完成

- [x] 在会话里选一个模型后，下一次提问沿用同一个
- [x] 请求里临时带上另一个模型，会话行也跟着改
- [x] 运行中改选也照样保存
- [x] 回看时每一轮都显示当时用的那个模型，而且名字是**当时的快照**——条目后来改名或停用，都不影响已经发生过的那几轮
- [x] 选择指向一个不存在的 id → 提问**在开始运行之前**失败并返回 404；生效的那一个已停用（含它所属渠道停用）→ 409；两种情况模型都一次都没被调用
- [x] **只校验当轮生效的那一份**：请求里给了就用请求的，没给才用会话里存的；另一份失效不影响这一轮
- [x] **存一个当前已失效的选择仍然保存成功**——可用性只在运行前那道门判，保存时不判
- [x] 选择器**每次打开时重新拉取目录**，刚建好的模型立刻能选到
- [x] 选中的模型不可用时，界面显示失效提示与重选入口，不静默回落到默认
- [x] 「目录为空或全部停用」与「选中的模型不可用」各有自己的文案
- [x] 「上游说没有这个模型」与「上游认证失败」两条既有文案补上「……请联系管理员，或换一个模型试试」——注意前端那条文案今天是好几个错误码共用的，要拆开

## 验收记录

新增：会话行 `llm_model_id` 列（迁移 `a3f7c2e9b5d4`，可空、逻辑外键、库上无约束）、`services/llm_model_selection_service.py`（解析当轮模型，**直读目录表**）、`schemas/llm_models.py` 的 `ResolvedLlmModel`（快照形状：id + 展示名（已回落）+ 上下文窗口）；提问请求体加 `llm_model_id`；`PATCH /agent/threads/{thread_id}/model` 保存选择；`AgentContext.llm_model`；冻结进提问消息的 `agent_run.llm_model`；接手重建上下文改读 `_read_frozen_run_meta`（**不重判可用性**）；`AgentReplayTurn.llm_model` 逐轮读出；前端 `useChatModel.ts`、`AgentModelPicker.vue`、`AgentTurnCard.vue` 的「本次模型」与两类文案。

| 勾选项 | 观察 |
|---|---|
| 选择被记住 | `test_agent_thread_model_choice.py::test_a_chosen_model_carries_over_to_the_next_question`、`test_a_model_in_the_request_is_written_back_to_the_thread_row`、`test_changing_the_model_while_a_run_is_in_flight_is_saved_anyway` |
| 逐轮可见、名字是快照 | `test_a_turn_keeps_the_name_it_ran_with_when_the_entry_is_later_renamed`；回放幂等断言 `[turn["llm_model"]["display_name"] for turn in history["turns"]]`（`:145`、`:348`）；前端 `AgentTurnCard.vue` 的「本次模型：{{ turn.llmModel.display_name }}」 |
| 404 / 409 都在运行之前、且模型一次没被调 | `test_selecting_a_missing_model_id_is_a_404_before_the_run_starts`、`test_an_unavailable_effective_model_is_a_409_without_calling_the_model`（后者断言假模型的调用次数为 0）；`api/agent_chat.py` 里解析那一步在 `ensure_thread`/流开始**之前** |
| 只校验当轮生效的那一份 | `test_only_the_effective_selection_is_checked` |
| 存失效选择仍然成功 | `test_selecting_an_unavailable_model_is_still_saved` |
| 目录为空不退化成默认 | `test_an_empty_catalog_is_its_own_error_and_does_not_fall_back` |
| 每打开重拉、失效态 | **新增** `frontend/src/features/agent-chat/tests/useChatModel.spec.ts`（6 条）：失效态与「不改动选择」、仍在目录里时不判失效、**目录读不到时不乱下结论**、`refreshCatalog()` 真的重拉且新条目立刻可选、保存失败给复核提示、会话未建立时不发保存 |
| 两条文案各有归属 | `agent-error.ts` 新增 `SELECTED_MODEL_UNAVAILABLE_COPY`（覆盖 404 与 409）与 `NO_AVAILABLE_MODELS_COPY` |
| 两条既有文案补话 + 拆开 | 同文件拆出 `AUTHENTICATION_FAILED_COPY` 与 `MODEL_NOT_FOUND_COPY`，各自带「请联系管理员，或换一个模型试试」，不再共用那句「服务端的模型配置需要维护」 |

**跨组闭环**：提问行冻结的模型快照已用端到端用例钉住（`test_agent_thread_messages.py::test_a_finished_run_lands_in_the_history_table` 断言 `run_meta["llm_model"]` 带展示名与窗口），那条同时关掉了「会话历史 02」的最后一个勾选项。

**已跑**：`uv run pytest -q` → **1026 passed, 76 skipped**；`uv run alembic heads` → 单一 head `a3f7c2e9b5d4`；前端 `npm run lint` 无告警、`npm run test:run` → **91 files / 847 tests passed**、`npm run build`（含 `vue-tsc -b`）通过；`openapi.ts` 与从当前后端重新生成的结果**逐字一致**。

**未跑**：真库 `alembic check`；被门控的集成测试。

## 施工事故与主代理补完的部分

执行子代理**第三次跑满 30 分钟超时**，停在最后一步（正准备重新生成前端类型）。代码基本都写完了且后端当时就是绿的，但前端留下 78 个红用例。我没重派，而是自己收尾：

1. **两处是测试夹子的缺口，不是生产缺陷**：`useAgentChat.spec.ts` 的挂载夹子没给 `QueryClient`（新的选择器用 vue-query，于是每个用例都死在 `injection VUE_QUERY_CLIENT not found`），并且没有打桩目录接口；`AgentChatPage.spec.ts` 的 `agent-threads` 替身少了新导出的 `updateAgentThreadModel`。已修（夹子里装 `VueQueryPlugin` + 新 `QueryClient`、补桩、补齐替身导出）。
2. **一处是 spec 明确要求而本轮缺的覆盖**：前端用例总数在改动前后都是 841，说明新增的 `useChatModel`/选择器只有集成、没有覆盖。而 spec 0002 的「测试决策」点名要求前端覆盖「**选择器的失效态**（不静默回落）」，所以我补了 `useChatModel.spec.ts`。**证伪**：把 `isChoiceUnavailable` 改成永远 `false`，失效态那条立刻红；还原后 6 passed。
3. **关掉「会话历史 02」的最后一个勾选项**：给 `test_agent_thread_messages.py` 补上「真实一次运行的 `run_meta` 里带着模型快照」的端到端断言。**证伪**：去掉 `streaming.py` 里冻结模型那一句，该用例红（`KeyError: 'llm_model'`）；还原后全量 1026 passed。

## 残余风险

- **接手那一轮不重判可用性是刻意的**（spec 要求）：排空期间被停用的模型仍会用完那一轮。要停就直接停整个会话，或让上游自己失败。
- 选择器读的是 `GET /llm-models/available`（直读目录表、不经过客户端缓存），所以刚停用的模型立刻不可选；但界面上的**已存选择**是不是失效，靠「它有没有出现在刚拉到的目录里」——目录读失败时不作判断（宁可不说，也不能把一个好好的模型说成不能用）。
- 前端没有做浏览器级验收（只有组件/组合式函数测试）。
