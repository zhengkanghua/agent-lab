/**
 * 检索输入的共享约束。纯常量与纯函数，不做任何网络请求。
 *
 * MAX_QUERY_CHARACTERS 对应 schemas 两个 SearchRequest 的 query 上限（4096）。
 * 数量参数（document_limit / matches_per_document）的契约边界不在这里：它们同时被
 * 设置中心的「检索偏好」消费，单一事实源在 `@/api/document-search`。
 */

export const MAX_QUERY_CHARACTERS = 4096

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
