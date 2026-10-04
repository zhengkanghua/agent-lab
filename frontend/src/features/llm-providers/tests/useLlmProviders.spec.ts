import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { localProvider, openaiProvider } from '@/api/llm-providers.fixture'

const api = vi.hoisted(() => ({
  listLlmProviders: vi.fn(),
  createLlmProvider: vi.fn(),
  updateLlmProvider: vi.fn(),
}))

// 整份 mock 掉这个模块意味着常量也得手写一份：缓存身份由 useLlmProviders 从这里读。
vi.mock('@/api/llm-providers', () => ({
  ...api,
  llmProvidersQueryKey: ['llm-providers', 'management'],
}))

import { ApiError } from '@/api/client'
import { useLlmProviders } from '../useLlmProviders'

/* 上游渠道目录的行为。这里钉的是可观察结果与真实失败风险：
 * 凭据「留空 = 不改」（既不预填、也不进请求体，更不是清空）、
 * 「改成需要凭据的接入类型却没补凭据」在本地就被拦住、
 * 以及错误码翻成人话、成功后才写缓存。 */

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => {
    resolve = settle
  })
  return { promise, resolve }
}

function mountHarness() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  let directory!: ReturnType<typeof useLlmProviders>
  const Host = defineComponent({
    setup() {
      directory = useLlmProviders()
      return () => null
    },
  })
  const wrapper = mount(Host, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  return { wrapper, directory, queryClient }
}

beforeEach(() => {
  api.listLlmProviders.mockReset()
  api.createLlmProvider.mockReset()
  api.updateLlmProvider.mockReset()
  api.listLlmProviders.mockResolvedValue([openaiProvider, localProvider])
})

describe('useLlmProviders', () => {
  it('按后端返回的顺序展示，读失败给可显示的文案', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    expect(directory.items.value.map((item) => item.name)).toEqual(['主中转站', '本地 Ollama'])
    expect(directory.loadError.value).toBe('')

    api.listLlmProviders.mockRejectedValue(
      new ApiError({ message: '挂了', code: 'llm_catalog_database_unavailable', status: 503 }),
    )
    await directory.refresh()
    expect(directory.loadError.value).toBe('模型目录服务暂时不可用，请稍后重试。')
  })

  it('新增时表单为空且默认需要凭据，编辑时按那一行预填但不预填凭据', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    directory.openEditor()
    expect(directory.editorOpen.value).toBe(true)
    expect(directory.editingId.value).toBeNull()
    expect(directory.draft).toEqual({
      name: '',
      provider: 'openai_compatible',
      baseUrl: '',
      credential: '',
      enabled: true,
    })

    directory.openEditor(openaiProvider)
    expect(directory.editingId.value).toBe(openaiProvider.id)
    expect(directory.draft).toMatchObject({
      name: '主中转站',
      provider: 'openai_compatible',
      baseUrl: 'https://api.example.com/v1',
      // 已存的凭据拿不回来，也不预填：输入框永远是空的。
      credential: '',
      enabled: true,
    })
  })

  it('新增时凭据与地址不合法就地拦住，不发请求', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    directory.openEditor()
    directory.draft.name = '主中转站'
    directory.draft.baseUrl = 'api.example.com/v1'
    await directory.submit()
    expect(directory.fieldErrors.baseUrl).not.toBe('')
    expect(directory.fieldErrors.credential).not.toBe('')
    expect(api.createLlmProvider).not.toHaveBeenCalled()

    // 换成不需要凭据的接入类型后就可以不填凭据；地址仍然是硬要求。
    directory.draft.provider = 'ollama'
    directory.draft.baseUrl = 'http://127.0.0.1:11434'
    api.createLlmProvider.mockResolvedValue({
      ...localProvider,
      id: '30000000-0000-4000-8000-000000000003',
      name: '主中转站',
      enabled: true,
    })
    await directory.submit()
    expect(api.createLlmProvider).toHaveBeenCalledWith({
      name: '主中转站',
      provider: 'ollama',
      base_url: 'http://127.0.0.1:11434',
      enabled: true,
    })
  })

  it('编辑时凭据留空不改动原凭据：请求体里没有这个字段', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.updateLlmProvider.mockResolvedValue({ ...openaiProvider, name: '主中转站（改）' })

    directory.openEditor(openaiProvider)
    directory.draft.name = '主中转站（改）'
    await directory.submit()

    expect(api.updateLlmProvider).toHaveBeenCalledWith(openaiProvider.id, {
      name: '主中转站（改）',
      provider: 'openai_compatible',
      base_url: 'https://api.example.com/v1',
      enabled: true,
    })
    expect(directory.feedback.value).toBe('已更新上游渠道 主中转站（改）。')
    expect(directory.editorOpen.value).toBe(false)
  })

  it('编辑时填了凭据就带上它', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.updateLlmProvider.mockResolvedValue(openaiProvider)

    directory.openEditor(openaiProvider)
    directory.draft.credential = 'sk-新凭据'
    await directory.submit()

    expect(api.updateLlmProvider).toHaveBeenCalledWith(openaiProvider.id, {
      name: '主中转站',
      provider: 'openai_compatible',
      base_url: 'https://api.example.com/v1',
      enabled: true,
      credential: 'sk-新凭据',
    })
  })

  it('把没有凭据的渠道改成需要凭据的接入类型、又没补凭据 → 本地拦住', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    directory.openEditor(localProvider)
    directory.draft.provider = 'openai_compatible'
    await directory.submit()

    expect(directory.fieldErrors.credential).toBe('该接入类型必须配置凭据，请填写凭据后再保存。')
    expect(api.updateLlmProvider).not.toHaveBeenCalled()

    // 同一次保存里补上凭据就能过。
    directory.draft.credential = 'sk-新凭据'
    api.updateLlmProvider.mockResolvedValue({ ...localProvider, provider: 'openai_compatible' })
    await directory.submit()
    expect(api.updateLlmProvider).toHaveBeenCalledOnce()
    expect(api.updateLlmProvider).toHaveBeenCalledWith(localProvider.id, {
      name: '本地 Ollama',
      provider: 'openai_compatible',
      base_url: 'http://127.0.0.1:11434',
      enabled: false,
      credential: 'sk-新凭据',
    })
  })

  it('新建成功后追加到列表末尾', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.createLlmProvider.mockResolvedValue({
      ...localProvider,
      id: '30000000-0000-4000-8000-000000000003',
      name: '备用本地',
    })

    directory.openEditor()
    directory.draft.name = '备用本地'
    directory.draft.provider = 'ollama'
    directory.draft.baseUrl = 'http://127.0.0.1:11434'
    await directory.submit()

    expect(directory.items.value.map((item) => item.name)).toEqual([
      '主中转站',
      '本地 Ollama',
      '备用本地',
    ])
  })

  it('启停切换成功后按新状态原地改写那一行并给出反馈', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.updateLlmProvider.mockResolvedValue({ ...openaiProvider, enabled: false })

    await directory.setEnabled(openaiProvider, false)

    expect(api.updateLlmProvider).toHaveBeenCalledWith(openaiProvider.id, { enabled: false })
    expect(directory.feedback.value).toBe('已停用上游渠道 主中转站。')
    expect(directory.items.value[0]?.enabled).toBe(false)
  })

  it('保存失败按错误码翻成人话，且不动列表', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.updateLlmProvider.mockRejectedValue(
      new ApiError({
        message: 'x',
        code: 'llm_catalog_unavailable',
        status: 503,
      }),
    )
    const before = directory.items.value.map((item) => item.id)

    directory.openEditor(openaiProvider)
    directory.draft.credential = 'sk-新凭据'
    await directory.submit()

    expect(directory.saveError.value).toBe(
      '渠道凭据的加密密钥未配置，请联系部署方配置 LLM_CREDENTIAL_KEY 后重试。',
    )
    expect(directory.editorOpen.value).toBe(true)
    expect(directory.items.value.map((item) => item.id)).toEqual(before)
  })

  it('启停失败时错误落在操作提示里，不落在编辑器上', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.updateLlmProvider.mockRejectedValue(
      new ApiError({ message: 'x', code: 'llm_provider_not_found', status: 404 }),
    )

    await directory.setEnabled(openaiProvider, false)

    expect(directory.actionError.value).toBe('这条上游渠道已不存在，请刷新列表后重试。')
    expect(directory.saveError.value).toBe('')
  })

  it('请求在途时不重入：连点保存与启停都只发一次', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    const pending = deferred<typeof openaiProvider>()
    api.updateLlmProvider.mockReturnValue(pending.promise)

    const first = directory.setEnabled(openaiProvider, false)
    await directory.setEnabled(openaiProvider, true)
    // mutateAsync 内部还要先 await 一次 cancelQueries，所以请求本身晚一个微任务才发出。
    await flushPromises()
    expect(api.updateLlmProvider).toHaveBeenCalledTimes(1)

    directory.openEditor(openaiProvider)
    expect(directory.editorOpen.value).toBe(false)

    pending.resolve({ ...openaiProvider, enabled: false })
    await first
  })

  it('离开页面之后到达的响应不写回缓存，避免污染下一个账号', async () => {
    const { directory, wrapper, queryClient } = mountHarness()
    await flushPromises()
    const pending = deferred<typeof openaiProvider>()
    api.createLlmProvider.mockReturnValue(pending.promise)

    directory.openEditor()
    directory.draft.name = '迟到的渠道'
    directory.draft.provider = 'ollama'
    directory.draft.baseUrl = 'http://127.0.0.1:11434'
    const saving = directory.submit()

    wrapper.unmount()
    pending.resolve({ ...localProvider, id: 'late', name: '迟到的渠道' })
    await saving
    await flushPromises()

    const cached = queryClient.getQueryData<{ name: string }[]>(['llm-providers', 'management'])
    expect(cached?.some((item) => item.name === '迟到的渠道')).not.toBe(true)
  })
})
