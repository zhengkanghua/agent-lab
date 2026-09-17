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
import type { RemotePreferences } from '@/api/preferences'

/**
 * 用户偏好的纯模型：类型、默认值、清洗与校验。不持有任何响应式状态、不碰网络，
 * 读写与加载态在 `../composables/usePreferences.ts`。
 *
 * 偏好归属于账号、存在后端（`user_preferences` 表），换浏览器或清站点数据都不再丢失。
 * 这里仍然没有任何密钥或凭据——密码与 Token 只存在于 HttpOnly Cookie，绝不落到前端存储
 * （见 frontend/README.md 的登录边界）。
 */

export interface UserPreferences {
  /** 每次检索返回的不同文档数量（契约边界见 @/api/document-search）。 */
  documentLimit: number
  /** 每篇文档保留的相关片段数。 */
  matchesPerDocument: number
  /** 自定义系统提示词。空串表示使用服务端默认的那份。 */
  agentSystemPrompt: string
}

export const DEFAULT_PREFERENCES: UserPreferences = {
  documentLimit: DEFAULT_RESULT_LIMIT,
  matchesPerDocument: DEFAULT_MATCHES_PER_DOCUMENT,
  agentSystemPrompt: '',
}

export function validateAgentSystemPrompt(prompt: string): string | null {
  if (prompt.length > MAX_SYSTEM_PROMPT_CHARACTERS) {
    return `系统提示词不能超过 ${MAX_SYSTEM_PROMPT_CHARACTERS} 个字符。`
  }
  return null
}

/**
 * 把任意来源清洗成安全值：缺失/非法字段落回默认，数量参数按契约边界归一，
 * 提示词超长截断到上界。
 *
 * 接口返回值和「读接口失败时的兜底」两条路径都走它——任何异常输入都不能让设置页打不开，
 * 偏好是体验数据，不是事实数据。
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

/** 把接口返回的那份（字段名与前端不同）转成前端偏好。 */
export function fromRemotePreferences(remote: RemotePreferences): UserPreferences {
  return sanitizePreferences({
    documentLimit: remote.documentLimit,
    matchesPerDocument: remote.matchesPerDocument,
    // 后端的 null 表示「未配置」，在界面上就是空串。
    agentSystemPrompt: remote.systemPrompt,
  })
}

/** 把前端偏好转成接口要提交的形状。 */
export function toRemotePreferences(preferences: UserPreferences): RemotePreferences {
  return {
    systemPrompt: preferences.agentSystemPrompt,
    documentLimit: preferences.documentLimit,
    matchesPerDocument: preferences.matchesPerDocument,
  }
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
