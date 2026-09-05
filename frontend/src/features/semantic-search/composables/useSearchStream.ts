import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { isAbortError, ApiError } from '@/api/client'
import {
  DEFAULT_MATCHES_PER_DOCUMENT,
  DEFAULT_RESULT_LIMIT,
  normalizeMatchesPerDocument,
  normalizeResultLimit,
  searchDocuments,
} from '@/api/document-search'
import { toNewsDocumentResults, type NewsDocumentResult } from '../model/search-result'
import { presentSearchError } from '../model/search-error'
import { MAX_QUERY_CHARACTERS, validateQuery } from '../model/search-validation'
import {
  createPendingRecord,
  type SearchRecord,
  type SearchRecordStatus,
} from '../model/search-record'

/** 注入数量参数的读取时机：提交那一刻的偏好值，就是这一轮用的值。 */
export interface UseSearchStreamOptions {
  getDocumentLimit?: () => number
  getMatchesPerDocument?: () => number
}

/**
 * 检索流状态编排：把每次搜索追加成一条记录，形成可回看、可折叠、可清空的多轮检索流。
 *
 * 取代旧的 useSemanticSearch / useChunkSearch（单次请求状态机，第二次搜索会覆盖第一次，
 * 且带模式切换）。检索页现在只走文档级 /document-search（按新闻分组），不再有「按片段」。
 *
 * 状态分两层：
 *  - records：一条条已提交搜索的累积结果，最新在数组末尾，渲染时由调用方决定贴顶方向；
 *  - 输入草稿 draft 与数量参数：数量参数是设置中心的偏好（全局一份，影响之后所有检索轮），
 *    由调用方注入 getter，本 composable 不持有偏好——它只管「这一轮发什么」。
 *
 * 一次只允许一条在途请求：提交新搜索会 abort 上一条。检索流直觉是「还没等到结果就再搜
 * 一条，应取消前一条」，不该并行堆积；这也让陈旧响应无法覆盖新结果。requestSequence 仍
 * 保留，兜住 abort 已 resolve、await 尚未恢复的那个窗口。
 */
export function useSearchStream({
  getDocumentLimit = () => DEFAULT_RESULT_LIMIT,
  getMatchesPerDocument = () => DEFAULT_MATCHES_PER_DOCUMENT,
}: UseSearchStreamOptions = {}) {
  const draft = ref('')
  const records = shallowRef<SearchRecord[]>([])

  const inputError = ref<string | null>(null)

  let requestSequence = 0
  let activeController: AbortController | null = null

  const remainingCharacters = computed(() => MAX_QUERY_CHARACTERS - draft.value.length)

  /** 最新一条记录；还没有记录（还没搜过）时为 null。 */
  const latestRecord = computed<SearchRecord | null>(() => {
    const list = records.value
    return list.length > 0 ? list[list.length - 1]! : null
  })

  const isSearching = computed(() => latestRecord.value?.status === 'loading')

  watch(draft, (value) => {
    if (inputError.value && !validateQuery(value)) {
      inputError.value = null
    }
  })

  function settleRecord(
    recordId: number,
    status: Exclude<SearchRecordStatus, 'loading'>,
    results: NewsDocumentResult[],
    error: ApiError | null,
  ): void {
    records.value = records.value.map((record) => {
      if (record.id !== recordId) return record
      return {
        ...record,
        status,
        results: status === 'success' ? results : [],
        error: error ? presentSearchError(error) : null,
      }
    })
  }

  async function search(): Promise<void> {
    const normalizedQuery = draft.value.trim()

    inputError.value = validateQuery(draft.value)
    if (inputError.value) {
      // 输入校验没通过就结束：不要碰在途请求，也别清空任何状态。
      return
    }

    abortActive()

    const limit = normalizeResultLimit(getDocumentLimit())
    const perDocument = normalizeMatchesPerDocument(getMatchesPerDocument())

    // 若上一条还在「loading」（用户没等结果就再搜一次），这次取消会 abort 它但不会让它
    // settle，直接把它从流里移走——一个被用户中途放弃的占位轮不该留在界面上显示永远在转。
    records.value = records.value.filter((record) => record.status !== 'loading')

    const pending = createPendingRecord({
      query: normalizedQuery,
      documentLimit: limit,
      matchesPerDocument: perDocument,
    })
    records.value = [...records.value, pending]

    const requestId = ++requestSequence
    const controller = new AbortController()
    activeController = controller
    inputError.value = null

    try {
      const response = await searchDocuments({
        query: normalizedQuery,
        documentLimit: limit,
        matchesPerDocument: perDocument,
        signal: controller.signal,
      })

      if (requestId !== requestSequence) return

      const mapped = toNewsDocumentResults(response)
      settleRecord(pending.id, mapped.length > 0 ? 'success' : 'empty', mapped, null)
    } catch (caught) {
      if (requestId !== requestSequence || isAbortError(caught)) return

      const error =
        caught instanceof ApiError
          ? caught
          : new ApiError({
              message: 'Unexpected search failure.',
              code: 'unknown_error',
              cause: caught,
            })
      settleRecord(pending.id, 'error', [], error)
    } finally {
      if (requestId === requestSequence) {
        activeController = null
      }
    }
  }

  /**
   * 用某条记录的检索词重新发起一次搜索。
   *
   * 用于错误轮的「再试一次」：直接把那条的 query 放进草稿并搜一遍，追加成新的最新记录。
   * 旧的那条失败记录保留在流里可回看，而不是原地被改写——它记录的是那次没搜成功的事实。
   */
  async function retry(recordQuery: string): Promise<void> {
    draft.value = recordQuery
    await search()
  }

  /** 清空整个检索流与输入草稿（Q6：等价于刷新后从零开始）。 */
  function clear(): void {
    abortActive()
    records.value = []
    inputError.value = null
    draft.value = ''
  }

  function abortActive(): void {
    requestSequence += 1
    activeController?.abort()
    activeController = null
  }

  onScopeDispose(abortActive)

  return {
    draft,
    records,
    inputError,
    remainingCharacters,
    latestRecord,
    isSearching,
    search,
    retry,
    clear,
  }
}
