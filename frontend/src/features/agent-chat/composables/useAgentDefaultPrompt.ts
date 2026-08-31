import { onScopeDispose, ref } from 'vue'
import { fetchAgentDefaultPrompt } from '@/api/agent-chat'
import { isAbortError } from '@/api/client'

/**
 * 取一次默认系统提示词，用于把自定义提示词输入框预填成可编辑的起点。
 *
 * 取不到就留成 null，界面上只是「填入默认提示词」按钮不可用——对话本身不需要它（不传
 * system_prompt 时后端会用同一份默认值），所以这次请求失败不该阻断进入页面。
 */
export function useAgentDefaultPrompt() {
  const defaultPrompt = ref<string | null>(null)
  const controller = new AbortController()

  async function load(): Promise<void> {
    try {
      defaultPrompt.value = await fetchAgentDefaultPrompt(controller.signal)
    } catch (error) {
      if (isAbortError(error)) return
      defaultPrompt.value = null
    }
  }

  onScopeDispose(() => controller.abort())

  return { defaultPrompt, load }
}
