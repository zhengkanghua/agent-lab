# 02: 送出去的历史里，旧引用标记被剥掉

**要交付什么：** 送给模型的那一份历史里，旧引用标识被换成一句明说它已失效的文字；本次运行自己的标识一个字不动。模型不会再把上一轮的标识抄进新回答。

**被谁阻塞：** 01（不撤掉裁剪就没有历史可剥）

**状态：** 已完成

- [x] 送给模型的历史回答与历史工具结果里，`[[E…]]` 变成 `[出处已失效]`
- [x] 本次运行自己的工具结果里标识一字不动（那是模型唯一能抄的东西）
- [x] 存档原文不变：回看那一轮时旧引用仍然点得开（既有回放断言仍绿）

## 验收记录

改动：`agent/evidence.py` 新增 `STALE_CITATION_NOTICE = "[出处已失效]"` 与纯函数 `strip_stale_citations(content)`（紧贴 `CITATION_PATTERN`，两种正文形状都处理，**不改入参**）；`agent/middleware.py` 新增 `_without_stale_citations(request)` 并在 `handler(request)` **之前**套上。

| 勾选项 | 观察 |
|---|---|
| 历史被剥、本轮不动 | `test_agent_middleware.py::test_history_citations_are_stripped_but_this_runs_are_not`：断言模型实际收到的最后一份消息里 `旧资料 [出处已失效]`、`旧回答 [出处已失效]` 都在、旧标识一个不剩，**而本次自己的 `本次资料 [[E222222222222]]` 原样还在** |
| 存档不变 | `test_agent_middleware.py::test_stripping_the_sent_copy_leaves_the_checkpoint_text_unchanged`：从 `graph.aget_state` 读回，存档里仍是 `[[E111111111111]]` 且不含失效说明；既有回放断言（`test_agent_replay.py`、`test_agent_evidence_scope.py` 里那些断言引用仍解析得出来的）全部未改且仍绿 |

**证伪（我自己做的）**：把 `model_copy(update=...)` 换成就地改 `message.content` —— 存档那条立刻红，报错原文 `assert '旧资料 [[E111111111111]]' in ['…摘要', '第一问', '', '旧资料 [出处已失效]', '旧回答 [出处已失效]', …]`；而「历史被剥」那条**仍然绿**（两种写法都能剥，差异全在存档）。这正好证明第二条断言不是重复：它就是防「剥离渗进存档」的那一道。还原后 23 passed。

**已跑**：`uv run pytest -q` → **948 passed, 76 skipped**。

**未跑**：前端（未动）；被环境变量门控的集成测试。

**残余风险 / 边界**：
- **摘要模型那次调用不经过这个发送副本**（它在压缩中间件内部自己拼消息、直接调模型），所以它的输入里如果带了旧标识不会被剥。spec 的口径是「送给模型的那一份」且摘要提示词本来就要求不制造可点击的标识；本工单没改它，已在此登记。
- 剥标记与「先清后压」方向相反：前者只改发送副本、后者写回 state。两条分别在两个工单里，不要混。
- `STALE_CITATION_NOTICE` 与 `CITATION_PATTERN` 放在同一处并互相注明约束：哪天把正则放宽到单层，这句说明会连同用户真写下的方括号文字一起被吃掉。
