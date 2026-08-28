import { defineComponent, h, nextTick } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../../api/client'
import { searchDocuments } from '../../../api/document-search'
import { useSemanticSearch } from '../composables/useSemanticSearch'

vi.mock('../../../api/document-search', () => ({
  searchDocuments: vi.fn(),
}))

const mockedSearchDocuments = vi.mocked(searchDocuments)

// 仍然挂载组件而不是裸调 composable：onScopeDispose 的取消语义需要真实的 effect scope。
function mountHarness() {
  let composable: ReturnType<typeof useSemanticSearch> | undefined
  const Harness = defineComponent({
    setup() {
      composable = useSemanticSearch()
      return () => h('div')
    },
  })
  const wrapper = mount(Harness)
  if (!composable) throw new Error('Test harness did not initialize composable')
  return { wrapper, search: composable }
}

const match = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.9123,
  page_content: '央行维持政策利率不变。',
  chunk_index: 0,
  chunk_count: 2,
}

const dto = {
  document_id: '20000000-0000-4000-8000-000000000001',
  content_hash: 'a'.repeat(64),
  title: '政策利率维持不变',
  url: 'https://example.com/news',
  published_at: '2026-08-14T08:00:00Z',
  source_name: '测试来源',
  authors: [],
  labels: ['宏观'],
  chunk_count: 2,
  best_score: match.score,
  best_match: match,
  additional_matches: [],
}

describe('useSemanticSearch', () => {
  beforeEach(() => {
    mockedSearchDocuments.mockReset()
  })

  it('rejects blank input without calling the API', async () => {
    const { wrapper, search } = mountHarness()

    await search.search()

    expect(search.inputError.value).toContain('请输入')
    expect(search.status.value).toBe('idle')
    expect(mockedSearchDocuments).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('clears a local validation error once the input becomes valid', async () => {
    const { wrapper, search } = mountHarness()

    await search.search()
    expect(search.inputError.value).not.toBeNull()

    search.query.value = '居民消费趋势'
    await nextTick()

    expect(search.inputError.value).toBeNull()
    wrapper.unmount()
  })

  it('maps a grouped response and exposes the success state', async () => {
    mockedSearchDocuments.mockResolvedValue([dto])
    const { wrapper, search } = mountHarness()
    search.query.value = '央行利率'

    await search.search()
    await flushPromises()

    expect(mockedSearchDocuments).toHaveBeenCalledWith({
      query: '央行利率',
      documentLimit: 10,
      matchesPerDocument: 3,
      signal: expect.any(AbortSignal),
    })
    expect(search.status.value).toBe('success')
    expect(search.results.value[0]).toMatchObject({
      title: '政策利率维持不变',
      sourceName: '测试来源',
      bestScore: 0.9123,
      bestMatch: { chunkIndex: 0 },
    })
    wrapper.unmount()
  })

  it('normalizes article count to the supported minimum of one', async () => {
    mockedSearchDocuments.mockResolvedValue([])
    const { wrapper, search } = mountHarness()
    search.query.value = '居民消费'
    search.documentLimit.value = 0

    await search.search()

    expect(search.documentLimit.value).toBe(1)
    expect(mockedSearchDocuments).toHaveBeenCalledWith(
      expect.objectContaining({ documentLimit: 1 }),
    )
    wrapper.unmount()
  })

  it('prevents an older response from replacing a newer search', async () => {
    let resolveFirst: ((value: (typeof dto)[]) => void) | undefined
    let resolveSecond: ((value: (typeof dto)[]) => void) | undefined
    mockedSearchDocuments
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockImplementationOnce(() => new Promise((resolve) => (resolveSecond = resolve)))

    const { wrapper, search } = mountHarness()
    search.query.value = '第一条'
    const first = search.search()
    await nextTick()
    search.query.value = '第二条'
    const second = search.search()
    await nextTick()

    resolveSecond?.([dto])
    await second
    resolveFirst?.([
      {
        ...dto,
        title: '过期响应',
      },
    ])
    await first
    await flushPromises()

    expect(search.results.value[0]?.title).toBe('政策利率维持不变')
    expect(search.lastQuery.value).toBe('第二条')
    wrapper.unmount()
  })

  it('keeps retryable API failures explicit', async () => {
    mockedSearchDocuments.mockRejectedValue(
      new ApiError({
        message: 'timeout',
        code: 'embedding_timeout',
        status: 504,
        retryable: true,
      }),
    )
    const { wrapper, search } = mountHarness()
    search.query.value = '宏观数据'

    await search.search()
    await flushPromises()

    expect(search.status.value).toBe('error')
    expect(search.requestError.value?.code).toBe('embedding_timeout')
    wrapper.unmount()
  })
})
