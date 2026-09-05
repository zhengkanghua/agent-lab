import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { searchDocuments } from '@/api/document-search'
import { useSearchStream } from '../composables/useSearchStream'
import { _resetRecordSequence } from '../model/search-record'

// 数量参数的归一化函数随 composable 一起被使用，mock 里用 importOriginal 保留真实现。
vi.mock('../../../api/document-search', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/document-search')>()),
  searchDocuments: vi.fn(),
}))

const mockedSearchDocuments = vi.mocked(searchDocuments)

// 仍然挂载组件而不是裸调 composable：onScopeDispose 的取消语义需要真实的 effect scope。
function mountHarness(options: Parameters<typeof useSearchStream>[0] = {}) {
  let stream: ReturnType<typeof useSearchStream> | undefined
  const Harness = defineComponent({
    setup() {
      stream = useSearchStream(options)
      return () => h('div')
    },
  })
  const wrapper = mount(Harness)
  if (!stream) throw new Error('Test harness did not initialize search stream')
  return { wrapper, stream }
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
  published_at: null,
  source_name: '测试来源',
  authors: [],
  labels: ['宏观'],
  chunk_count: 2,
  best_score: match.score,
  best_match: match,
  additional_matches: [],
}

describe('useSearchStream', () => {
  beforeEach(() => {
    mockedSearchDocuments.mockReset()
    _resetRecordSequence()
  })

  it('rejects blank input without adding a record or calling the API', async () => {
    const { wrapper, stream } = mountHarness()

    await stream.search()

    expect(stream.inputError.value).toContain('请输入')
    expect(stream.records.value).toHaveLength(0)
    expect(mockedSearchDocuments).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('clears a local validation error once the input becomes valid', async () => {
    const { wrapper, stream } = mountHarness()

    await stream.search()
    expect(stream.inputError.value).not.toBeNull()

    stream.draft.value = '居民消费趋势'
    await nextTick()
    expect(stream.inputError.value).toBeNull()
    wrapper.unmount()
  })

  it('maps a grouped response and accumulates a success record', async () => {
    mockedSearchDocuments.mockResolvedValue([dto])
    const { wrapper, stream } = mountHarness()
    stream.draft.value = '央行利率'

    await stream.search()
    await flushPromises()

    expect(mockedSearchDocuments).toHaveBeenCalledWith({
      query: '央行利率',
      documentLimit: 10,
      matchesPerDocument: 3,
      signal: expect.any(AbortSignal),
    })
    expect(stream.records.value).toHaveLength(1)
    const latest = stream.records.value[0]!
    expect(latest.status).toBe('success')
    expect(latest.results[0]).toMatchObject({ title: '政策利率维持不变' })
    expect(stream.isSearching.value).toBe(false)
    wrapper.unmount()
  })

  it('accumulates multiple searches as separate records newest last', async () => {
    mockedSearchDocuments
      .mockResolvedValueOnce([dto])
      .mockResolvedValueOnce([{ ...dto, title: '第二篇' }])
    const { wrapper, stream } = mountHarness()

    stream.draft.value = '利率'
    await stream.search()
    await flushPromises()

    stream.draft.value = '楼市'
    await stream.search()
    await flushPromises()

    expect(stream.records.value).toHaveLength(2)
    expect(stream.records.value.map((r) => r.query)).toEqual(['利率', '楼市'])
    expect(stream.latestRecord.value?.results[0]).toMatchObject({ title: '第二篇' })
    wrapper.unmount()
  })

  it('cancels a superseding in-flight search and drops the loading placeholder', async () => {
    let resolveFirst: ((value: (typeof dto)[]) => void) | undefined
    mockedSearchDocuments
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockResolvedValueOnce([{ ...dto, title: '新响应' }])
    const { wrapper, stream } = mountHarness()

    stream.draft.value = '第一条'
    const first = stream.search()
    await nextTick()
    expect(stream.records.value).toHaveLength(1)
    expect(stream.records.value[0]!.status).toBe('loading')

    // 用户没等第一条回来就再搜：第一条应被 abort、占位轮被移除。
    stream.draft.value = '第二条'
    await stream.search()
    await flushPromises()

    expect(stream.records.value).toHaveLength(1)
    expect(stream.records.value[0]!.query).toBe('第二条')
    expect(stream.records.value[0]!.status).toBe('success')

    resolveFirst?.([{ ...dto, title: '过期响应' }])
    await first
    await flushPromises()
    expect(stream.records.value).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps retryable API failures explicit on the failed record', async () => {
    mockedSearchDocuments.mockRejectedValue(
      new ApiError({
        message: 'timeout',
        code: 'embedding_timeout',
        status: 504,
        retryable: true,
      }),
    )
    const { wrapper, stream } = mountHarness()
    stream.draft.value = '宏观数据'

    await stream.search()
    await flushPromises()

    const record = stream.records.value[0]!
    expect(record.status).toBe('error')
    expect(record.error?.title).toBe('检索等待时间过长')
    expect(record.error?.retryable).toBe(true)
    wrapper.unmount()
  })

  it('retry re-runs a query as a new record', async () => {
    mockedSearchDocuments
      .mockRejectedValueOnce(
        new ApiError({ message: 'down', code: 'network_error', status: 503, retryable: true }),
      )
      .mockResolvedValueOnce([dto])
    const { wrapper, stream } = mountHarness()
    stream.draft.value = '居民消费'

    await stream.search()
    await flushPromises()
    expect(stream.records.value).toHaveLength(1)
    expect(stream.records.value[0]!.status).toBe('error')

    await stream.retry('居民消费')
    await flushPromises()

    expect(stream.records.value).toHaveLength(2)
    expect(stream.latestRecord.value?.status).toBe('success')
    wrapper.unmount()
  })

  it('数量参数在提交那一刻从注入的 getter 读取（设置中心的偏好）', async () => {
    mockedSearchDocuments.mockResolvedValue([dto])
    let limit = 20
    let perDocument = 5
    const { wrapper, stream } = mountHarness({
      getDocumentLimit: () => limit,
      getMatchesPerDocument: () => perDocument,
    })
    stream.draft.value = '货币政策'

    await stream.search()
    await flushPromises()

    expect(mockedSearchDocuments).toHaveBeenCalledWith({
      query: '货币政策',
      documentLimit: 20,
      matchesPerDocument: 5,
      signal: expect.any(AbortSignal),
    })

    // 下一轮提交时改了偏好：新的一轮用新值，不发请求去改旧记录。
    limit = 1
    perDocument = 1
    stream.draft.value = '财政政策'
    await stream.search()
    await flushPromises()

    expect(mockedSearchDocuments).toHaveBeenLastCalledWith({
      query: '财政政策',
      documentLimit: 1,
      matchesPerDocument: 1,
      signal: expect.any(AbortSignal),
    })
    wrapper.unmount()
  })

  it('clear empties records and draft', async () => {
    mockedSearchDocuments.mockResolvedValue([dto])
    const { wrapper, stream } = mountHarness()
    stream.draft.value = '宏观'

    await stream.search()
    await flushPromises()
    expect(stream.records.value).toHaveLength(1)

    stream.clear()
    expect(stream.records.value).toHaveLength(0)
    expect(stream.draft.value).toBe('')
    wrapper.unmount()
  })
})
