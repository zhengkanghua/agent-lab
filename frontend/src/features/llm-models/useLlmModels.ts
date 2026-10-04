import { computed, onScopeDispose, reactive, ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { listLlmProviders, llmProvidersQueryKey } from '@/api/llm-providers'
import {
  createLlmModel,
  listLlmModels,
  updateLlmModel,
  type LlmModelCreateRequest,
  type LlmModelDto,
  type LlmModelUpdateRequest,
} from '@/api/llm-models'
import { resolveErrorCopy } from '@/api/error-copy'

const modelsKey = ['llm-models', 'management'] as const

type SaveCommand =
  | { kind: 'create'; body: LlmModelCreateRequest }
  | { kind: 'update'; id: string; body: LlmModelUpdateRequest }

function presentError(error: unknown): string {
  return resolveErrorCopy(error, {
    byCode: {
      llm_model_entry_not_found: '这条可用模型已不存在，请刷新列表后重试。',
      llm_model_name_conflict: '这条渠道下已经有同名的上游模型，请换一个上游模型名。',
      llm_model_provider_disabled: '目标上游渠道已停用，请先启用它再把这个模型挂过去。',
      llm_model_default_not_available:
        '不能把当前不可用的模型设为默认，请先启用这条模型和它所属的渠道。',
      llm_model_default_cannot_be_cleared: '默认标记不能直接取消，请先把另一条模型设为默认。',
      llm_model_default_cannot_be_disabled:
        '这条模型是当前默认，请先把默认换到另一条模型再停用它。',
      llm_model_default_conflict: '另一个请求刚刚改过默认模型，请刷新列表后重试。',
      llm_provider_in_use_as_default:
        '这条渠道下有当前默认模型，请先把默认换到别的渠道的模型再停用它。',
      llm_provider_not_found: '目标上游渠道已不存在，请刷新列表后重试。',
      llm_catalog_unavailable:
        '渠道凭据的加密密钥未配置，请联系部署方配置 LLM_CREDENTIAL_KEY 后重试。',
      llm_catalog_database_unavailable: '模型目录服务暂时不可用，请稍后重试。',
      invalid_request: '请检查所属渠道、上游模型名与上下文窗口后重试。',
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
 * 可用模型目录与编辑状态：一个渠道下面的条目、启停与「设为默认」。
 *
 * 两条语义集中在这里：
 *
 * - **上下文窗口是必填的正整数**，空、0、小数、非数字都在本地拦住；它是压缩触发与单次工具
 *   输出上限的比例基准，不是可选的备注，所以不本地补一个默认值替用户填。
 * - **默认标记只有一条命令能动**（`setDefault`）：编辑表单的提交不带 `is_default`，否则改个
 *   名字就可能顺手把默认取消掉，而后端会因此拒掉整个保存（见 spec 0002 的「默认值的唯一性」）。
 *
 * 渠道列表与渠道目录页共用同一份缓存（同一个 query key）：两边读的是同一份上游渠道配置。
 */
export function useLlmModels() {
  const queryClient = useQueryClient()
  let disposed = false
  onScopeDispose(() => {
    disposed = true
  })

  const query = useQuery({
    queryKey: modelsKey,
    queryFn: ({ signal }) => listLlmModels(signal),
    staleTime: 10_000,
  })
  // 新增模型要先挑一条渠道，所以这一页也要读上游渠道目录。它只有读、没有写：渠道的增改在
  // 渠道那个板块里做。
  const providersQuery = useQuery({
    queryKey: llmProvidersQueryKey,
    queryFn: ({ signal }) => listLlmProviders(signal),
    staleTime: 10_000,
  })
  const items = computed(() => query.data.value ?? [])
  const providers = computed(() => providersQuery.data.value ?? [])
  const loadError = computed(() => (query.error.value ? presentError(query.error.value) : ''))
  const providersError = computed(() =>
    providersQuery.error.value ? presentError(providersQuery.error.value) : '',
  )
  const feedback = ref('')
  const actionError = ref('')
  const editorOpen = ref(false)
  const editingId = ref<string | null>(null)
  const draft = reactive({
    providerId: '',
    upstreamModelName: '',
    displayName: '',
    /** 输入框给的是字符串或数字；空串表示「留空」，不折成 0，否则看不出用户填没填。 */
    contextWindow: '32768' as string | number,
    enabled: true,
  })
  const fieldErrors = reactive({
    providerId: '',
    upstreamModelName: '',
    displayName: '',
    contextWindow: '',
  })
  const saveError = ref('')

  const mutation = useMutation({
    mutationFn: (command: SaveCommand) => {
      return command.kind === 'create'
        ? createLlmModel(command.body)
        : updateLlmModel(command.id, command.body)
    },
    onSuccess: () => {
      if (disposed) return
      // 整表重取而不是就地改一行：设为默认会同时改掉旧默认那一行，启停又可能让另一条成为
      // 默认，改哪几行取决于后端算出来的结果，前端照着自己猜就会显示出两个「默认」。
      void queryClient.invalidateQueries({ queryKey: modelsKey })
    },
  })

  function openEditor(item?: LlmModelDto): void {
    if (mutation.isPending.value) return
    editingId.value = item?.id ?? null
    Object.assign(draft, {
      // 新建默认选第一条渠道：只有一条渠道时不必再点一次；一条都没有时留空，本地校验会拦住。
      providerId: item?.provider_id ?? providers.value[0]?.id ?? '',
      upstreamModelName: item?.upstream_model_name ?? '',
      displayName: item?.display_name ?? '',
      // 新建预填 32768（spec 0002 的实现决策）：它只是一个起点，提示里说清了要改成确认过的值。
      contextWindow: item ? item.context_window : '32768',
      enabled: item?.enabled ?? true,
    })
    Object.assign(fieldErrors, {
      providerId: '',
      upstreamModelName: '',
      displayName: '',
      contextWindow: '',
    })
    saveError.value = ''
    actionError.value = ''
    feedback.value = ''
    editorOpen.value = true
  }

  function closeEditor(): void {
    if (!mutation.isPending.value) editorOpen.value = false
  }

  /** 读草稿里的窗口值：空、0、小数、非数字都算「没填一个能用的数」。 */
  function readContextWindow(): number | null {
    const raw = draft.contextWindow
    const value = typeof raw === 'number' ? raw : Number(raw.trim())
    return Number.isInteger(value) && value > 0 ? value : null
  }

  async function submit(): Promise<void> {
    if (mutation.isPending.value) return
    const upstreamModelName = draft.upstreamModelName.trim()
    const displayName = draft.displayName.trim()
    const contextWindow = readContextWindow()
    fieldErrors.providerId = providers.value.some((item) => item.id === draft.providerId)
      ? ''
      : '请选择所属上游渠道。'
    fieldErrors.upstreamModelName =
      !upstreamModelName || upstreamModelName.length > 255
        ? '上游模型名不能为空，最多 255 个字符。'
        : ''
    fieldErrors.displayName =
      displayName.length > 255 ? '展示名最多 255 个字符。' : ''
    fieldErrors.contextWindow =
      contextWindow === null ? '请填写你确认过的上下文窗口（大于 0 的整数）。' : ''
    saveError.value = ''
    if (Object.values(fieldErrors).some(Boolean)) return
    const body = {
      provider_id: draft.providerId,
      upstream_model_name: upstreamModelName,
      // 留空表示「这条模型没有展示名」，字段照样发出去：省掉它就成了「不修改」，
      // 于是清空展示名这个动作在编辑时永远做不成。
      display_name: displayName,
      context_window: contextWindow as number,
      enabled: draft.enabled,
    }
    try {
      const result = await mutation.mutateAsync(
        editingId.value
          ? { kind: 'update', id: editingId.value, body }
          : // 新建永远不带默认标记：这一页没有「新建即默认」这个动作，目录里还没有默认时
            // 由后端挑（最早添加的那条启用模型）。契约上这个字段是必填的（有非空默认值的
            // 字段在生成类型里都是 required），所以显式给它 false。
            { kind: 'create', body: { ...body, is_default: false } },
      )
      if (disposed) return
      feedback.value = `已${editingId.value ? '更新' : '新增'}可用模型 ${
        result.display_name || result.upstream_model_name
      }。`
      editorOpen.value = false
    } catch (error) {
      if (!disposed) saveError.value = presentError(error)
    }
  }

  async function setEnabled(item: LlmModelDto, enabled: boolean): Promise<void> {
    await command(item.id, { enabled }, `已${enabled ? '启用' : '停用'}可用模型 ${modelLabel(item)}。`)
  }

  async function setDefault(item: LlmModelDto): Promise<void> {
    await command(item.id, { is_default: true }, `已把 ${modelLabel(item)} 设为默认模型。`)
  }

  async function command(
    id: string,
    body: LlmModelUpdateRequest,
    done: string,
  ): Promise<void> {
    if (mutation.isPending.value) return
    feedback.value = ''
    actionError.value = ''
    try {
      await mutation.mutateAsync({ kind: 'update', id, body })
      if (!disposed) feedback.value = done
    } catch (error) {
      if (!disposed) actionError.value = presentError(error)
    }
  }

  return {
    items,
    providers,
    loadError,
    providersError,
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
    setDefault,
  }
}

/** 列表与提示里显示的名字：填过展示名就用它，没填就是上游模型名。 */
export function modelLabel(item: LlmModelDto): string {
  return item.display_name || item.upstream_model_name
}
