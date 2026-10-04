# 07: 给模型读的正文上限改成随窗口算

**要交付什么：** 一次工具调用能交给模型多少文本，不再是一个与窗口无关的定值，而是按当轮那个模型的窗口算——窗口小就收紧、窗口大就放宽。

**被谁阻塞：** 03（要能拿到当轮窗口）

**状态：** 已完成

- [x] 上限取窗口的 20%、封顶 20000 字符、保底 2000 字符，作用在**一次工具调用交给模型的文本总长**上
- [x] 窗口 32768 时约 6500 字符；窗口 100k 以上按封顶的 20000
- [x] 检索那条也受同一个上限管（今天它只靠「最多几篇 × 每篇几段」间接约束）
- [x] 既有那条按定值断言的工具用例一并改
- [x] **最坏情况仍可能越窗**（一轮里再调一次工具就可能超），那一轮按失败收尾——这是既有的、已接受的边界，本工单不引入新机制去拦它

## 验收记录

改了什么：`agent/limits.py` 新增 `TOOL_OUTPUT_WINDOW_FRACTION = 0.2` / `TOOL_OUTPUT_MAX_CHARS = 20000` / `TOOL_OUTPUT_MIN_CHARS = 2000` / `TOOL_OUTPUT_DEFAULT_CONTEXT_WINDOW = 32768` 与纯函数 `tool_output_char_limit(context_window)`；`READ_DOCUMENT_MAX_CHARS = 6000` **删除**（含 `__all__`，全仓无残留引用）；两个工具都改用它（`read_document.py` 的截断与那句说明里的数字、`search_documents.py` 对整份输出文本的总量上限）；`tests/test_agent_tools.py` 既有那条按定值断言的用例改成按 `tool_output_char_limit(None)`；`backend/docs/architecture.md` 里那处悬空引用同步改掉。

| 勾选项 | 观察 |
|---|---|
| 上限公式与两个端点 | `test_agent_tools.py::test_tool_output_char_limit_follows_the_window` 钉死两端：`1 → 2000`、`9000 → 2000`、`99_999 → 19999`、`100_000 → 20000`、`1_000_000 → 20000` |
| 32768 → 约 6500；100k+ → 20000 | `tool_output_char_limit(32768) == 6553`（用例逐字断言）、`100_000`/`1_000_000` 等于 `TOOL_OUTPUT_MAX_CHARS` |
| 检索那条也受管 | `test_search_tool_output_stays_within_the_window_limit`：造一条 8000 字的命中片段，窗口 32768 时断言 `len(output) <= 6553`、有可见说明（「后续命中片段未提供」）、8000 字那段**不在**输出里；同一片段在窗口 200_000 下**完整在**且无说明——两侧可互相证伪 |
| 既有定值用例一并改 | `test_read_tool_truncates_an_overlong_body_with_a_visible_marker` 改成按 `cap = tool_output_char_limit(None)` 断言（`"正"*cap` 在、`"正"*(cap+1)` 不在、说明里的数字是 `cap`） |
| 不引入新机制拦越窗 | 代码里没有任何「本轮工具预算 / 跨调用总量控制」；本工单只改单次调用的上限（我在复核时核过） |

**已跑**：`uv run pytest -q` → **1055 passed, 76 skipped**（比上一条 +3）。

**未跑**：前端（未碰）；真库集成测试。

**证伪（我自己做的）**：把 `TOOL_OUTPUT_WINDOW_FRACTION` 从 `0.2` 改成 `0.5` → 两条工具用例立刻红；还原后 `tests/test_agent_tools.py` 24 passed。

## 两处判断（均已审定）

1. **没有当轮窗口时按 32768 算**（我拍的）：`TOOL_OUTPUT_DEFAULT_CONTEXT_WINDOW` 就是模型表单里预填的那个保守值，于是上限约 6553，与它替掉的 6000 定值同量级——**保底不为零比「没窗口就不限制」安全得多**。子代理还在注释里主动区分了它与 `SUMMARIZATION_PLACEHOLDER_WINDOW`（数值相同、**不是同一个东西**：那个只过上游构造校验、不参与计算），这个区分是正确的。
2. **检索那条用字符级截断 + 可见说明**（与读全文同一种）：说明长度先从预算里扣掉，所以 `len(输出) <= limit` 恒成立。代价是截断点可能落在半条命中块的中间（最坏情况：引用标识完整但它的元数据被截掉）。我判为可接受：标识仍然是应用分配的、仍然能解析（引用核验靠 id + artifact），而模型看不到的内容不会去引；不值得为此把截断改成按块对齐。

**残余风险**：单次上限只是「一次调用」的尺度，一轮里多调几次工具累起来仍可能越窗（spec 明说这是既有的、已接受的边界，本工单刻意不去拦）。
