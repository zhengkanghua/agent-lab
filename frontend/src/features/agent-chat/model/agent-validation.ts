import { MAX_SYSTEM_PROMPT_CHARACTERS } from '@/api/agent-chat'

/**
 * Agent 对话输入的共享约束。纯常量与纯函数，不做任何网络请求。
 *
 * MAX_MESSAGE_CHARACTERS 与后端 `agent/limits.py` 的 MAX_USER_MESSAGE_CHARS 一一对应
 * （4000）。它比检索页的 MAX_QUERY_CHARACTERS（4096）小，而且是另一套约束：检索输入只
 * 用来算向量，Agent 输入会整段进入模型上下文并按 token 计费，所以刻意不共用同一个常量。
 * 提示词的上界（MAX_SYSTEM_PROMPT_CHARACTERS）在 `@/api/agent-chat`：设置中心的
 * 「Agent 偏好」编辑器是它的另一个消费方。
 */

export const MAX_MESSAGE_CHARACTERS = 4000

export type AgentChatStatus = 'idle' | 'streaming'

export function validateMessage(message: string): string | null {
  if (!message.trim()) {
    return '请输入想问 Agent 的问题。'
  }
  if (message.length > MAX_MESSAGE_CHARACTERS) {
    return `提问不能超过 ${MAX_MESSAGE_CHARACTERS} 个字符。`
  }
  return null
}

/**
 * 校验自定义系统提示词。
 *
 * 空白不算错：清空输入框的语义是「用服务端默认的那份」，后端的 field_validator 也这么处理。
 */
export function validateSystemPrompt(prompt: string): string | null {
  if (prompt.length > MAX_SYSTEM_PROMPT_CHARACTERS) {
    return `系统提示词不能超过 ${MAX_SYSTEM_PROMPT_CHARACTERS} 个字符。`
  }
  return null
}
