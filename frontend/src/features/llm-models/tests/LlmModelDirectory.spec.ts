import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { localProvider, openaiProvider } from '@/api/llm-providers.fixture'
import { defaultLlmModel, namelessLlmModel, orphanedLlmModel } from '@/api/llm-models.fixture'

const modelsApi = vi.hoisted(() => ({
  listLlmModels: vi.fn(),
  createLlmModel: vi.fn(),
  updateLlmModel: vi.fn(),
}))
const providersApi = vi.hoisted(() => ({ listLlmProviders: vi.fn() }))

vi.mock('@/api/llm-models', () => modelsApi)
vi.mock('@/api/llm-providers', () => ({
  ...providersApi,
  llmProvidersQueryKey: ['llm-providers', 'management'],
}))

import LlmModelDirectory from '../LlmModelDirectory.vue'

/* 模型目录页面上那几处「只有渲染出来才看得见」的约定：窗口那一栏的提示、没有展示名时显示什么、
 * 以及渠道停用/默认这两档状态在表格里怎么呈现。 */

async function mountDirectory() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const wrapper = mount(LlmModelDirectory, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  for (const fn of Object.values(modelsApi)) fn.mockReset()
  providersApi.listLlmProviders.mockReset()
  modelsApi.listLlmModels.mockResolvedValue([
    defaultLlmModel,
    namelessLlmModel,
    orphanedLlmModel,
  ])
  modelsApi.updateLlmModel.mockResolvedValue(defaultLlmModel)
  providersApi.listLlmProviders.mockResolvedValue([openaiProvider, localProvider])
})

describe('LlmModelDirectory', () => {
  it('窗口那一栏的提示说清要填确认过的最小值，并交代填小了的后果', async () => {
    const wrapper = await mountDirectory()
    await wrapper.get('.toolbar-actions button:last-child').trigger('click')
    await flushPromises()

    const windowField = wrapper
      .findAll('.editor-fields > *')
      .find((field) => field.text().includes('上下文窗口'))
    expect(windowField).toBeDefined()
    const text = windowField!.text()
    expect(text).toContain('填你确认过的最小值')
    expect(text).toContain('历史压缩什么时候触发按它的比例算')
    expect(text).toContain('模型更早只剩下摘要')
  })

  it('没有展示名的那条显示上游模型名，有展示名的把上游名放在下面', async () => {
    const wrapper = await mountDirectory()

    const rows = wrapper.findAll('tbody tr')
    const nameless = rows[1]!
    expect(nameless.get('.name-cell').text()).toBe('qwen2.5:14b')
    const named = rows[0]!
    expect(named.get('.name-cell').text()).toContain('快速模型')
    expect(named.get('.name-cell').text()).toContain('gpt-4o-mini')
  })

  it('渠道停用与默认这两档状态都看得见', async () => {
    const wrapper = await mountDirectory()

    const rows = wrapper.findAll('tbody tr')
    expect(rows[2]!.get('.provider-state').text()).toBe('渠道已停用')
    expect(rows[0]!.get('.default-cell').text()).toBe('默认')
    expect(rows[0]!.find('.status-chip').exists()).toBe(true)
    // 全是默认之外的那两条各给一枚「设为默认」，而不是一个可点的空格子
    expect(rows[1]!.get('.default-cell button').text()).toContain('设为默认')
  })

  it('不可用的模型不能在这里设成默认，禁用原因写在提示里', async () => {
    const wrapper = await mountDirectory()

    const rows = wrapper.findAll('tbody tr')
    // 第二条自己启用、渠道也启用：可以设为默认
    expect(rows[1]!.get('.default-cell button').attributes('disabled')).toBeUndefined()
    // 第三条所属渠道停用：键禁掉，并把原因写在 title 上
    const unavailable = rows[2]!.get('.default-cell button')
    expect(unavailable.attributes('disabled')).toBeDefined()
    expect(unavailable.attributes('title')).toBe('所属渠道已停用，先启用渠道再设为默认。')
  })

  it('窗口留空就地报错，不发请求', async () => {
    const wrapper = await mountDirectory()
    await wrapper.get('.toolbar-actions button:last-child').trigger('click')
    await flushPromises()

    await wrapper.get('input[name="llm-model-upstream-name"]').setValue('qwen2.5:14b')
    await wrapper.get('input[name="llm-model-context-window"]').setValue('')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('请填写你确认过的上下文窗口（大于 0 的整数）。')
    expect(modelsApi.createLlmModel).not.toHaveBeenCalled()
  })
})
