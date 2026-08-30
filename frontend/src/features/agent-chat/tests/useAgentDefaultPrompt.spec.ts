import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ fetchAgentDefaultPrompt: vi.fn() }))

vi.mock('../../../api/agent-chat', () => api)

import { ApiError } from '../../../api/client'
import { useAgentDefaultPrompt } from '../composables/useAgentDefaultPrompt'

/** 仍然挂载组件而不是裸调 composable：onScopeDispose 的取消语义需要真实的 effect scope。 */
function mountHarness() {
  let composable: ReturnType<typeof useAgentDefaultPrompt> | undefined
  const Harness = defineComponent({
    setup() {
      composable = useAgentDefaultPrompt()
      return () => h('div')
    },
  })
  const wrapper = mount(Harness)
  if (!composable) throw new Error('Test harness did not initialize composable')
  return { wrapper, prompt: composable }
}

describe('useAgentDefaultPrompt', () => {
  beforeEach(() => {
    api.fetchAgentDefaultPrompt.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('取到就存下来', async () => {
    api.fetchAgentDefaultPrompt.mockResolvedValue('你是新闻检索助手。')
    const { wrapper, prompt } = mountHarness()

    await prompt.load()

    expect(prompt.defaultPrompt.value).toBe('你是新闻检索助手。')
    wrapper.unmount()
  })

  it('取不到就留 null，不抛给调用方', async () => {
    api.fetchAgentDefaultPrompt.mockRejectedValue(
      new ApiError({ message: 'nope', code: 'agent_runtime_unavailable', status: 503 }),
    )
    const { wrapper, prompt } = mountHarness()

    // 对话本身不需要它（不传 system_prompt 时后端用同一份默认值），所以失败不该阻断进入页面。
    await expect(prompt.load()).resolves.toBeUndefined()
    expect(prompt.defaultPrompt.value).toBeNull()
    wrapper.unmount()
  })

  it('卸载后不再写 ref，被取消的请求不覆盖状态', async () => {
    let settle: ((value: string) => void) | undefined
    api.fetchAgentDefaultPrompt.mockReturnValue(
      new Promise<string>((resolve) => (settle = resolve)),
    )
    const { wrapper, prompt } = mountHarness()

    const loading = prompt.load()
    wrapper.unmount()
    settle?.('迟到的提示词')
    await loading
    await flushPromises()

    // 请求确实带上了会在卸载时中止的信号。
    expect(api.fetchAgentDefaultPrompt.mock.calls[0]?.[0]).toBeInstanceOf(AbortSignal)
    expect(api.fetchAgentDefaultPrompt.mock.calls[0]?.[0]?.aborted).toBe(true)
  })
})
