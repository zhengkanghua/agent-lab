import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { newsKnowledgeBase, techKnowledgeBase } from '@/api/knowledge-bases.fixture'

const api = vi.hoisted(() => ({
  listKnowledgeBases: vi.fn(),
  createKnowledgeBase: vi.fn(),
  updateKnowledgeBase: vi.fn(),
}))

vi.mock('@/api/knowledge-bases', () => api)

import { ApiError } from '@/api/client'
import { useKnowledgeBases } from '../useKnowledgeBases'

/* 知识库配置目录的行为。这个 feature 此前零测试；这里钉的是可观察结果与真实失败风险：
 * 校验拦住不该发的请求、错误码翻成人话、成功后才写缓存、以及「离开页面后的写回不落地」。 */

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
  let directory!: ReturnType<typeof useKnowledgeBases>
  const Host = defineComponent({
    setup() {
      directory = useKnowledgeBases()
      return () => null
    },
  })
  const wrapper = mount(Host, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  return { wrapper, directory, queryClient }
}

beforeEach(() => {
  api.listKnowledgeBases.mockReset()
  api.createKnowledgeBase.mockReset()
  api.updateKnowledgeBase.mockReset()
  api.listKnowledgeBases.mockResolvedValue([techKnowledgeBase, newsKnowledgeBase])
})

describe('useKnowledgeBases', () => {
  it('读取列表时按后端返回的顺序展示，读失败给可显示的文案', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    expect(directory.items.value.map((item) => item.key)).toEqual(['tech-notes', 'news'])
    expect(directory.loadError.value).toBe('')

    api.listKnowledgeBases.mockRejectedValue(
      new ApiError({ message: '挂了', code: 'knowledge_base_unavailable', status: 503 }),
    )
    await directory.refresh()
    expect(directory.loadError.value).toBe('知识库服务暂时不可用，请稍后重试。')
  })

  it('新建时预置空表单，编辑时按那一行预填', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    directory.openEditor()
    expect(directory.editorOpen.value).toBe(true)
    expect(directory.editingId.value).toBeNull()
    expect(directory.draft).toEqual({ key: '', name: '', description: '', isActive: true })

    directory.openEditor(newsKnowledgeBase)
    expect(directory.editingId.value).toBe(newsKnowledgeBase.id)
    expect(directory.draft).toEqual({
      key: 'news',
      name: '新闻',
      description: '每日新闻与行业动态',
      isActive: true,
    })
  })

  it('稳定键不合规时不发请求，且只在新建时校验它', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    directory.openEditor()
    directory.draft.key = 'Bad Key'
    directory.draft.name = '示例'
    await directory.submit()
    expect(directory.fieldErrors.key).not.toBe('')
    expect(api.createKnowledgeBase).not.toHaveBeenCalled()

    // 编辑时稳定键不可改，历史遗留的键（例如带大写）不该拦住一次改名。
    directory.draft.key = ''
    directory.openEditor(newsKnowledgeBase)
    directory.draft.key = 'Old-Key'
    directory.draft.name = '新闻（改）'
    api.updateKnowledgeBase.mockResolvedValue({ ...newsKnowledgeBase, name: '新闻（改）' })
    await directory.submit()
    expect(api.updateKnowledgeBase).toHaveBeenCalledOnce()
  })

  it('名称与说明的长度上限就地拦住', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    directory.openEditor()

    directory.draft.key = 'fresh'
    directory.draft.name = ''
    await directory.submit()
    expect(directory.fieldErrors.name).not.toBe('')

    directory.draft.name = 'x'.repeat(256)
    await directory.submit()
    expect(directory.fieldErrors.name).not.toBe('')

    directory.draft.name = '可用名称'
    directory.draft.description = 'y'.repeat(2001)
    await directory.submit()
    expect(directory.fieldErrors.description).not.toBe('')
    expect(api.createKnowledgeBase).not.toHaveBeenCalled()
  })

  it('新建成功后写回缓存并按稳定键排序，同时关闭编辑器', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.createKnowledgeBase.mockResolvedValue({
      ...newsKnowledgeBase,
      id: '10000000-0000-4000-8000-000000000012',
      key: 'alpha',
      name: '新库',
      description: null,
    })

    directory.openEditor()
    directory.draft.key = 'alpha'
    directory.draft.name = '新库'
    await directory.submit()

    expect(api.createKnowledgeBase).toHaveBeenCalledWith({
      key: 'alpha',
      name: '新库',
      description: null,
      is_active: true,
    })
    expect(directory.feedback.value).toBe('已创建知识库 新库。')
    expect(directory.editorOpen.value).toBe(false)
    expect(directory.items.value.map((item) => item.key)).toEqual(['alpha', 'news', 'tech-notes'])
  })

  it('编辑提交只发 name 与 description，不夹带稳定键与启停状态', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.updateKnowledgeBase.mockResolvedValue({ ...techKnowledgeBase, name: '技术资料（改）' })

    directory.openEditor(techKnowledgeBase)
    directory.draft.name = '技术资料（改）'
    directory.draft.description = ''
    await directory.submit()

    expect(api.updateKnowledgeBase).toHaveBeenCalledWith(techKnowledgeBase.id, {
      name: '技术资料（改）',
      description: null,
    })
    expect(directory.feedback.value).toBe('已更新知识库 技术资料（改）。')
  })

  it('保存失败按错误码翻成人话，且不动列表', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.createKnowledgeBase.mockRejectedValue(
      new ApiError({ message: 'conflict', code: 'knowledge_base_key_conflict', status: 409 }),
    )
    const before = directory.items.value.map((item) => item.id)

    directory.openEditor()
    directory.draft.key = 'news'
    directory.draft.name = '重复的键'
    await directory.submit()

    expect(directory.saveError.value).toBe('该稳定键已被使用，请换一个。')
    expect(directory.editorOpen.value).toBe(true)
    expect(directory.items.value.map((item) => item.id)).toEqual(before)
  })

  it('启停切换成功后按新状态改写那一行并给出反馈', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.updateKnowledgeBase.mockResolvedValue({ ...newsKnowledgeBase, is_active: false })

    await directory.setActive(newsKnowledgeBase, false)

    expect(api.updateKnowledgeBase).toHaveBeenCalledWith(newsKnowledgeBase.id, { is_active: false })
    expect(directory.feedback.value).toBe('已停用知识库 新闻。')
    expect(directory.items.value.find((item) => item.id === newsKnowledgeBase.id)?.is_active).toBe(
      false,
    )
  })

  it('启停失败时错误落在操作提示里，不落在编辑器上', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    api.updateKnowledgeBase.mockRejectedValue(
      new ApiError({ message: 'x', code: 'invalid_request', status: 422 }),
    )

    await directory.setActive(newsKnowledgeBase, false)

    expect(directory.actionError.value).toBe('请检查名称、稳定键和说明后重试。')
    expect(directory.saveError.value).toBe('')
  })

  it('请求在途时不重入：连点保存与启停都只发一次', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    const pending = deferred<typeof newsKnowledgeBase>()
    api.updateKnowledgeBase.mockReturnValue(pending.promise)

    const first = directory.setActive(newsKnowledgeBase, false)
    await directory.setActive(newsKnowledgeBase, true)
    // mutateAsync 内部还要先 await 一次 cancelQueries，所以请求本身晚一个微任务才发出。
    await flushPromises()
    expect(api.updateKnowledgeBase).toHaveBeenCalledTimes(1)

    // 在途时也不该打开另一个编辑器（会覆盖正在提交的那份草稿）。
    directory.openEditor(newsKnowledgeBase)
    expect(directory.editorOpen.value).toBe(false)

    pending.resolve({ ...newsKnowledgeBase, is_active: false })
    await first
  })

  it('离开页面之后到达的响应不写回缓存，避免污染下一个账号', async () => {
    const { directory, wrapper, queryClient } = mountHarness()
    await flushPromises()
    const pending = deferred<typeof newsKnowledgeBase>()
    api.createKnowledgeBase.mockReturnValue(pending.promise)

    directory.openEditor()
    directory.draft.key = 'later'
    directory.draft.name = '迟到的响应'
    const saving = directory.submit()

    wrapper.unmount()
    pending.resolve({ ...newsKnowledgeBase, id: 'late', key: 'later', name: '迟到的响应' })
    await saving
    await flushPromises()

    const cached = queryClient.getQueryData<{ key: string }[]>(['knowledge-bases', 'management'])
    expect(cached?.some((item) => item.key === 'later')).not.toBe(true)
  })
})
