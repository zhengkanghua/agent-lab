import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { searchVector, type VectorSearchResultDto } from '../../../api/vector-search'
import { useChunkSearch } from '../composables/useChunkSearch'

vi.mock('../../../api/vector-search', () => ({
  searchVector: vi.fn(),
}))

const mockedSearchVector = vi.mocked(searchVector)

const dto: VectorSearchResultDto = {
  point_id: '10000000-0000-4000-8000-000000000001',
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.72,
  page_content: '第一条原始片段。',
  document_id: '20000000-0000-4000-8000-000000000001',
  content_hash: 'a'.repeat(64),
  chunk_index: 0,
  chunk_count: 2,
  title: '同一新闻',
  url: 'https://example.com/news',
  published_at: null,
  source_updated_at: null,
  document_type: 'article',
  source_id: '30000000-0000-4000-8000-000000000001',
  source_provider: 'test',
  source_name: '测试来源',
  source_external_id: 'feed/1',
  document_external_id: 'article/1',
  authors: [],
  labels: ['宏观'],
  previous_chunk_id: null,
  next_chunk_id: '10000000-0000-4000-8000-000000000002',
  index_schema_version: 'v1',
  embedding_model: 'bge-m3:567m',
}

function mountHarness() {
  let composable: ReturnType<typeof useChunkSearch> | undefined
  const Harness = defineComponent({
    setup() {
      composable = useChunkSearch()
      return () => h('div')
    },
  })
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  })
  const wrapper = mount(Harness, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  if (!composable) throw new Error('Test harness did not initialize Chunk search')
  return { wrapper, search: composable }
}

describe('useChunkSearch', () => {
  beforeEach(() => mockedSearchVector.mockReset())

  it('calls /vector-search semantics and preserves order plus duplicate document ids', async () => {
    const second = {
      ...dto,
      point_id: '10000000-0000-4000-8000-000000000002',
      chunk_id: '10000000-0000-4000-8000-000000000002',
      score: 0.91,
      page_content: '第二条原始片段，分数更高但仍保持后端顺序。',
      chunk_index: 1,
      previous_chunk_id: dto.chunk_id,
      next_chunk_id: null,
    }
    mockedSearchVector.mockResolvedValue([dto, second])
    const { wrapper, search } = mountHarness()
    search.query.value = '央行利率'

    await search.search()
    await flushPromises()

    expect(mockedSearchVector).toHaveBeenCalledWith({
      query: '央行利率',
      topK: 10,
      signal: expect.any(AbortSignal),
    })
    expect(search.results.value.map((item) => item.id)).toEqual([dto.chunk_id, second.chunk_id])
    expect(search.results.value.map((item) => item.documentId)).toEqual([
      dto.document_id,
      dto.document_id,
    ])
    expect(search.results.value[0]?.contentHash).toBe(dto.content_hash)
    expect(search.status.value).toBe('success')
    wrapper.unmount()
  })

  it('normalizes an invalid result count to the supported minimum of one', async () => {
    mockedSearchVector.mockResolvedValue([])
    const { wrapper, search } = mountHarness()
    search.query.value = '宏观数据'
    search.topK.value = 0

    await search.search()

    expect(search.topK.value).toBe(1)
    expect(mockedSearchVector).toHaveBeenCalledWith(
      expect.objectContaining({ query: '宏观数据', topK: 1 }),
    )
    expect(search.status.value).toBe('empty')
    wrapper.unmount()
  })

  it('prevents an older raw response from replacing a newer search', async () => {
    let resolveFirst: ((value: VectorSearchResultDto[]) => void) | undefined
    let resolveSecond: ((value: VectorSearchResultDto[]) => void) | undefined
    mockedSearchVector
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockImplementationOnce(() => new Promise((resolve) => (resolveSecond = resolve)))
    const { wrapper, search } = mountHarness()

    search.query.value = '第一条'
    const first = search.search()
    await nextTick()
    search.query.value = '第二条'
    const second = search.search()
    await nextTick()

    resolveSecond?.([{ ...dto, title: '新响应' }])
    await second
    resolveFirst?.([{ ...dto, title: '旧响应' }])
    await first
    await flushPromises()

    expect(search.results.value[0]?.title).toBe('新响应')
    expect(search.lastQuery.value).toBe('第二条')
    wrapper.unmount()
  })
})
