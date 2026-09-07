import { computed, onScopeDispose, ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { listKnowledgeBases } from '@/api/knowledge-bases'
import { bindSource, listSources, type SourceDto } from '@/api/sources'
import { resolveErrorCopy } from '@/api/error-copy'

const sourcesKey = ['sources', 'management'] as const
const knowledgeBaseOptionsKey = ['knowledge-bases', 'source-management'] as const

function presentError(error: unknown): string {
  return resolveErrorCopy(error, {
    byCode: {
      source_binding_conflict: '来源已有文档、删除待办或未完成写入，不能修改绑定。',
      source_write_recovery_required: '写操作结果需要人工核实，暂时不能修改绑定。',
      knowledge_base_inactive: '停用的知识库不能绑定新来源。',
      source_not_found: '来源不存在，请刷新列表后重试。',
      knowledge_base_not_found: '目标知识库不存在，请刷新选项后重试。',
      invalid_request: '请求格式不正确，请刷新后重试。',
      response_invalid: '来源服务返回了无法识别的数据，请刷新后重试。',
    },
    byStatus: {
      401: '登录已失效，请重新登录。',
      403: '当前账号没有来源管理权限。',
    },
    fallback: '来源服务暂时不可用，请稍后重试。',
  })
}

/** 来源目录与绑定状态；只有成功响应才写回缓存，失败时行内选择保持原值。 */
export function useSources() {
  const queryClient = useQueryClient()
  let disposed = false
  onScopeDispose(() => {
    disposed = true
  })

  const query = useQuery({
    queryKey: sourcesKey,
    queryFn: ({ signal }) => listSources(signal),
    staleTime: 10_000,
  })
  // 读取停用库以展示当前绑定；组件只允许启用库成为新的目标。
  const optionsQuery = useQuery({
    queryKey: knowledgeBaseOptionsKey,
    queryFn: ({ signal }) => listKnowledgeBases(true, signal),
    staleTime: 10_000,
  })

  const items = computed(() => query.data.value ?? [])
  const knowledgeBaseOptions = computed(() => optionsQuery.data.value ?? [])
  const loadError = computed(() => {
    if (query.error.value) return presentError(query.error.value)
    if (optionsQuery.error.value) return presentError(optionsQuery.error.value)
    return ''
  })
  const feedback = ref('')
  const actionError = ref('')

  const mutation = useMutation({
    mutationFn: ({ id, knowledgeBaseId }: { id: string; knowledgeBaseId: string | null }) =>
      bindSource(id, knowledgeBaseId),
    onSuccess: (updated) => {
      if (disposed) return
      queryClient.setQueryData<SourceDto[]>(sourcesKey, (previous = []) =>
        previous.map((item) => (item.id === updated.id ? updated : item)),
      )
    },
  })

  function knowledgeBaseName(id: string | null): string {
    if (id === null) return ''
    return knowledgeBaseOptions.value.find((option) => option.id === id)?.name ?? id
  }

  async function changeBinding(item: SourceDto, knowledgeBaseId: string | null): Promise<void> {
    if (mutation.isPending.value) return
    feedback.value = ''
    actionError.value = ''
    try {
      const result = await mutation.mutateAsync({ id: item.id, knowledgeBaseId })
      if (disposed) return
      feedback.value = result.knowledge_base_key
        ? `已把 ${result.name} 绑定到 ${knowledgeBaseName(result.knowledge_base_id)}。`
        : `已解除 ${result.name} 的绑定，下一次同步从新基线开始。`
    } catch (error) {
      if (!disposed) actionError.value = presentError(error)
    }
  }

  return {
    items,
    knowledgeBaseOptions,
    loadError,
    loading: query.isPending,
    refreshing: query.isFetching,
    refresh: () => {
      void query.refetch()
      void optionsQuery.refetch()
    },
    feedback,
    actionError,
    binding: mutation.isPending,
    knowledgeBaseName,
    changeBinding,
  }
}
