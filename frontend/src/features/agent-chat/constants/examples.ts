/**
 * Agent 对话的示例问题，在空态时引导用户使用。
 *
 * 这些示例展示了 Agent 的三种典型用法：
 * 1. 按主题检索最新报道
 * 2. 核对资料中的明确规定
 * 3. 跨知识库比较资料
 */
export const AGENT_EXAMPLES = [
  '最近有哪些关于利率的报道？',
  '资料中规定的备份保留期限是多久？',
  '比较所选知识库中的方案，哪些结论有冲突？',
] as const

export type AgentExample = (typeof AGENT_EXAMPLES)[number]
