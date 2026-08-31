import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SearchPage from '@/pages/SearchPage.vue'

const match = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.91,
  page_content: '新闻分组中的最佳片段。',
  chunk_index: 0,
  chunk_count: 2,
}

const documentResult = {
  document_id: '20000000-0000-4000-8000-000000000001',
  content_hash: 'a'.repeat(64),
  title: '文档模式结果',
  url: 'https://example.com/news',
  source_name: '测试来源',
  published_at: null,
  authors: [],
  labels: ['宏观'],
  chunk_count: 2,
  best_score: match.score,
  best_match: match,
  additional_matches: [],
}

const chunkResult = {
  chunk_id: match.chunk_id,
  score: 0.82,
  page_content: '原始 Chunk 模式结果。',
  document_id: documentResult.document_id,
  content_hash: documentResult.content_hash,
  chunk_index: 0,
  chunk_count: 2,
  title: '片段模式结果',
  url: documentResult.url,
  published_at: null,
  source_updated_at: null,
  document_type: 'article',
  source_id: '30000000-0000-4000-8000-000000000001',
  source_provider: 'test',
  source_name: documentResult.source_name,
  source_external_id: 'feed/1',
  document_external_id: 'article/1',
  authors: [],
  labels: ['宏观'],
  previous_chunk_id: null,
  next_chunk_id: null,
  embedding_model: 'bge-m3:567m',
}

describe('SearchPage search modes', () => {
  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
  })

  it('uses the grouped endpoint by default and the raw endpoint after switching modes', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      const responseBody = url.endsWith('/document-search') ? [documentResult] : [chunkResult]
      return Promise.resolve(
        new Response(JSON.stringify(responseBody), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const wrapper = mount(SearchPage, {
      attachTo: document.body,
      global: { plugins: [[VueQueryPlugin, { queryClient }]] },
    })

    await wrapper.get('textarea').setValue('央行利率')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/document-search')
    expect(wrapper.findAll('.result-card')).toHaveLength(1)

    const chunkModeButton = wrapper
      .findAll<HTMLButtonElement>('.mode-switch button')
      .find((button) => button.text().includes('按片段'))
    await chunkModeButton?.trigger('click')
    await wrapper.get('textarea').setValue('央行利率')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/vector-search')
    expect(wrapper.findAll('.chunk-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('片段模式结果')
    wrapper.unmount()
  })
})
