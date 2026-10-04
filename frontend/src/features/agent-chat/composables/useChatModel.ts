import { computed, ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { listAvailableLlmModels, type AvailableLlmModelDto } from '@/api/llm-models'
import { updateAgentThreadModel } from '@/api/agent-threads'

/* 会话的模型选择：当前选择 + 逐次保存 + 可选目录。
 *
 * 从 useAgentChat 里分出来的一块，与 useChatScope 同形。它需要的全部外部信息就是「现在是哪个
 * 会话」——用 getter 要而不是传 ref，因为 id 由流（run_started）与打开会话两条路写入，这个
 * 组合式函数只读不写。
 *
 * 两条与知识库选择不同的地方：
 *
 * - **目录在这里**，而且是每次打开选择器就重拉。会话列表与选择器加载后是有缓存的，不重拉的话
 *   用户会看到一个刚下线的模型、或者刚建的模型迟迟选不到。
 * - **失效态是算出来的**：存着的那个 id 不在刚拉到的可选目录里，就是「已停用或不存在」。
 *   界面据此显示「原来是 xxx，该模型已不可用，请重新选择」，**不静默回落到默认模型**。
 */

/** 会话当前选择在界面上的样子：id 连它此刻的名字。 */
export interface ChatModelChoice {
  id: string
  /** 名字可能为空：目录里已经查不到这一条时，服务端只回得出 id。 */
  displayName: string | null
}

export interface UseChatModelOptions {
  /** 当前会话 id；为 null 时改选只留在本地，等会话建出来再保存。 */
  getThreadId: () => string | null
  /** 保存实现，测试注入。 */
  saveModel?: typeof updateAgentThreadModel
}

export function useChatModel({
  getThreadId,
  saveModel = updateAgentThreadModel,
}: UseChatModelOptions) {
  // 可选目录：任何已登录账号都能读，只含「自身启用且所属渠道也启用」的条目。
  // staleTime 为 0 是刻意的：这个列表背后是管理员随时能改的配置，缓存久了就会选到一个
  // 已经下线的模型。真正的拉取时机由选择器打开时那次 refresh 决定（见 refreshCatalog）。
  const query = useQuery({
    queryKey: ['llm-models', 'available'],
    queryFn: ({ signal }) => listAvailableLlmModels(signal),
    retry: false,
    staleTime: 0,
  })
  const availableModels = computed<AvailableLlmModelDto[]>(() => query.data.value ?? [])
  const catalogLoading = computed(() => query.isPending.value)
  const catalogError = computed(() =>
    query.error.value ? '模型目录加载失败，请重新加载。' : '',
  )
  const choice = ref<ChatModelChoice | null>(null)
  const savingModel = ref(false)
  const modelSaveError = ref<string | null>(null)
  /**
   * 编辑版本。每次改选与每次换会话都前进一格，用来判断「这个响应回来时，用户是不是已经
   * 又改过、或者已经换了会话」——这类陈旧写入不能盖掉新的选择。
   */
  let modelEditVersion = 0
  /** 保存串成一条链，避免两次快速改选的请求乱序落地。 */
  let pendingModelSave: Promise<void> = Promise.resolve()

  /**
   * 会话里存着的选择现在还可选吗。
   *
   * 判据是「它有没有出现在刚拉到的目录里」——目录只回可用的条目，所以不在里面就等于已停用、
   * 所属渠道停用、或已经不存在。目录还没读到（首次加载中或读失败）时**不下结论**：把读不到
   * 目录说成「你选的那个不能用了」会让用户去换一个其实好好的模型。
   */
  const isChoiceUnavailable = computed(
    () =>
      choice.value !== null &&
      query.data.value !== undefined &&
      !availableModels.value.some((item) => item.id === choice.value?.id),
  )

  /** 逐次保存有效选择，防止快速改选的旧请求晚到后覆盖新选择。 */
  function persistSelection(
    targetThreadId: string,
    modelId: string,
    version: number,
  ): Promise<void> {
    savingModel.value = true
    pendingModelSave = pendingModelSave.then(async () => {
      try {
        await saveModel(targetThreadId, modelId)
        if (version === modelEditVersion && getThreadId() === targetThreadId)
          modelSaveError.value = null
      } catch {
        if (version === modelEditVersion && getThreadId() === targetThreadId)
          modelSaveError.value = '模型选择保存未确认；请重新选择，或重新打开会话核对。'
      } finally {
        if (version === modelEditVersion) savingModel.value = false
      }
    })
    return pendingModelSave
  }

  function updateChoice(value: ChatModelChoice): Promise<void> {
    choice.value = { id: value.id, displayName: value.displayName }
    modelEditVersion += 1
    modelSaveError.value = null
    savingModel.value = false
    const target = getThreadId()
    return target ? persistSelection(target, value.id, modelEditVersion) : Promise.resolve()
  }

  /**
   * 换会话时的复位：作废在途保存、清掉错误，但**不动当前选择**——打开会话时那个选择由回放
   * 决定（见 adopt），先复位成一个中间值会让选择器闪一下。
   */
  function reset(): void {
    modelEditVersion += 1
    savingModel.value = false
    modelSaveError.value = null
  }

  /** 直接落一个选择（打开会话时用回放里的那份；开新会话时回「没选过」）。不触发保存。 */
  function adopt(value: ChatModelChoice | null): void {
    choice.value = value ? { id: value.id, displayName: value.displayName } : null
  }

  /** 当前编辑版本。提问开始时读一次，用来判断这一轮跑完后选择是否又被改过。 */
  function editVersion(): number {
    return modelEditVersion
  }

  /** 当前选择对应的 id；没选过时为空，请求里不带它，服务端就用默认模型。 */
  function selectedId(): string | null {
    return choice.value?.id ?? null
  }

  /** 重新拉目录。选择器每次打开都调它一次（故事 18：刚建好的模型立刻能选到）。 */
  function refreshCatalog(): Promise<unknown> {
    return query.refetch()
  }

  return {
    choice,
    availableModels,
    catalogLoading,
    catalogError,
    isChoiceUnavailable,
    savingModel,
    modelSaveError,
    updateChoice,
    persistSelection,
    editVersion,
    selectedId,
    reset,
    adopt,
    refreshCatalog,
  }
}
