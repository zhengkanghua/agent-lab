import { computed, onScopeDispose, reactive, ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  createKnowledgeBase,
  listKnowledgeBases,
  updateKnowledgeBase,
  type KnowledgeBaseCreateRequest,
  type KnowledgeBaseDto,
  type KnowledgeBaseUpdateRequest,
} from '@/api/knowledge-bases'
import { resolveErrorCopy } from '@/api/error-copy'

const directoryKey = ['knowledge-bases', 'management'] as const

type SaveCommand =
  | { kind: 'create'; body: KnowledgeBaseCreateRequest }
  | { kind: 'update'; id: string; body: KnowledgeBaseUpdateRequest }

function presentError(error: unknown): string {
  return resolveErrorCopy(error, {
    byCode: {
      knowledge_base_key_conflict: '该稳定键已被使用，请换一个。',
      knowledge_base_not_found: '知识库不存在，请刷新列表后重试。',
      invalid_request: '请检查名称、稳定键和说明后重试。',
      response_invalid: '知识库服务返回了无法识别的数据，请刷新后重试。',
    },
    byStatus: {
      401: '登录已失效，请重新登录。',
      403: '当前账号没有知识库管理权限。',
    },
    fallback: '知识库服务暂时不可用，请稍后重试。',
  })
}

/** 配置目录与编辑状态；成功响应才进入缓存，离开页面后的写回不污染后续账号。 */
export function useKnowledgeBases() {
  const queryClient = useQueryClient()
  let disposed = false
  onScopeDispose(() => {
    disposed = true
  })

  const query = useQuery({
    queryKey: directoryKey,
    queryFn: ({ signal }) => listKnowledgeBases(true, signal),
    staleTime: 10_000,
  })
  const items = computed(() => query.data.value ?? [])
  const loadError = computed(() => (query.error.value ? presentError(query.error.value) : ''))
  const feedback = ref('')
  const actionError = ref('')
  const editorOpen = ref(false)
  const editingId = ref<string | null>(null)
  const draft = reactive({ key: '', name: '', description: '', isActive: true })
  const fieldErrors = reactive({ key: '', name: '', description: '' })
  const saveError = ref('')

  const mutation = useMutation({
    mutationFn: async (command: SaveCommand) => {
      await queryClient.cancelQueries({ queryKey: directoryKey })
      return command.kind === 'create'
        ? createKnowledgeBase(command.body)
        : updateKnowledgeBase(command.id, command.body)
    },
    onSuccess: (updated) => {
      if (disposed) return
      queryClient.setQueryData<KnowledgeBaseDto[]>(directoryKey, (previous = []) =>
        [...previous.filter((item) => item.id !== updated.id), updated].sort((left, right) =>
          left.key.localeCompare(right.key),
        ),
      )
    },
  })

  function openEditor(item?: KnowledgeBaseDto): void {
    if (mutation.isPending.value) return
    editingId.value = item?.id ?? null
    Object.assign(draft, {
      key: item?.key ?? '',
      name: item?.name ?? '',
      description: item?.description ?? '',
      isActive: item?.is_active ?? true,
    })
    Object.assign(fieldErrors, { key: '', name: '', description: '' })
    saveError.value = ''
    actionError.value = ''
    feedback.value = ''
    editorOpen.value = true
  }

  function closeEditor(): void {
    if (!mutation.isPending.value) editorOpen.value = false
  }

  async function submit(): Promise<void> {
    if (mutation.isPending.value) return
    const key = draft.key.trim()
    const name = draft.name.trim()
    const description = draft.description.trim()
    fieldErrors.key =
      !editingId.value && (key.length > 64 || !/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(key))
        ? '使用小写字母、数字和连字符，以字母开头，最多 64 个字符。'
        : ''
    fieldErrors.name = !name || name.length > 255 ? '名称不能为空，最多 255 个字符。' : ''
    fieldErrors.description = description.length > 2000 ? '说明最多 2000 个字符。' : ''
    saveError.value = ''
    if (Object.values(fieldErrors).some(Boolean)) return
    try {
      const result = await mutation.mutateAsync(
        editingId.value
          ? {
              kind: 'update',
              id: editingId.value,
              body: { name, description: description || null },
            }
          : {
              kind: 'create',
              body: { key, name, description: description || null, is_active: draft.isActive },
            },
      )
      if (disposed) return
      feedback.value = `已${editingId.value ? '更新' : '创建'}知识库 ${result.name}。`
      editorOpen.value = false
    } catch (error) {
      if (!disposed) saveError.value = presentError(error)
    }
  }

  async function setActive(item: KnowledgeBaseDto, isActive: boolean): Promise<void> {
    if (mutation.isPending.value) return
    feedback.value = ''
    actionError.value = ''
    try {
      const result = await mutation.mutateAsync({
        kind: 'update',
        id: item.id,
        body: { is_active: isActive },
      })
      if (!disposed)
        feedback.value = `已${result.is_active ? '启用' : '停用'}知识库 ${result.name}。`
    } catch (error) {
      if (!disposed) actionError.value = presentError(error)
    }
  }

  return {
    items,
    loadError,
    loading: query.isPending,
    refreshing: query.isFetching,
    refresh: query.refetch,
    feedback,
    actionError,
    editorOpen,
    editingId,
    draft,
    fieldErrors,
    saveError,
    saving: mutation.isPending,
    openEditor,
    closeEditor,
    submit,
    setActive,
  }
}
