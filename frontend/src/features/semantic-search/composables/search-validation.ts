/**
 * 搜索输入和数量参数的共享约束。
 * 该模块只负责前端输入校验与边界归一化，不执行任何网络请求。
 */

export const MAX_QUERY_CHARACTERS = 4096
export const MIN_RESULT_LIMIT = 1
export const MAX_RESULT_LIMIT = 100
export const DEFAULT_RESULT_LIMIT = 10
export const DEFAULT_MATCHES_PER_DOCUMENT = 3
export const MAX_MATCHES_PER_DOCUMENT = 20

export type SearchStatus = 'idle' | 'loading' | 'success' | 'empty' | 'error'

export function validateQuery(query: string): string | null {
  if (!query.trim()) {
    return '请输入需要研究的新闻问题或主题。'
  }
  if (query.length > MAX_QUERY_CHARACTERS) {
    return `检索内容不能超过 ${MAX_QUERY_CHARACTERS} 个字符。`
  }
  return null
}

export function normalizeResultLimit(value: number, fallback = DEFAULT_RESULT_LIMIT): number {
  if (!Number.isFinite(value)) return fallback
  return Math.min(MAX_RESULT_LIMIT, Math.max(MIN_RESULT_LIMIT, Math.trunc(value)))
}

export function normalizeMatchesPerDocument(
  value: number,
  fallback = DEFAULT_MATCHES_PER_DOCUMENT,
): number {
  if (!Number.isFinite(value)) return fallback
  return Math.min(MAX_MATCHES_PER_DOCUMENT, Math.max(MIN_RESULT_LIMIT, Math.trunc(value)))
}
