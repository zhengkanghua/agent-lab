import { computed, onScopeDispose, ref, watch } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { ApiError, isAbortError } from '../../../api/client'
import { searchDocuments } from '../../../api/document-search'
import { toNewsDocumentResults, type NewsDocumentResult } from '../model/search-result'
import {
  DEFAULT_MATCHES_PER_DOCUMENT,
  DEFAULT_RESULT_LIMIT,
  MAX_QUERY_CHARACTERS,
  normalizeMatchesPerDocument,
  normalizeResultLimit,
  validateQuery,
  type SearchStatus,
} from './search-validation'

export {
  DEFAULT_MATCHES_PER_DOCUMENT,
  DEFAULT_RESULT_LIMIT,
  MAX_QUERY_CHARACTERS,
  MAX_MATCHES_PER_DOCUMENT,
  MAX_RESULT_LIMIT,
  MIN_RESULT_LIMIT,
  type SearchStatus,
} from './search-validation'

interface SearchVariables {
  query: string
  documentLimit: number
  matchesPerDocument: number
  signal: AbortSignal
}

export function useSemanticSearch() {
  const query = ref('')
  const documentLimit = ref(DEFAULT_RESULT_LIMIT)
  const matchesPerDocument = ref(DEFAULT_MATCHES_PER_DOCUMENT)
  const results = ref<NewsDocumentResult[]>([])
  const status = ref<SearchStatus>('idle')
  const inputError = ref<string | null>(null)
  const requestError = ref<ApiError | null>(null)
  const lastQuery = ref('')

  let requestSequence = 0
  let activeController: AbortController | null = null

  const mutation = useMutation({
    mutationFn: ({ query, documentLimit, matchesPerDocument, signal }: SearchVariables) =>
      searchDocuments({ query, documentLimit, matchesPerDocument, signal }),
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
      const normalizedDocumentLimit = normalizeResultLimit(documentLimit.value)
      const normalizedMatchesPerDocument = normalizeMatchesPerDocument(matchesPerDocument.value)
      documentLimit.value = normalizedDocumentLimit
      matchesPerDocument.value = normalizedMatchesPerDocument

      const response = await mutation.mutateAsync({
        query: normalizedQuery,
        documentLimit: normalizedDocumentLimit,
        matchesPerDocument: normalizedMatchesPerDocument,
        signal: controller.signal,
      })

      if (requestId !== requestSequence) {
        return
      }

      results.value = toNewsDocumentResults(response)
      status.value = results.value.length > 0 ? 'success' : 'empty'
    } catch (error) {
      if (requestId !== requestSequence || isAbortError(error)) {
        return
      }

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
      if (requestId === requestSequence) {
        activeController = null
      }
    }
  }

  function clear(): void {
    reset()
    query.value = ''
  }

  function reset(): void {
    cancelActiveRequest()
    results.value = []
    lastQuery.value = ''
    inputError.value = null
    requestError.value = null
    status.value = 'idle'
  }

  function retry(): Promise<void> {
    if (lastQuery.value) {
      query.value = lastQuery.value
    }
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
    documentLimit,
    matchesPerDocument,
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
