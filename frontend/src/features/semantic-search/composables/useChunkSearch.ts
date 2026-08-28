import { ref } from 'vue'
import { searchVector } from '../../../api/vector-search'
import { toNewsChunkResults, type NewsChunkResult } from '../model/search-result'
import { DEFAULT_RESULT_LIMIT, normalizeResultLimit } from '../model/search-validation'
import { useSearchRequest } from './useSearchRequest'

/**
 * 编排兼容的 Chunk 级语义搜索。
 * 该 composable 保留 Qdrant 原始顺序和重复 document，不做文档聚合；网络访问统一经 api 层完成。
 * query 生命周期、陈旧响应守卫和状态机由 useSearchRequest 提供。
 */
export function useChunkSearch() {
  const topK = ref(DEFAULT_RESULT_LIMIT)

  const request = useSearchRequest<NewsChunkResult>(async (query, signal) => {
    const normalizedTopK = normalizeResultLimit(topK.value)
    topK.value = normalizedTopK

    const response = await searchVector({ query, topK: normalizedTopK, signal })

    // Chunk 模式必须保留后端返回顺序；同一 document 的重复命中是有意的兼容语义。
    return toNewsChunkResults(response)
  })

  return { ...request, topK }
}
