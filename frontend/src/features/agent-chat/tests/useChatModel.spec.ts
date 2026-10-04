import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { availableLlmModels } from '@/api/llm-models.fixture'

const api = vi.hoisted(() => ({
  listAvailableLlmModels: vi.fn(),
  updateAgentThreadModel: vi.fn(),
}))

vi.mock('@/api/llm-models', () => ({ listAvailableLlmModels: api.listAvailableLlmModels }))
vi.mock('@/api/agent-threads', () => ({ updateAgentThreadModel: api.updateAgentThreadModel }))

import { useChatModel } from '../composables/useChatModel'

/* 会话里那个模型选择器的行为。spec 0002 的「测试决策」点名要求覆盖**失效态**：当前选的模型
 * 已不可用时显示「原来是 xxx，请重新选择」，而不是静默回落到默认模型；另钉住「选择器每次打开
 * 都重拉目录」（故事 18：刚建好的模型立刻选得到）与「目录还没读到时不乱下结论」。
 *
 * 断言尽量落在可观察结果上：目录里有什么、选择器报不报失效、请求发了几次。 */

const THREAD_ID = '30000000-0000-4000-8000-000000000001'
const OTHER_MODEL_ID = '40000000-0000-4000-8000-000000000009'
const GONE_MODEL_ID = '40000000-0000-4000-8000-0000000000ff'

function mountHarness(getThreadId: () => string | null = () => THREAD_ID) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  let picker!: ReturnType<typeof useChatModel>
  const Host = defineComponent({
    setup() {
      picker = useChatModel({ getThreadId })
      return () => null
    },
  })
  const wrapper = mount(Host, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  return { wrapper, picker, queryClient }
}

beforeEach(() => {
  api.listAvailableLlmModels.mockReset()
  api.updateAgentThreadModel.mockReset()
  api.listAvailableLlmModels.mockResolvedValue(availableLlmModels)
  api.updateAgentThreadModel.mockResolvedValue({ llm_model_id: null })
})

describe('useChatModel', () => {
  it('选中的模型不在刚拉到的目录里时进入失效态，且不改动选择', async () => {
    const { picker } = mountHarness()
    await flushPromises()
    // 打开一个会话：回放里记着的是那条已经被管理员停用的模型
    picker.adopt({ id: GONE_MODEL_ID, displayName: '原来的模型' })

    expect(picker.isChoiceUnavailable.value).toBe(true)
    // 失效不等于换一个：选择保持原样，界面才能显示「原来是 xxx」，而不是悄悄变成默认
    expect(picker.choice.value).toEqual({ id: GONE_MODEL_ID, displayName: '原来的模型' })
    expect(picker.selectedId()).toBe(GONE_MODEL_ID)
  })

  it('选中的模型仍在目录里时不是失效态', async () => {
    const { picker } = mountHarness()
    await flushPromises()
    picker.adopt({ id: availableLlmModels[0].id, displayName: '快速模型' })

    expect(picker.isChoiceUnavailable.value).toBe(false)
  })

  it('目录还没读到时不判失效——读不到目录不等于你选的那个不能用了', async () => {
    // 目录读失败必须在**挂载之前**就摆好：挂载时那次拉取就已经发出去，事后再 mock 已经晚了。
    api.listAvailableLlmModels.mockRejectedValue(new Error('离线'))
    const { picker } = mountHarness()
    await flushPromises()
    picker.adopt({ id: GONE_MODEL_ID, displayName: '原来的模型' })

    expect(picker.catalogError.value).not.toBe('')
    expect(picker.isChoiceUnavailable.value).toBe(false)
  })

  it('选择器每次打开都重新拉目录，刚建好的模型立刻可选', async () => {
    const { picker } = mountHarness()
    await flushPromises()
    const before = api.listAvailableLlmModels.mock.calls.length
    // 管理员刚加了一条，用户此刻打开选择器
    api.listAvailableLlmModels.mockResolvedValue([
      ...availableLlmModels,
      { ...availableLlmModels[0], id: OTHER_MODEL_ID, display_name: '新上的模型' },
    ])

    await picker.refreshCatalog()
    await flushPromises()

    expect(api.listAvailableLlmModels.mock.calls.length).toBeGreaterThan(before)
    expect(picker.availableModels.value.map((item) => item.id)).toContain(OTHER_MODEL_ID)
  })

  it('改选会落到会话行上；保存失败时给出复核提示，而不是默默算数', async () => {
    const { picker } = mountHarness()
    await flushPromises()
    api.updateAgentThreadModel.mockRejectedValue(new Error('写入未确认'))

    await picker.updateChoice({ id: availableLlmModels[0].id, displayName: '快速模型' })
    await flushPromises()

    expect(api.updateAgentThreadModel).toHaveBeenCalledWith(THREAD_ID, availableLlmModels[0].id)
    expect(picker.modelSaveError.value).not.toBeNull()
    expect(picker.savingModel.value).toBe(false)
  })

  it('会话还没建出来时改选只留在本地，不发出保存', async () => {
    const { picker } = mountHarness(() => null)
    await flushPromises()

    await picker.updateChoice({ id: availableLlmModels[0].id, displayName: '快速模型' })

    expect(api.updateAgentThreadModel).not.toHaveBeenCalled()
    expect(picker.selectedId()).toBe(availableLlmModels[0].id)
  })
})
