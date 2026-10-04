# 06: 摘要消息记下两样：分界标记与产生它的运行标识

**要交付什么：** 摘要那条消息上多两个字段——「压到哪一轮之前」，以及「它是哪次运行产生的」。前者是界面上那条分界线的唯一来源，后者让下游能认出「这一行是不是这次产生的」。

**被谁阻塞：** 03、**04**（两者改的是同一个方法：04 要在触发之后、调摘要模型之前插清理并重新计量，06 要往摘要消息上贴字段。**顺序上 04 先落**，否则 06 要白改一遍）

**状态：** 已完成

- [x] 摘要消息上带着「被折掉那一段里最后一次提问的运行标识」
- [x] 那一段里一条提问都没有时（第二次及以后的压缩），沿用上一条摘要的标记
- [x] 摘要消息上还带着「产生它的那次运行」的标识

## 验收记录

改了什么：`agent/middleware.py` 新增 `_CompressionCall`（冻结 dataclass：本次压缩的输入 + 产生它的运行 id）、按协程隔离的 `_SUMMARY_BOUNDARY` 与 `_bind_summary_boundary`/`_unbind_summary_boundary`；`abefore_model` 在调父类前 bind、`finally` 里 unbind；**`_build_new_messages` 覆写成普通实例方法**（上游调用处是 `self._build_new_messages(summary)`，所以能读到上下文变量），它**原样复用父类造出来的那条消息**、只往 `additional_kwargs` 里加两个键；`_memory_boundary_run_id()` 从被折掉那一段往前找最后一次提问的运行标识、找不到则沿用上一条摘要的值。

**两个字段名（与「摘要行写进会话历史表」那件的契约）**：`memory_boundary_run_id`（被折掉那一段里最后一次提问的 run_id）、`produced_by_run_id`（产生这条摘要的那一次运行）。两者与既有的 `lc_source` 并列写在 `additional_kwargs` 里；状态里那条摘要消息的正文与 `lc_source` **一个字未动**。

| 勾选项 | 观察 |
|---|---|
| 记最后一次提问的运行标识 | `test_agent_middleware.py::test_the_summary_records_the_last_question_of_the_dropped_segment`（段里埋多个提问，断言取到的是**最后**那一个） |
| 无提问时沿用上一条 | `::test_a_second_compression_without_a_question_inherits_the_previous_boundary`（专门造两次压缩的序列） |
| 记产生它的运行 | `::test_the_summary_records_the_run_that_produced_it` |

**已跑**：`uv run pytest -q` → **1058 passed, 76 skipped**（比上一条 +3）；既有的摘要消息形态契约测试仍绿。

**未跑**：前端（未碰）；真库集成测试。

**证伪（我自己做的）**：把「无提问时沿用上一条摘要」那一支换成直接 `return None` → 用例立刻红，报错原文 `AssertionError: 没有提问时沿用上一条摘要的分界，不能记空` / `assert None == '1917fa53-…'`；还原后 1058 passed。

## 施工事故（第三次同类卡死）

子代理在**实现与测试都已落盘且绿**（`tests/test_agent_middleware.py` 40 passed）之后，卡在一条自己写的「准备备份」命令上：`python - <<'EOF' 2>/dev/null || true`（**空 heredoc + 裸 `python`**）。又是「裸 `python` + 多行 heredoc」这一类；前两次分别是 `grep -rn` 全仓与 `||` 接全盘 `find`。处置：interrupt 停掉（工作已落盘、零损失），验证与证伪我自己做完。

## 残余风险与一处需要下游知道的边界

- **两个字段在极少数路径上可能为 `None`**：不经过运行上下文（同步 `before_model`、直接调中间件）**且**没有上一条摘要可沿用时。本项目图一律走异步 `astream`，所以生产路径上取得到；但**下游（会话历史表那条工单）要能容忍 `None`**，不能假定它一定有值。这条子代理写进了 `_build_new_messages` 的 docstring。
- **分界标记是「重算切点」得来的**：`_build_new_messages` 只收得到摘要正文，所以 `_memory_boundary_run_id` 自己调一次 `_determine_cutoff_index`——用的是**同一个纯函数、同一份输入**，所以与父类这次切的是同一段。这是一处耦合（将来若有人改了切点逻辑的**有状态**性，两边会分叉）；已在方法 docstring 里写明。
- 本中间件**不写会话历史表**：贴字段与落表是两件事，后者是另一条工单。

（这条工单本身没有更靠外的层可贯穿，它的下游是会话历史落表那组的「摘要行也写进表」。）
