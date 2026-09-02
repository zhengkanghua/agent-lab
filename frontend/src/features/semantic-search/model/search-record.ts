import type { NewsDocumentResult } from './search-result'
import type { SearchErrorPresentation } from './search-error'

/**
 * 一条检索记录：一次已提交的语义搜索及其结果快照。
 *
 * 检索页从「单次覆盖式搜索」演进成多轮累积的检索流后，每一次搜索都固化成一条记录，
 * 记录下提交时的检索词与数量参数，以及这次请求从发起到结束的状态。旧记录可以折叠成
 * 标题行回看，而不是被新一轮覆盖。记录只在页内存在：刷新或离开即清空，不落任何后端。
 *
 * 与 Agent 侧区分：这不是「运行(run)/会话(thread)/轮(turn)」那些 agent 词——检索侧
 * 不生成答案、不做多轮推理，每次搜索彼此独立，只是被放在同一条向下长的流里展示。
 */
export type SearchRecordStatus = 'loading' | 'success' | 'empty' | 'error'

export interface SearchRecord {
  /** 页内稳定的 v-for key。用递增序号即可，不参与任何持久化。 */
  id: number
  /** 提交时经 trim 的检索词。既是这轮的问题，也是折叠标题行的识别主信息。 */
  query: string
  /** 这轮提交时的文章数量上限快照（Q7：参数全局一条，但每轮仍要记住自己用了多少，
      否则重发/回看时显示的数会和实际不符）。 */
  documentLimit: number
  /** 每篇新闻最多保留的相关片段数快照。 */
  matchesPerDocument: number
  status: SearchRecordStatus
  /** 成功时后端返回的按新闻分组结果，原始顺序。 */
  results: NewsDocumentResult[]
  /** status 为 error 时的错误展示。 */
  error: SearchErrorPresentation | null
}

let sequence = 0

/** 生成检索记录页内唯一的 id，只用于 v-for key 与折叠态索引。 */
export function nextRecordId(): number {
  sequence += 1
  return sequence
}

/** 构建一条刚提交、还在等待结果的记录。 */
export function createPendingRecord(input: {
  query: string
  documentLimit: number
  matchesPerDocument: number
}): SearchRecord {
  return {
    id: nextRecordId(),
    query: input.query,
    documentLimit: input.documentLimit,
    matchesPerDocument: input.matchesPerDocument,
    status: 'loading',
    results: [],
    error: null,
  }
}

/** 折叠标题行显示的命中数：成功按结果条数，空态是 0，失败不出数。 */
export function recordHitCount(record: SearchRecord): number {
  return record.status === 'success' ? record.results.length : 0
}

/**
 * 供单轮展示用的「最新」判断数据，跨 ApiError 直接携带原始错误。
 * SearchRecord.error 已收敛成文案表结果，重试按钮要不要出现取决于文案是否 retryable，
 * 不需要再回到 ApiError。
 */
export function isErrorRetryable(record: SearchRecord): boolean {
  return record.status === 'error' && (record.error?.retryable ?? false)
}

/** 让测试可重置页内自增序号，避免跨用例 id 增长造成的脆弱断言。 */
export function _resetRecordSequence(): void {
  sequence = 0
}
