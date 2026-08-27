import { computed, onScopeDispose, ref, watch } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { ApiError, isAbortError } from '../../../api/client'
import { searchVector } from '../../../api/vector-search'
import { toNewsChunkResults, type NewsChunkResult } from '../model/search-result'
import {
  DEFAULT_RESULT_LIMIT,
  MAX_QUERY_CHARACTERS,
  normalizeResultLimit,
  type SearchStatus,
  validateQuery,
} from './search-validation'

interface SearchVariables {
  query: string
  topK: number
  signal: AbortSignal
}

/**
 * 编排兼容的 Chunk 级语义搜索。
 * 该 composable 保留 Qdrant 原始顺序和重复 document，不做文档聚合；网络访问统一经 api 层完成。
 */
export function useChunkSearch() {
  const query = ref('')
  const topK = ref(DEFAULT_RESULT_LIMIT)
  const results = ref<NewsChunkResult[]>([])
  const status = ref<SearchStatus>('idle')
  const inputError = ref<string | null>(null)
  const requestError = ref<ApiError | null>(null)
  const lastQuery = ref('')

  let requestSequence = 0
  let activeController: AbortController | null = null

  const mutation = useMutation({
    mutationFn: ({ query, topK, signal }: SearchVariables) => searchVector({ query, topK, signal }),
  })

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
      const normalizedTopK = normalizeResultLimit(topK.value)
      topK.value = normalizedTopK
      const response = await mutation.mutateAsync({
        query: normalizedQuery,
        topK: normalizedTopK,
        signal: controller.signal,
      })

      if (requestId !== requestSequence) return

      // Chunk 模式必须保留后端返回顺序；同一 document 的重复命中是有意的兼容语义。
      results.value = toNewsChunkResults(response)
      status.value = results.value.length > 0 ? 'success' : 'empty'
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
    topK,
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
