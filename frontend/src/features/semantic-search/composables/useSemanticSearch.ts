import { ref } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { searchDocuments } from '../../../api/document-search'
import { toNewsDocumentResults, type NewsDocumentResult } from '../model/search-result'
import {
  DEFAULT_MATCHES_PER_DOCUMENT,
  DEFAULT_RESULT_LIMIT,
  normalizeMatchesPerDocument,
  normalizeResultLimit,
} from './search-validation'
import { useSearchRequest } from './useSearchRequest'

interface SearchVariables {
  query: string
  documentLimit: number
  matchesPerDocument: number
  signal: AbortSignal
}

/**
 * 编排文档级语义搜索。文档分组由 Qdrant grouped query 在后端完成，前端不做聚合。
 * query 生命周期、陈旧响应守卫和状态机由 useSearchRequest 提供。
 */
export function useSemanticSearch() {
  const documentLimit = ref(DEFAULT_RESULT_LIMIT)
  const matchesPerDocument = ref(DEFAULT_MATCHES_PER_DOCUMENT)

  const mutation = useMutation({
    mutationFn: ({ query, documentLimit, matchesPerDocument, signal }: SearchVariables) =>
      searchDocuments({ query, documentLimit, matchesPerDocument, signal }),
  })

  const request = useSearchRequest<NewsDocumentResult>(async (query, signal) => {
    const normalizedDocumentLimit = normalizeResultLimit(documentLimit.value)
    const normalizedMatchesPerDocument = normalizeMatchesPerDocument(matchesPerDocument.value)
    documentLimit.value = normalizedDocumentLimit
    matchesPerDocument.value = normalizedMatchesPerDocument

    const response = await mutation.mutateAsync({
      query,
      documentLimit: normalizedDocumentLimit,
      matchesPerDocument: normalizedMatchesPerDocument,
      signal,
    })

    return toNewsDocumentResults(response)
  })

  return { ...request, documentLimit, matchesPerDocument }
}
