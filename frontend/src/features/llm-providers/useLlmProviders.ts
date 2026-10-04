import { computed, onScopeDispose, reactive, ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import {
  createLlmProvider,
  listLlmProviders,
  llmProvidersQueryKey,
  updateLlmProvider,
  type LlmProviderCreateRequest,
  type LlmProviderDto,
  type LlmProviderKind,
  type LlmProviderUpdateRequest,
} from '@/api/llm-providers'
import { isHttpUrl } from '@/api/json-guards'
import { resolveErrorCopy } from '@/api/error-copy'

const directoryKey = llmProvidersQueryKey

type SaveCommand =
  | { kind: 'create'; body: LlmProviderCreateRequest }
  | { kind: 'update'; id: string; body: LlmProviderUpdateRequest }

function presentError(error: unknown): string {
  return resolveErrorCopy(error, {
    byCode: {
      llm_provider_not_found: '这条上游渠道已不存在，请刷新列表后重试。',
      llm_provider_credential_required: '该接入类型必须配置凭据，请填写凭据后再保存。',
      llm_catalog_unavailable:
        '渠道凭据的加密密钥未配置，请联系部署方配置 LLM_CREDENTIAL_KEY 后重试。',
      llm_catalog_database_unavailable: '模型目录服务暂时不可用，请稍后重试。',
      invalid_request: '请检查渠道名称、接入类型与地址后重试。',
      response_invalid: '模型目录服务返回了无法识别的数据，请刷新后重试。',
    },
    byStatus: {
      401: '登录已失效，请重新登录。',
      403: '当前账号没有模型目录管理权限。',
    },
    fallback: '模型目录服务暂时不可用，请稍后重试。',
  })
}

/**
 * 上游渠道目录与编辑状态；成功响应才进入缓存，离开页面后的写回不污染后续账号。
 *
 * 凭据的语义集中在这里的一条规则上：**表单里的凭据留空 = 不改**。所以草稿里的凭据永远不会被
 * 预填，提交时也只有非空才把它放进请求体——留空既不是「清空」，也不会覆盖已存的那份。
 */
export function useLlmProviders() {
  const queryClient = useQueryClient()
  let disposed = false
  onScopeDispose(() => {
    disposed = true
  })

  const query = useQuery({
    queryKey: directoryKey,
    queryFn: ({ signal }) => listLlmProviders(signal),
    staleTime: 10_000,
  })
  const items = computed(() => query.data.value ?? [])
  const loadError = computed(() => (query.error.value ? presentError(query.error.value) : ''))
  const feedback = ref('')
  const actionError = ref('')
  const editorOpen = ref(false)
  const editingId = ref<string | null>(null)
  /** 正在编辑的这条渠道是否已经存过凭据；决定「留空」能不能通过本地校验。 */
  const editingCredentialConfigured = ref(false)
  const draft = reactive({
    name: '',
    provider: 'openai_compatible' as LlmProviderKind,
    baseUrl: '',
    credential: '',
    enabled: true,
  })
  const fieldErrors = reactive({ name: '', baseUrl: '', credential: '' })
  const saveError = ref('')

  const mutation = useMutation({
    mutationFn: async (command: SaveCommand) => {
      await queryClient.cancelQueries({ queryKey: directoryKey })
      return command.kind === 'create'
        ? createLlmProvider(command.body)
        : updateLlmProvider(command.id, command.body)
    },
    onSuccess: (updated) => {
      if (disposed) return
      // 新建的追加在末尾、已有的原地替换：前者与后端按添加时间排序的口径一致。
      queryClient.setQueryData<LlmProviderDto[]>(directoryKey, (previous = []) =>
        previous.some((item) => item.id === updated.id)
          ? previous.map((item) => (item.id === updated.id ? updated : item))
          : [...previous, updated],
      )
    },
  })

  function openEditor(item?: LlmProviderDto): void {
    if (mutation.isPending.value) return
    editingId.value = item?.id ?? null
    editingCredentialConfigured.value = item?.credential_configured ?? false
    Object.assign(draft, {
      name: item?.name ?? '',
      provider: item?.provider ?? 'openai_compatible',
      baseUrl: item?.base_url ?? '',
      // 已存的凭据拿不回来，也不预填：留空就表示这一项不动。
      credential: '',
      enabled: item?.enabled ?? true,
    })
    Object.assign(fieldErrors, { name: '', baseUrl: '', credential: '' })
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
    const name = draft.name.trim()
    const baseUrl = draft.baseUrl.trim()
    const credential = draft.credential.trim()
    fieldErrors.name = !name || name.length > 255 ? '名称不能为空，最多 255 个字符。' : ''
    fieldErrors.baseUrl = !isHttpUrl(baseUrl) ? '请填写以 http:// 或 https:// 开头的完整地址。' : ''
    // 与后端同一条规则：判定看的是保存之后这条渠道会变成什么——已经存过凭据的渠道留空能过，
    // 新建或原本没有凭据的渠道要凭据就必须在这一份表单里填上。
    fieldErrors.credential =
      draft.provider === 'openai_compatible' &&
      !credential &&
      !(editingId.value && editingCredentialConfigured.value)
        ? '该接入类型必须配置凭据，请填写凭据后再保存。'
        : ''
    saveError.value = ''
    if (Object.values(fieldErrors).some(Boolean)) return
    const body = {
      name,
      provider: draft.provider,
      base_url: baseUrl,
      enabled: draft.enabled,
      // 留空 = 不改：字段根本不出现，后端也就没有机会把它理解成清空。
      ...(credential ? { credential } : {}),
    }
    try {
      const result = await mutation.mutateAsync(
        editingId.value
          ? { kind: 'update', id: editingId.value, body }
          : { kind: 'create', body },
      )
      if (disposed) return
      feedback.value = `已${editingId.value ? '更新' : '创建'}上游渠道 ${result.name}。`
      editorOpen.value = false
    } catch (error) {
      if (!disposed) saveError.value = presentError(error)
    }
  }

  async function setEnabled(item: LlmProviderDto, enabled: boolean): Promise<void> {
    if (mutation.isPending.value) return
    feedback.value = ''
    actionError.value = ''
    try {
      const result = await mutation.mutateAsync({
        kind: 'update',
        id: item.id,
        body: { enabled },
      })
      if (!disposed)
        feedback.value = `已${result.enabled ? '启用' : '停用'}上游渠道 ${result.name}。`
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
    setEnabled,
  }
}
