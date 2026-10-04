# 06: 界面上那条分界线与两处文案

**要交付什么：** 时间线上一条分界线加一句实话，说的是**模型那一侧**保留了什么；同时改掉那句已经被本次改动变成假的错文案。

**被谁阻塞：** 05

**状态：** 已完成

- [x] 表里有带标记的摘要行时，界面上在对应的那一轮之后画出分界线，并显示那句实话
- [x] 表里没有摘要行时，不画线、也不出现那句话
- [x] 「较早消息已压缩，原始问答不再提供回看」这句错文案消失

## 验收记录

改了什么：`model/conversation.ts`（`AgentTurn` 加可选字段 `isMemoryBoundary`；`turnsFromReplay` 加第三个参数 `memoryBoundaryRunId`，**只有 `run_id` 精确对上才标**）；`composables/useThreadHistory.ts`（**两个渲染入口**都把 `memory_boundary_run_id` 传下去：打开/同步会话那条、以及轮询结束按回放渲染那条）；`components/AgentTranscript.vue`（那句实话的常量 + 在带标记那一轮之后追加一个 `<p class="memory-boundary">`，线与文案**同一个元素、同一个 `v-if`** + 它的样式）；测试 `AgentTranscript.spec.ts`、`conversation.spec.ts`、`useThreadHistory.spec.ts`。

| 勾选项 | 观察 |
|---|---|
| 有标记时线画在对应那一轮之后 + 那句话 | `AgentTranscript.spec.ts`「有分界标记时，线画在对应那一轮之后，那句话也在」：取 `.turn-list` 的元素子节点，先找到含「第二问」的那一轮，再断言分界线节点是它的**下一个兄弟**（用元素相对关系而不是写死下标），并断言它不是列表头也不是末尾；`.memory-boundary` 的文本**逐字**等于那句实话；实测 DOM 为 `[第一问, 第二问, P.memory-boundary, 第三问]` |
| 没标记时不画线也不出话 | `AgentTranscript.spec.ts`「没有分界标记时，线和那句话都不出现」（`.memory-boundary` 不存在、文本不含「模型只保留了摘要」）；数据层 `conversation.spec.ts`「没有分界标记、或标记对不上任何一轮时，一轮都不标」；`useThreadHistory.spec.ts` 另有一条证明字段真的从响应落到了对应那一轮 |
| 旧错文案消失 | **起步点已为真**（上一批工单删 `summarized`/`summary` 时一起摘掉的）：`grep -rF "较早消息已压缩" frontend backend README.md CONTEXT.md` 只剩 spec 与工单两个受保护文件，源码与测试零命中。子代理如实报为「不是本工单改的」，**没有为凑验收伪造一处改动** |

**已跑**：前端 `npm run lint` 无告警、`npm run test:run` → **91 files / 850 tests passed**、`npm run build`（含 `vue-tsc -b`）通过；后端**未被本工单动过**（纯前端）。

**未跑**：浏览器级验收（只有组件测试）。

**主代理拍的一条判断（已写进代码注释）**：**标记对不上任何一轮时不画线、也不显示那句话**。那一轮的提问行可能因为强杀或被清空而不在表里；宁可不画，也不能把线画在一个猜出来的位置上——那会让用户以为「线以上模型还记得」，而事实未知。

**残余风险**：那句实话的后半句提到「较早的工具原文可能已被清理成占位文字」，而那个机制是**下一条工单**（先清后压）才会落地。三份改动同一次部署，所以用户看到它时那句话已经为真；但若那条工单被放弃，这句话就成了夸大——已在此登记。
