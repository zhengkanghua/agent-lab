import { computed, ref, type Ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { listKnowledgeBases } from '@/api/knowledge-bases'
import type { KnowledgeBaseSelection } from '@/api/knowledge-scope'

export function useKnowledgeBaseScope(
  selection: Ref<KnowledgeBaseSelection> = ref({ mode: 'all' }),
) {
  const query = useQuery({
    queryKey: ['knowledge-base-directory'],
    queryFn: ({ signal }) => listKnowledgeBases(false, signal),
    retry: false,
    staleTime: 0,
  })
  const knowledgeBases = computed(() => query.data.value ?? [])
  const error = computed(() => {
    if (query.error.value) return '知识库目录加载失败，请重新加载。'
    if (query.isPending.value) return '正在加载知识库。'
    if (!knowledgeBases.value.length) return '没有启用的知识库。'
    if (selection.value.mode === 'selected') {
      const ids = selection.value.knowledge_base_ids ?? []
      if (!ids.length) return '至少选择一个知识库。'
      if (ids.some((id) => !knowledgeBases.value.some((item) => item.id === id))) {
        return '所选知识库已停用或不存在，请重新选择。'
      }
    }
    return null
  })
  return {
    selection,
    knowledgeBases,
    error,
    loading: query.isPending,
    refresh: () => query.refetch(),
  }
}
