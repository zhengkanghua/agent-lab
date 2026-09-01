/**
 * Agent 对话的示例问题，在空态时引导用户使用。
 *
 * 这些示例展示了 Agent 的三种典型用法：
 * 1. 按主题检索最新报道
 * 2. 按时间范围总结要点
 * 3. 多来源对比分析
 */
export const AGENT_EXAMPLES = [
  '最近有哪些关于利率的报道？',
  '总结一下本周的科技新闻要点',
  '关于新能源汽车，各家来源的说法有分歧吗？',
] as const

export type AgentExample = (typeof AGENT_EXAMPLES)[number]
