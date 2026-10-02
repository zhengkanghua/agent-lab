import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { boundNewsSource, unboundSource } from '@/api/sources.fixture'
import { newsKnowledgeBase, techKnowledgeBase } from '@/api/knowledge-bases.fixture'

const sourcesApi = vi.hoisted(() => ({ listSources: vi.fn(), bindSource: vi.fn() }))
const knowledgeApi = vi.hoisted(() => ({ listKnowledgeBases: vi.fn() }))

vi.mock('@/api/sources', () => sourcesApi)
vi.mock('@/api/knowledge-bases', () => knowledgeApi)

import { ApiError } from '@/api/client'
import { useSources } from '../useSources'

/* 来源绑定的行为。这个 feature 此前零测试；这里钉的是：只改那一行、反馈说清绑到哪/解绑后
 * 从新基线开始、失败按错误码翻成人话、以及在途时不重入。 */

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
  let directory!: ReturnType<typeof useSources>
  const Host = defineComponent({
    setup() {
      directory = useSources()
      return () => null
    },
  })
  const wrapper = mount(Host, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  return { wrapper, directory, queryClient }
}

beforeEach(() => {
  sourcesApi.listSources.mockReset()
  sourcesApi.bindSource.mockReset()
  knowledgeApi.listKnowledgeBases.mockReset()
  sourcesApi.listSources.mockResolvedValue([boundNewsSource, unboundSource])
  knowledgeApi.listKnowledgeBases.mockResolvedValue([newsKnowledgeBase, techKnowledgeBase])
})

describe('useSources', () => {
  it('同时读来源与知识库选项，读停用库是为了展示已有绑定', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    expect(directory.items.value).toHaveLength(2)
    expect(directory.knowledgeBaseOptions.value.map((item) => item.key)).toEqual([
      'news',
      'tech-notes',
    ])
    // 停用库也在选项里：否则一个已经绑到停用库的来源没法如实显示它绑在哪。
    expect(knowledgeApi.listKnowledgeBases).toHaveBeenCalledWith(true, expect.anything())
    expect(directory.loadError.value).toBe('')
  })

  it('来源或选项任一读失败都给出可显示的文案', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    sourcesApi.listSources.mockRejectedValue(
      new ApiError({ message: 'x', code: 'source_unavailable', status: 503 }),
    )
    // refresh() 内部是两个 void refetch（不返回 Promise），要自己等一拍。
    await directory.refresh()
    await flushPromises()
    expect(directory.loadError.value).toBe('来源服务暂时不可用，请稍后重试。')

    sourcesApi.listSources.mockResolvedValue([boundNewsSource])
    knowledgeApi.listKnowledgeBases.mockRejectedValue(
      new ApiError({ message: 'x', code: 'source_unavailable', status: 503 }),
    )
    await directory.refresh()
    await flushPromises()
    expect(directory.loadError.value).toBe('来源服务暂时不可用，请稍后重试。')
  })

  it('按 id 查名字：查得到给名字，查不到回落成 id，未绑定给空串', async () => {
    const { directory } = mountHarness()
    await flushPromises()

    expect(directory.knowledgeBaseName(newsKnowledgeBase.id)).toBe('新闻')
    expect(directory.knowledgeBaseName('not-in-options')).toBe('not-in-options')
    expect(directory.knowledgeBaseName(null)).toBe('')
  })

  it('绑定成功后只替换那一行，并在反馈里点名目标库', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    sourcesApi.bindSource.mockResolvedValue({
      ...unboundSource,
      knowledge_base_id: newsKnowledgeBase.id,
      knowledge_base_key: newsKnowledgeBase.key,
    })

    await directory.changeBinding(unboundSource, newsKnowledgeBase.id)

    expect(sourcesApi.bindSource).toHaveBeenCalledWith(unboundSource.id, newsKnowledgeBase.id)
    expect(directory.feedback.value).toBe('已把 待配置订阅 绑定到 新闻。')
    const rows = directory.items.value
    expect(rows.find((item) => item.id === unboundSource.id)?.knowledge_base_key).toBe('news')
    // 另一行保持原样。
    expect(rows.find((item) => item.id === boundNewsSource.id)).toEqual(boundNewsSource)
  })

  it('解绑的反馈说的是「下一次同步从新基线开始」，不是「绑定到空」', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    sourcesApi.bindSource.mockResolvedValue({
      ...boundNewsSource,
      knowledge_base_id: null,
      knowledge_base_key: null,
    })

    await directory.changeBinding(boundNewsSource, null)

    expect(sourcesApi.bindSource).toHaveBeenCalledWith(boundNewsSource.id, null)
    expect(directory.feedback.value).toBe('已解除 财经早报 的绑定，下一次同步从新基线开始。')
  })

  it('绑定失败按错误码翻成人话，且不动列表', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    sourcesApi.bindSource.mockRejectedValue(
      new ApiError({ message: 'x', code: 'source_binding_conflict', status: 409 }),
    )

    await directory.changeBinding(unboundSource, newsKnowledgeBase.id)

    expect(directory.actionError.value).toBe('来源已有文档、删除待办或未完成写入，不能修改绑定。')
    expect(directory.items.value).toEqual([boundNewsSource, unboundSource])
  })

  it('绑定请求在途时不重入：连点只发一次', async () => {
    const { directory } = mountHarness()
    await flushPromises()
    const pending = deferred<typeof unboundSource>()
    sourcesApi.bindSource.mockReturnValue(pending.promise)

    const first = directory.changeBinding(unboundSource, newsKnowledgeBase.id)
    await directory.changeBinding(unboundSource, techKnowledgeBase.id)

    expect(sourcesApi.bindSource).toHaveBeenCalledTimes(1)
    expect(directory.binding.value).toBe(true)

    pending.resolve(unboundSource)
    await first
    expect(directory.binding.value).toBe(false)
  })
})
