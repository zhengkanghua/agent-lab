/**
 * 搜索输入和数量参数的共享约束。
 * 该模块只负责前端输入校验与边界归一化，不执行任何网络请求。
 *
 * 放在 model/ 而不是 composables/：这里全是纯常量与纯函数，没有任何 ref 或
 * 响应式状态，与 search-result.ts 的格式化函数同类。
 *
 * 三个上界与后端契约一一对应，不要按当前 UI 的下拉选项收窄：
 * MAX_QUERY_CHARACTERS 对应 schemas 两个 SearchRequest 的 query 上限，
 * MAX_RESULT_LIMIT 对应 vector_search.py 的 top_k 与 document_search.py 的
 * document_limit（都是 1..100），MAX_MATCHES_PER_DOCUMENT 对应
 * matches_per_document（1..20）。UI 只给到 20 是产品选择，归一化仍按契约兜底，
 * 以免发出后端必然拒绝的请求。
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

export function normalizeResultLimit(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_RESULT_LIMIT
  return Math.min(MAX_RESULT_LIMIT, Math.max(MIN_RESULT_LIMIT, Math.trunc(value)))
}

export function normalizeMatchesPerDocument(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_MATCHES_PER_DOCUMENT
  return Math.min(MAX_MATCHES_PER_DOCUMENT, Math.max(MIN_RESULT_LIMIT, Math.trunc(value)))
}
