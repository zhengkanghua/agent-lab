# 05: 会话真的用选中的模型回答（四个模型环境变量在此退休）

**要交付什么：** 选中的模型确实被用来回答；历史压缩按**选中模型**的上下文窗口算——换一个窗口不同的模型，压缩的触发点跟着变。接入类型、地址、凭据、模型名不再从环境变量读，模型全部来自目录。

**被谁阻塞：** 04（要有当轮选定的模型条目进上下文）；以及上下文策略那一组的两条——「窗口按调用期读取」与「压缩改成按窗口占比」。第二条是必须的：本工单那条「换窗口小的模型、压缩触发点前移」的验收，今天不可能满足，因为压缩现在是按消息条数触发的，口径本身属于那一组。

**状态：** 已完成

- [x] 同一次提问，选不同模型时，实际请求打到不同的上游与模型名
- [x] 账本里那次调用的模型名就是选中的上游模型名，而且每条调用恰好一条记录
- [x] 换一个窗口小得多的模型，压缩的触发点跟着前移（用「摘要模型被调了几次」观察）
- [x] 一条在途运行被排空、由别的进程接手时，仍用原来那个模型跑完
- [x] 从**代码里**删掉接入类型、地址、凭据、模型名这四项配置，且没有任何代码再读它们；对话照常可用
- [x] 接入类型决定用哪个客户端类（用客户端构造参数的断言确认）

## 验收记录

改了什么：新增 `agent/model_resolution.py`（`CatalogModelResolver`：按 id 读目录表那一行、解凭据、按接入类型构造客户端；解析包装 `build_run_model(...)`：装配期不读环境变量/不发请求/不连库，每次调用从 `langgraph.runtime.get_runtime().context.llm_model` 取当轮条目并转交）；`agent/chat_model.py` 改成**按目录里一行渠道**构造（接入类型决定客户端类）；`agent/runtime.py` 把层次写成 `wrap_with_usage_recording(解析包装)`；`config/llm.py` 删掉四个字段（保留温度/超时/UA/checkpoint 池大小）；`backend/.env.example` 删四个变量；`backend/README.md` 那段**假的**启动期自检说明删掉。

| 勾选项 | 观察 |
|---|---|
| 选不同模型→不同上游与模型名 | `test_agent_chat_model.py::test_two_channels_build_clients_with_their_own_address_and_model`（两行渠道 → 客户端类不同、名字与地址各自对上、温度仍共用）；`test_agent_model_resolution.py::test_each_run_resolves_the_client_of_the_model_in_its_context`（两次运行选不同 id → 只有选中的那个被调用）；`::test_the_resolver_builds_each_client_from_its_own_channel_row`（真 `CatalogModelResolver` + 真凭据解密 → `ChatOpenAI`/`ChatOllama` 且地址与凭据各自对上） |
| 账本记上游模型名、每调用恰好一条 | `::test_the_ledger_records_the_upstream_name_of_the_chosen_model`：**展示名与上游名刻意不同**，断言 `[record.model_name …] == ["alpha","beta"]`（列表相等同时排除重复包采集）；`::test_a_smaller_model_window_moves_the_compression_trigger_earlier` 把**摘要那次调用**也算进去：断言 `(2,2)`（摘要+回答）与 `(1,1)`（只回答）——这是「摘要那次被记两条」唯一能被观察到的地方 |
| 换小窗口→触发点前移 | 同上：同一条会话、同一段历史，窗口取 `tokens×1` 时摘要被调 1 次、取 `tokens×10` 时 0 次；两个假模型自己的 `profile` 都是 32768，所以差别只可能来自**当轮快照的 `context_window`** |
| 接手仍用原来那个模型 | `test_agent_handover.py` 那几条仍绿；接力跳重走的是冻结快照里的模型 |
| 接入类型决定客户端类 | `test_the_access_type_decides_which_client_class_is_built`（`ChatOpenAI` vs `ChatOllama`，且各自的字段位置不同） |
| 四个字段删干净 | `grep -rn "LLM_PROVIDER\|LLM_BASE_URL\|LLM_API_KEY\|LLM_MODEL\b" src/ .env.example` 只剩**一条**命中，而且它是一条带明确标注的历史事故注释（「那项环境变量今天已退休，模型改由目录配」）——保留那段是为了不把「为什么需要这道守卫」的故事也删掉 |

**已跑**：`uv run pytest -q` → **1092 passed, 76 skipped**（比上一条 +14）。

**未跑**：前端（未碰）；真库集成测试。

**证伪（我自己做的）**：把装配处改成**重复包一层采集**（`wrap_with_usage_recording(wrap_with_usage_recording(…))`）→ 账本用例立刻红，报错原文 `assert ['alpha','alpha','beta','beta'] == ['alpha','beta']`（Left contains 2 more items），即「摘要那次调用被记两条」；还原后 1092 passed。

## 三处需要记下的判断

1. **`_get_ls_params()` 在「一次调用还没解析过客户端」时返回空字典**（而不是抛错）：摘要中间件会在「这一轮还没开始调模型」时读一次它（上游用 `ls_provider` 比对上一轮自报的用量是否已超线）。**代价**：那条「按上一轮自报用量提前压缩」的路径在我们这里不成立，压缩一律按窗口占比触发。已写进代码注释。
2. **可观测属性逐项委托**：除 `model_name`/`model`/`profile`/`_get_ls_params` 外，还委托了 `default_headers`/`request_timeout`/`temperature`/`max_retries`/`model_kwargs`/`use_responses_api`/`client_kwargs`。委托用的是一个内部上下文变量（不是在包装上拄字段），所以并发会话不会串台。
3. **解析不出来时的失败形态**：新增 `RunModelUnresolvedError`（`RuntimeError`），**刻意不新开面向用户的文案**：它只在两种不变量被破坏时才出现（运行上下文里没有模型；或目录里已经没有当轮那个 id——而本表只有停用、没有删除）。它落到 `agent_internal_error` 兜底（500）并由流层日志指出是哪一次运行。**能给用户可照做的话的是「开始运行之前解析」那道门（已落地）**，不是这条内部路径。

**残余风险**：本工单**没有缓存**——每次解析都查库并新建客户端（功能对、只是慢），缓存是下一条工单；解析包装读目录表那一步**不判可用性**（刻意的，接手一条在途运行要能用已停用的模型跑完）。
