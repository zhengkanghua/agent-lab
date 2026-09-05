import { MAX_SYSTEM_PROMPT_CHARACTERS } from '@/api/agent-chat'
import {
  DEFAULT_MATCHES_PER_DOCUMENT,
  DEFAULT_RESULT_LIMIT,
  MAX_MATCHES_PER_DOCUMENT,
  MAX_RESULT_LIMIT,
  MIN_RESULT_LIMIT,
  normalizeMatchesPerDocument,
  normalizeResultLimit,
} from '@/api/document-search'

/**
 * 用户偏好的纯模型：类型、默认值、清洗与校验。不持有任何响应式状态、不碰浏览器 API，
 * 持久化在 `../composables/usePreferences.ts`。
 *
 * 偏好只存在本浏览器（localStorage），不进后端：数量参数与系统提示词本来就随每次请求
 * 发送，后端不需要知道「用户偏好的默认值」。因此这里没有任何密钥或凭据——密码与 Token
 * 仍然只存在于 HttpOnly Cookie，绝不入 localStorage（见 frontend/README.md 的登录边界）。
 */

export interface UserPreferences {
  /** 每次检索返回的不同新闻数量（契约边界见 @/api/document-search）。 */
  documentLimit: number
  /** 每篇新闻保留的相关片段数。 */
  matchesPerDocument: number
  /** 自定义系统提示词。空串表示使用服务端默认的那份。 */
  agentSystemPrompt: string
}

export const DEFAULT_PREFERENCES: UserPreferences = {
  documentLimit: DEFAULT_RESULT_LIMIT,
  matchesPerDocument: DEFAULT_MATCHES_PER_DOCUMENT,
  agentSystemPrompt: '',
}

/** localStorage 键名。带版本号：未来字段语义变化时整体作废重读，不做跨版本迁移。 */
export const PREFERENCES_STORAGE_KEY = 'signaldesk.preferences.v1'

export function validateAgentSystemPrompt(prompt: string): string | null {
  if (prompt.length > MAX_SYSTEM_PROMPT_CHARACTERS) {
    return `系统提示词不能超过 ${MAX_SYSTEM_PROMPT_CHARACTERS} 个字符。`
  }
  return null
}

/**
 * 把任意来源（localStorage JSON、旧版本残留）清洗成安全值：
 * 缺失/非法字段落回默认，数量参数按契约边界归一，提示词超长截断到上界。
 * 任何异常输入都不能让设置页打不开——偏好是体验数据，不是事实数据。
 */
export function sanitizePreferences(input: unknown): UserPreferences {
  if (typeof input !== 'object' || input === null) {
    return { ...DEFAULT_PREFERENCES }
  }

  const raw = input as Record<string, unknown>
  const documentLimit =
    typeof raw.documentLimit === 'number'
      ? normalizeResultLimit(raw.documentLimit)
      : DEFAULT_PREFERENCES.documentLimit
  const matchesPerDocument =
    typeof raw.matchesPerDocument === 'number'
      ? normalizeMatchesPerDocument(raw.matchesPerDocument)
      : DEFAULT_PREFERENCES.matchesPerDocument
  const agentSystemPrompt =
    typeof raw.agentSystemPrompt === 'string'
      ? raw.agentSystemPrompt.slice(0, MAX_SYSTEM_PROMPT_CHARACTERS)
      : DEFAULT_PREFERENCES.agentSystemPrompt

  return { documentLimit, matchesPerDocument, agentSystemPrompt }
}

/**
 * 数量参数在设置页暴露的选项。是产品选择（比契约上界窄），不是契约本身；
 * 归一化仍按 api 层的契约边界兜底。
 */
export const DOCUMENT_LIMIT_OPTIONS = [1, 5, 10, 20].filter(
  (value) => value >= MIN_RESULT_LIMIT && value <= MAX_RESULT_LIMIT,
)
export const MATCHES_PER_DOCUMENT_OPTIONS = [1, 3, 5].filter(
  (value) => value <= MAX_MATCHES_PER_DOCUMENT,
)
