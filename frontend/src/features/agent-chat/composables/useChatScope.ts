import { ref } from 'vue'
import { updateAgentThreadScope } from '@/api/agent-threads'
import { copySelection, isSelection, type KnowledgeBaseSelection } from '@/api/knowledge-scope'

/* 会话的知识库范围：当前选择 + 逐次保存。
 *
 * 从 useAgentChat 里分出来的一块。它需要的全部外部信息就是「现在是哪个会话」——用 getter 要
 * 而不是传 ref，因为 id 由流（run_started）与打开会话两条路写入，这个组合式函数只读不写。
 */

export interface UseChatScopeOptions {
  /** 当前会话 id；为 null 时改选只留在本地，等会话建出来再保存。 */
  getThreadId: () => string | null
  /** 保存实现，测试注入。 */
  saveScope?: typeof updateAgentThreadScope
}

export function useChatScope({
  getThreadId,
  saveScope = updateAgentThreadScope,
}: UseChatScopeOptions) {
  const selection = ref<KnowledgeBaseSelection>({ mode: 'all' })
  const savingScope = ref(false)
  const scopeSaveError = ref<string | null>(null)
  /**
   * 编辑版本。每次改选与每次换会话都前进一格，用来判断「这个响应回来时，用户是不是已经
   * 又改过、或者已经换了会话」——这类陈旧写入不能盖掉新的选择。
   */
  let scopeEditVersion = 0
  /** 保存串成一条链，避免两次快速改选的请求乱序落地。 */
  let pendingScopeSave: Promise<void> = Promise.resolve()

  /** 逐次保存有效选择，防止快速改选的旧请求晚到后覆盖新选择。 */
  function persistSelection(
    targetThreadId: string,
    value: KnowledgeBaseSelection,
    version: number,
  ): Promise<void> {
    if (!isSelection(value)) return Promise.resolve()
    savingScope.value = true
    pendingScopeSave = pendingScopeSave.then(async () => {
      try {
        await saveScope(targetThreadId, value)
        if (version === scopeEditVersion && getThreadId() === targetThreadId)
          scopeSaveError.value = null
      } catch {
        if (version === scopeEditVersion && getThreadId() === targetThreadId)
          scopeSaveError.value = '范围保存未确认；请重新选择，或重新打开会话核对。'
      } finally {
        if (version === scopeEditVersion) savingScope.value = false
      }
    })
    return pendingScopeSave
  }

  function updateSelection(value: KnowledgeBaseSelection): Promise<void> {
    selection.value = copySelection(value)
    scopeEditVersion += 1
    scopeSaveError.value = null
    savingScope.value = false
    const target = getThreadId()
    return target
      ? persistSelection(target, copySelection(value), scopeEditVersion)
      : Promise.resolve()
  }

  /**
   * 换会话时的复位：作废在途保存、清掉错误，但**不动当前选择**——打开会话时那个选择由回放
   * 决定（见 adopt），先复位成一个中间值会让范围选择器闪一下。
   */
  function reset(): void {
    scopeEditVersion += 1
    savingScope.value = false
    scopeSaveError.value = null
  }

  /** 直接落一个选择（打开会话时用回放里的范围；开新会话时回默认）。不触发保存。 */
  function adopt(value: KnowledgeBaseSelection): void {
    selection.value = copySelection(value)
  }

  /** 当前编辑版本。提问开始时读一次，用来判断这一轮跑完后选择是否又被改过。 */
  function editVersion(): number {
    return scopeEditVersion
  }

  return {
    selection,
    savingScope,
    scopeSaveError,
    updateSelection,
    persistSelection,
    editVersion,
    reset,
    adopt,
  }
}
