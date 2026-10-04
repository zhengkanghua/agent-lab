# 02: 会话历史有了一张表，运行收尾会把它写进去

**要交付什么：** 跑完一次提问，这一轮就落在我们自己的表里——提问、回答、每一个工具调用、每一个工具结果。写入只发生在运行确定不再继续的那一刻（正常答完、用户停止、上游失败、放弃收尾、被别的进程接手后完成都算）；写到一半被排空交接的运行不写，交给接手方收尾时写。

**本组（会话历史落表、模型目录、上下文策略）三份一起部署、升级前清空既有会话，不要单独发布本组。** 理由是这张表必须先存在，上下文策略那次改动才成立——它会把旧轮次的工具正文换成占位，而用户回看读的是这张表。

**被谁阻塞：** 01（要能按运行切出每一组的消息）

**状态：** 已完成（最后一个勾选项当时等另一组提供输入，已在「会话里选模型」那条工单落地后补验）

- [x] 跑一次对话，表里出现组成那一轮的全部行（提问、回答、工具调用、工具结果）
- [x] 同一次收尾执行两遍，表里那些行只有一份
- [x] 重复收尾时，该组的顺序号沿用第一次写入的那一组——否则回看顺序会变
- [x] 排空到可交接边界的那一刻**不写**，接手方收尾时才写
- [x] 写入按运行标识分组；**运行标识的无条件化由上下文策略那一组负责**（那是它对本组提出的唯一要求），本工单只消费它
- [x] 落表前核一遍：每个提问行与助手行都带得上运行标识（带不上就不写，记日志）
- [x] 提问行的运行元数据装着三样：当时的范围快照、是否完整作答、**当时用的模型（含展示名与上下文窗口）**
- [x] 写表发生在**释放会话位之前**；终态事件仍然在释放之后发（那是既有保证，不能反过来）
- [x] 写表失败时只记日志、不中断收尾、不重试，而释放会话位与发终态事件照旧发生

## 验收记录

表 `agent_thread_messages`（迁移 `e9b3c7a41d58`，`down_revision` 指向当时的 head `c4a8f1d6b2e7`），模型 `models/agent_thread_message.py`，行级投影抽成纯函数 `agent/thread_messages.py`，写入方法在 `AgentThreadService.record_run_messages`，调用点在 `AgentRunRegistry._drive` 的 `finally` 里（`drained` 那一支不写）。

| 勾选项 | 观察 |
|---|---|
| 一轮的全部行 | `test_agent_thread_messages.py`：`test_a_run_becomes_question_answer_and_every_tool_row`、`test_every_parallel_tool_call_and_result_gets_its_own_row`（并发调用各占一行、靠 `tool_call_id` 配对而不是顺序）、`test_an_empty_assistant_text_does_not_become_its_own_row`；应用级 `test_a_finished_run_lands_in_the_history_table`（离线图 + 内存 checkpointer + 真实 Service 跑在真实 SQLite 文件上） |
| 收尾两遍只有一份 | `test_writing_the_same_run_twice_keeps_one_copy` |
| 重复收尾沿用原顺序号 | `test_a_repeat_write_keeps_the_sequence_numbers_of_the_first_write`；另 `test_the_sequence_numbers_continue_after_the_last_row_of_the_session`、`test_a_group_already_in_the_table_is_left_untouched`、`test_writing_one_session_leaves_another_session_alone` |
| 排空不写、接手方写 | 在 `test_agent_handover.py` 的接手用例里补了两条断言：排空后 `threads.recorded_run_messages == []`，接手收尾后恰有一次且行形状为 `[question, answer, tool_call, tool_result, answer]`。另在排空超时的放弃用例里断言放弃**也会写**（`completed is False`） |
| 按运行标识分组 | 分组口径来自上一个工单的 `group_messages_by_run`（回放与写入方共用同一份） |
| 运行标识缺失就不写 | `test_a_group_without_a_run_id_is_skipped_and_logged`（跳过并记日志，不编占位 id） |
| 写表先于释放 | **新增** `test_the_history_is_written_before_the_session_slot_is_released`：在写表那一刻读会话行，断言 `active_run_id` 仍是本次运行。**证伪**：把 `runs.py` 里 `_record_run_messages` 与 `_release` 两行对调，立刻红（`assert None == UUID(...)`）；换回后 19 passed |
| 写表失败只记日志 | `test_a_write_failure_only_logs_and_the_run_still_finishes`：终态事件照发、会话位照旧释放（第二次提问拿 200 而不是 409）、按运行标识计数每个运行只记一条日志（不重试） |

**已跑**：`uv run pytest -q` → **944 passed, 76 skipped**；`uv run alembic heads` → 单一 head `e9b3c7a41d58`。

**未跑**：真实 PostgreSQL 上的 `alembic check`；被环境变量门控的集成测试；前端（本工单不碰前端）。

## 为什么还差那一个勾选项（已完成闭环）

「当时用的模型快照」当时在系统里**根本不存在**：它由模型目录那一组的「用户会话选模型」那条工单写进提问消息的 `additional_kwargs["agent_run"]`。本工单把 `run_meta` 做成了**透传**，所以那条工单落地后确认**不需要任何代码改动**。

**闭环证据（在那条工单落地后补）**：`tests/test_agent_thread_messages.py::test_a_finished_run_lands_in_the_history_table` 现在断言真实一次运行写下的提问行里 `run_meta["llm_model"]` 带着 `display_name == "演示模型"`、`context_window == 32768`、`id == DEFAULT_OFFLINE_MODEL_ID`——快照自足（两个值都在里面，回看与接手都不回查目录）。**证伪**：把 `agent/streaming.py` 里冻结模型那一句去掉，该用例立刻红（`KeyError: 'llm_model'`）；还原后全量 1026 passed。

## 本工单施工中的意外与主代理的处置

1. **执行子代理跑满 30 分钟超时**（它已基本完成，最后停在收尾刷测试）。它留下两个调试文件（`backend/dbg.db`、`backend/dbg_tmp.py`），已删。没有重派：剩下的都是可直接核的收尾工作。
2. **它自己的两条新用例是红的**（另有两条勾选项根本没被断言）：一条停止用例的假模型是**真无界**流，用例永远转下去；一条把两次运行各一条的失败日志误断言成「只出现一次」。已修（无界流改成分片有界 + 末尾静默，并写清楚为什么必须这样构造）。
3. **`app_helpers.py` 里的 `RecordedRunMessages` 漏了 `@dataclass`**，于是内存替身每次写表都抛 `TypeError: takes no arguments`——而 `runs.py` 只记日志，所以**静默通过**，没有任何用例变红。已修（补 `@dataclass(frozen=True)`）。这条正是「替身也会骗人」的例子：若不补断言，它会把写入路径整个屏蔽掉。
4. **一个不属于本工单的既有缺陷（已上报老板，老板拍定「现在就修」，已修完）**：`AgentRunRegistry._drive` 只在「一个轮询周期里没有新事件」时才看一眼停止标志（`if not done` 那一支），所以**事件源源不断的流里停止不会被观察到**。实测：一个持续产出的假模型下，`stop_requested` 已置 `True`，而驱动者仍在 `done=True` 分支上转了 1.4 万圈，直到排空超时被放弃收尾。修法是把检查提到循环体开头（每轮都看，且不丢掉已经就绪的事件）；已另行全量回归与证伪，不属于本工单的验收面。
