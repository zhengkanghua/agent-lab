import { onScopeDispose, ref } from 'vue'
import { fetchAgentDefaultPrompt } from '@/api/agent-chat'
import { isAbortError } from '@/api/client'

/**
 * 取一次服务端默认系统提示词，作为设置页编辑器的「填入默认」来源。
 *
 * 取不到就留成 null，界面上只是「填入默认提示词」按钮不可用——对话本身不需要它（不传
 * system_prompt 时后端会用同一份默认值），所以这次请求失败不该阻断进入页面。
 *
 * 从 features/agent-chat 迁来：自定义提示词的编辑与展示都归设置中心后，它的唯一消费方
 * 在这里；feature 之间禁止互相导入，composable 跟着消费方走。
 */
export function useDefaultAgentPrompt() {
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
