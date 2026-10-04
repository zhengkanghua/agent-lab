import { defineComponent } from 'vue'
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

import { ApiError } from '@/api/client'
import { useLlmModels } from '../useLlmModels'

/* 可用模型目录的行为。这里钉的是可观察结果与真实失败风险：
 * 上下文窗口是必填的正整数（空、0、小数都在本地拦住，不替用户补默认值）、
 * 编辑提交不带 is_default（否则改个名字可能顺手把默认取消掉）、
 * 以及「设为默认」是唯一动默认标记的那条命令。 */

function mountHarness() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  let directory!: ReturnType<typeof useLlmModels>
  const Host = defineComponent({
    setup() {
      directory = useLlmModels()
      return () => null
    },
  })
  const wrapper = mount(Host, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  return { wrapper, directory, queryClient }
}

beforeEach(() => {
  for (const fn of Object.values(modelsApi)) fn.mockReset()
  providersApi.listLlmProviders.mockReset()
  modelsApi.listLlmModels.mockResolvedValue([
    defaultLlmModel,
    namelessLlmModel,
    orphanedLlmModel,
  ])
  modelsApi.createLlmModel.mockResolvedValue(namelessLlmModel)
  modelsApi.updateLlmModel.mockResolvedValue(defaultLlmModel)
  providersApi.listLlmProviders.mockResolvedValue([openaiProvider, localProvider])
})

describe('useLlmModels', () => {
  it('按后端返回的顺序展示，并把渠道目录读进来给「所属渠道」用', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    expect(directory.items.value.map((item) => item.upstream_model_name)).toEqual([
      'gpt-4o-mini',
      'qwen2.5:14b',
      'llama3.1:8b',
    ])
    expect(directory.providers.value.map((provider) => provider.name)).toEqual([
      '主中转站',
      '本地 Ollama',
    ])
    expect(providersApi.listLlmProviders).toHaveBeenCalled()

    modelsApi.listLlmModels.mockRejectedValue(
      new ApiError({ message: '挂了', code: 'llm_catalog_database_unavailable', status: 503 }),
    )
    await directory.refresh()
    expect(directory.loadError.value).toBe('模型目录服务暂时不可用，请稍后重试。')
  })

  it('新建时预填 32768 并默认选第一条渠道，编辑时按那一行预填', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    directory.openEditor()
    expect(directory.editorOpen.value).toBe(true)
    expect(directory.editingId.value).toBeNull()
    expect(directory.draft).toEqual({
      providerId: openaiProvider.id,
      upstreamModelName: '',
      displayName: '',
      contextWindow: '32768',
      enabled: true,
    })

    directory.openEditor(namelessLlmModel)
    expect(directory.editingId.value).toBe(namelessLlmModel.id)
    expect(directory.draft).toMatchObject({
      providerId: namelessLlmModel.provider_id,
      upstreamModelName: 'qwen2.5:14b',
      // 没有展示名的那条预填成空：界面里那是一个空输入框，不是「不修改」。
      displayName: '',
      contextWindow: 131072,
      enabled: true,
    })
  })

  it('窗口留空或不是正整数时本地拦住，不替用户补一个数', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    directory.openEditor()
    directory.draft.upstreamModelName = 'gpt-4o-mini'

    for (const blank of ['', 0, -1, 12.5, 'abc'] as const) {
      directory.draft.contextWindow = blank
      await directory.submit()
      expect(directory.fieldErrors.contextWindow).not.toBe('')
      expect(modelsApi.createLlmModel).not.toHaveBeenCalled()
    }
  })

  it('所属渠道与上游模型名都是硬要求', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    directory.openEditor()

    directory.draft.providerId = ''
    directory.draft.upstreamModelName = '   '
    await directory.submit()

    expect(directory.fieldErrors.providerId).not.toBe('')
    expect(directory.fieldErrors.upstreamModelName).not.toBe('')
    expect(modelsApi.createLlmModel).not.toHaveBeenCalled()
  })

  it('新建成功后重取整表，并回报是哪一条', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    const before = modelsApi.listLlmModels.mock.calls.length

    directory.openEditor()
    directory.draft.upstreamModelName = 'qwen2.5:14b'
    directory.draft.displayName = ''
    directory.draft.contextWindow = 131072
    await directory.submit()

    expect(modelsApi.createLlmModel).toHaveBeenCalledWith({
      provider_id: openaiProvider.id,
      upstream_model_name: 'qwen2.5:14b',
      // 空展示名照样发出去：它不是「不修改」，而是「这条没有展示名」。
      display_name: '',
      context_window: 131072,
      // 新建不从这一页打默认标记：目录里还没有默认时由后端挑最早添加的那条。
      is_default: false,
      enabled: true,
    })
    expect(directory.editorOpen.value).toBe(false)
    expect(directory.feedback.value).toContain('qwen2.5:14b')
    // 默认可能落到别的行上（设为默认/启停都会改两行），所以整表重取而不是就地改一行。
    await flushPromises()
    expect(modelsApi.listLlmModels.mock.calls.length).toBeGreaterThan(before)
  })

  it('编辑提交不带 is_default，也不动物业渠道以外的字段', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    directory.openEditor(defaultLlmModel)
    directory.draft.displayName = ''
    directory.draft.contextWindow = 65536
    await directory.submit()

    expect(modelsApi.updateLlmModel).toHaveBeenCalledWith(defaultLlmModel.id, {
      provider_id: defaultLlmModel.provider_id,
      upstream_model_name: 'gpt-4o-mini',
      display_name: '',
      context_window: 65536,
      enabled: true,
    })
  })

  it('「设为默认」只发 is_default，启停只发 enabled', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    await directory.setDefault(namelessLlmModel)
    expect(modelsApi.updateLlmModel).toHaveBeenLastCalledWith(namelessLlmModel.id, {
      is_default: true,
    })
    expect(directory.feedback.value).toContain('已把 qwen2.5:14b 设为默认模型。')

    await directory.setEnabled(namelessLlmModel, false)
    expect(modelsApi.updateLlmModel).toHaveBeenLastCalledWith(namelessLlmModel.id, {
      enabled: false,
    })
  })

  it('后端拒绝时给一句能照做的话，且不显示成功', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    modelsApi.updateLlmModel.mockRejectedValue(
      new ApiError({
        message: 'raw database text',
        code: 'llm_model_default_cannot_be_disabled',
        status: 409,
      }),
    )
    await directory.setEnabled(defaultLlmModel, false)

    expect(directory.actionError.value).toBe(
      '这条模型是当前默认，请先把默认换到另一条模型再停用它。',
    )
    expect(directory.feedback.value).toBe('')

    modelsApi.updateLlmModel.mockRejectedValue(
      new ApiError({
        message: 'raw database text',
        code: 'llm_model_default_conflict',
        status: 409,
      }),
    )
    await directory.setDefault(namelessLlmModel)
    expect(directory.actionError.value).toBe('另一个请求刚刚改过默认模型，请刷新列表后重试。')
  })

  it('保存被后端拒绝时错误落在表单上，表单不关闭', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    modelsApi.createLlmModel.mockRejectedValue(
      new ApiError({ message: 'x', code: 'llm_model_name_conflict', status: 409 }),
    )
    directory.openEditor()
    directory.draft.upstreamModelName = 'gpt-4o-mini'
    await directory.submit()

    expect(directory.saveError.value).toBe(
      '这条渠道下已经有同名的上游模型，请换一个上游模型名。',
    )
    expect(directory.editorOpen.value).toBe(true)
  })
})
