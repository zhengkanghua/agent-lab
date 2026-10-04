# 03: 压缩改成按模型窗口占比，并且按调用期读窗口

**要交付什么：** 历史压缩按**选中模型的上下文窗口**算——触发取窗口的 80%、压缩后保留 30%；换一个窗口不同的模型，触发点跟着变。窗口在每次模型调用时从本次运行的上下文里取，不烤死在进程启动。

**被谁阻塞：** 01；**模型目录那一组的「选择解析结果进运行上下文」**（窗口的来源就是那条模型条目）

**状态：** 已完成

- [x] 压缩在上下文到窗口的 80% 时触发、压缩后保留 30%——触发与否用「摘要模型被调了几次」观察，不去读中间件内部状态
- [x] 换一个窗口不同的模型，触发点跟着变
- [x] 把客户端对象上那个构造期窗口值换成别的数（比如 1000），同一运行的触发点**不变**；只改运行期该轮的窗口值，触发点才变
- [x] 窗口口径用上游原生的按比例配置，不另造一套
- [x] 三处原本靠「40 条消息」自己构造输入来逼出压缩的用例改完并绿；其中钉住摘要消息形态的那条**不能删**
- [x] 离线假模型都带上窗口信息（不带的话中间件在构造期就抛异常）；**窗口挂在假模型客户端上，不要去动装配助手的签名**——模型目录那一组的 01 要改同一个文件
- [x] 那两个按条数的压缩常量删掉，架构文档里引它们的那句话跟着改

## 验收记录

改了什么：`agent/limits.py`（删两个按条数常量，换成 `SUMMARIZATION_TRIGGER_FRACTION = 0.8` / `SUMMARIZATION_KEEP_FRACTION = 0.3` + `SUMMARIZATION_PLACEHOLDER_WINDOW = 32768`）；`agent/middleware.py`（按协程隔离的 `_CONTEXT_WINDOW` 与 `bind_context_window`/`unbind_context_window`、`RunSafeSummarizationMiddleware._get_profile_limits()` 覆写、`_with_placeholder_window()` 给摘要客户端补占位 `profile`、构造改成 `trigger=("fraction", …)`/`keep=("fraction", …)`）；`agent/streaming.py`（跑图前把当轮窗口绑进上下文变量）；`tests/agent_helpers.py`（假模型带上 `profile`）；四处用例只改**构造**；`backend/docs/architecture.md` 那一句改写。

| 勾选项 | 观察 |
|---|---|
| 80% 触发 / 30% 保留 | `test_agent_middleware.py::test_compression_triggers_at_eighty_percent_of_the_model_window`（窗口 1518 → 触发；1523 → 不触发，翻转点正好在 80% 上）、`::test_compression_keeps_thirty_percent_of_the_model_window`（保留段 ≤ 30% + 一条余量，且再多留一条就超）；都只看摘要模型被调几次 |
| 换窗口则触发点变 | `::test_the_trigger_follows_the_runtime_window_not_the_client_profile`：同一份历史，运行期窗口 200 → 触发；100000 → 不触发 |
| 构造期值无关 | 同一条里两条断言：`client_window` 取 1000 与 32768（差一个数量级）触发次数相同；只改运行期窗口才变 |
| 上游原生比例口径 | `middleware.py` 的 `trigger=("fraction", SUMMARIZATION_TRIGGER_FRACTION)` / `keep=("fraction", SUMMARIZATION_KEEP_FRACTION)`，没有自算 token 再拼 messages 阈值 |
| 用例改构造不改断言 | `test_agent_replay.py`、`test_usage_summarization.py`、`test_agent_middleware.py`、**以及 `test_agent_evidence_scope.py`**（第四处也靠「41 条消息」逼压缩，不改必红）；钉上游摘要消息形态的那条契约测试原样保留 |
| 假模型带窗口 | `tests/agent_helpers.py` 的 `OFFLINE_MODEL_PROFILE = {"max_input_tokens": 32768}`，三个假模型各自声明字段；`build_offline_graph` 签名未动 |
| 常量与文档 | `grep -rn "SUMMARIZATION_TRIGGER_MESSAGES\|SUMMARIZATION_KEEP_MESSAGES" backend/src backend/tests backend/docs docs` **零命中**；`backend/docs/architecture.md:416-420` 已改成按当轮窗口占比触发/保留、并说明窗口按调用期读、占位值只用于构造校验 |

**已跑**：`uv run pytest -q` → **1031 passed, 76 skipped**。

**未跑**：前端（本工单未碰）；真库集成测试。

**残余风险与一条必须写清的边界**：
- **没当轮窗口时的回落是刻意的**：不经过 HTTP 入口的调用点（离线测试直接调中间件、`AgentContext` 里没有模型的那种）没有窗口可跟，`_get_profile_limits()` 就地回落到父类实现（也就是那个占位值）。**不能返回 `None`**——上游会把 fraction 判成「不满足」，于是压缩**静默彻底失效**（不报错、不压缩），比回落坏得多。这条写在了覆写的 docstring 里。
- 当轮窗口靠「跑图前把值放进上下文变量」往下传，而该机制成立靠的是 `astream` 建任务时复制调用方上下文——属于上游行为，所以**新增一条端到端用例把它钉住**（见下）。

## 施工事故与主代理的处置（含一次我自己的误判，必须记）

1. **第一次派发被全盘 `find` 卡死**（子代理写的是 `python -c "..." || find / -name "summarization.py"`，前半段因裸 `python` 失败而触发后者）。按工具种类统计：**15 次读 + 1 次 bash、零写入**，盘上无产出，停掉零损失；随后**重派一个全新子代理**，并把三个坑写死进 brief（不用裸 `python`、不用 `||` 接全盘搜索、不以仓库根/盘符为起点递归搜索），同时直接给出上游源码路径与行号。重派一次完成。
2. **我误判过一次「接线断了」，已全部回退，没有任何生产代码因此改动。** 现象：我写了一条端到端用例、用「摘要模型被调几次」观察，得到 1 次（以为压缩没发生）；又用同样的观察法在调用方任务里先绑定窗口，也得 1 次。据此我误以为「图不继承调用方上下文」，把绑定点从流入口挪进了 `abefore_model`。
   - 真相：**`call_count` 数不到摘要那次调用**——中间件为了补占位窗口会 `model_copy` 一份客户端，摘要调用落在副本上。换成看**效果**（状态头部是不是摘要伪提问、历史是否变短、那条摘要是不是摘要模型真产出的）之后，压缩一直都在发生；**对照实验**把设计换回原写法，同一条用例同样绿。
   - 处置：把改动**全部回退**（`middleware.py` 与 `streaming.py` 回到原子代理实现），只留那条端到端用例；并删掉我基于误判写在注释里的「图不继承调用方上下文」——那是个没有依据的断言，留着会误导后来人。
3. **新增的端到端用例**：`tests/test_agent_middleware.py::test_the_running_window_reaches_the_middleware_through_the_stream_entry`——断言一次真实运行里压缩真的发生了（状态头变成摘要伪提问、历史变短、那条摘要是摘要模型产出的），且窗口取**触发线的一半**（当轮窗口真送到了则阈值远低于历史长度、必触发；回落到占位值 26214 则必不触发），一刀切开两种情况、不卡临界点。**证伪**：把 `stream_agent_events` 里那行绑定去掉，该用例立刻红（`assert None == 'summarization'`）；还原后全量 1031 passed。
