import { computed, onScopeDispose, shallowRef, ref, watch } from 'vue'
import { ApiError, isAbortError } from '../../../api/client'
import { MAX_QUERY_CHARACTERS, validateQuery, type SearchStatus } from '../model/search-validation'

/**
 * 一次搜索请求的执行体。由调用方提供，负责归一化自己的请求参数、调用 api 层并把响应
 * 映射成展示模型。底座只关心它返回的数组长度和它可能抛出的错误。
 */
export type SearchExecutor<TResult> = (
  query: string,
  signal: AbortSignal,
) => Promise<TResult[]>

/**
 * 文档模式与 Chunk 模式共用的搜索请求底座。
 *
 * 收敛的是两种模式完全一致的部分：query 校验时机、AbortController 陈旧响应守卫、
 * idle/loading/success/empty/error 状态机、以及 reset/clear/retry/cancel 语义。
 * 两种模式的差异（请求参数、调用哪个 api、结果映射）留在各自的 executor 里。
 *
 * 陈旧响应守卫依赖 requestSequence 与 requestId 的比较，而不是只依赖 AbortController：
 * 请求已经 resolve、await 还没恢复执行的那个窗口内 abort() 不再起作用，此时只有序号
 * 比较能拦住旧响应覆盖新结果。
 */
export function useSearchRequest<TResult>(executor: SearchExecutor<TResult>) {
  const query = ref('')
  const results = shallowRef<TResult[]>([])
  const status = ref<SearchStatus>('idle')
  const inputError = ref<string | null>(null)
  const requestError = ref<ApiError | null>(null)
  const lastQuery = ref('')

  let requestSequence = 0
  let activeController: AbortController | null = null

  const remainingCharacters = computed(() => MAX_QUERY_CHARACTERS - query.value.length)
  const canSearch = computed(() => query.value.trim().length > 0 && status.value !== 'loading')

  watch(query, (value) => {
    if (inputError.value && !validateQuery(value)) {
      inputError.value = null
    }
  })

  async function search(): Promise<void> {
    const normalizedQuery = query.value.trim()

    inputError.value = validateQuery(query.value)
    if (inputError.value) {
      cancelActiveRequest()
      status.value = 'idle'
      return
    }

    cancelActiveRequest()
    const requestId = ++requestSequence
    const controller = new AbortController()
    activeController = controller
    requestError.value = null
    inputError.value = null
    lastQuery.value = normalizedQuery
    status.value = 'loading'

    try {
      const mapped = await executor(normalizedQuery, controller.signal)

      if (requestId !== requestSequence) return

      results.value = mapped
      status.value = mapped.length > 0 ? 'success' : 'empty'
    } catch (error) {
      if (requestId !== requestSequence || isAbortError(error)) return

      results.value = []
      requestError.value =
        error instanceof ApiError
          ? error
          : new ApiError({
              message: 'Unexpected search failure.',
              code: 'unknown_error',
              cause: error,
            })
      status.value = 'error'
    } finally {
      if (requestId === requestSequence) activeController = null
    }
  }

  function reset(): void {
    cancelActiveRequest()
    results.value = []
    lastQuery.value = ''
    inputError.value = null
    requestError.value = null
    status.value = 'idle'
  }

  function clear(): void {
    reset()
    query.value = ''
  }

  function retry(): Promise<void> {
    if (lastQuery.value) query.value = lastQuery.value
    return search()
  }

  function cancelActiveRequest(): void {
    requestSequence += 1
    activeController?.abort()
    activeController = null
  }

  onScopeDispose(cancelActiveRequest)

  return {
    query,
    results,
    status,
    inputError,
    requestError,
    lastQuery,
    remainingCharacters,
    canSearch,
    search,
    clear,
    reset,
    cancel: cancelActiveRequest,
    retry,
  }
}
