import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SearchPage from '@/pages/SearchPage.vue'
import { _resetRecordSequence } from '@/features/semantic-search'

const match = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.91,
  page_content: '新闻分组中的最佳片段。',
  chunk_index: 0,
  chunk_count: 2,
}

function documentResult(title: string) {
  return {
    document_id:
      title === '第一篇'
        ? '20000000-0000-4000-8000-000000000001'
        : '20000000-0000-4000-8000-000000000002',
    content_hash: 'a'.repeat(64),
    title,
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
}

describe('SearchPage search stream', () => {
  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
    _resetRecordSequence()
  })

  it('searches the grouped endpoint and shows the result as a record', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      void input
      return Promise.resolve(
        new Response(JSON.stringify([documentResult('第一篇')]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SearchPage, {
      attachTo: document.body,
      global: { plugins: [[VueQueryPlugin, { queryClient: makeQueryClient() }]] },
    })

    expect(wrapper.find('.empty-state').exists()).toBe(true)
    expect(wrapper.find('.stream').exists()).toBe(false)

    await wrapper.get('textarea').setValue('央行利率')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/document-search')
    expect(wrapper.find('.empty-state').exists()).toBe(false)
    expect(wrapper.findAll('.record')).toHaveLength(1)
    expect(wrapper.findAll('.result-card')).toHaveLength(1)
    wrapper.unmount()
  })

  it('accumulates rounds and keeps the newest record closest to the input', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const title = String(input).includes('楼市') ? '楼市结果' : '利率结果'
      return Promise.resolve(
        new Response(JSON.stringify([documentResult(title)]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SearchPage, {
      attachTo: document.body,
      global: { plugins: [[VueQueryPlugin, { queryClient: makeQueryClient() }]] },
    })

    // 第一轮
    await wrapper.get('textarea').setValue('利率')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    // 第二轮（换词）
    await wrapper.get('textarea').setValue('楼市')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.findAll('.record')).toHaveLength(2)

    // 模型二：最新一条（楼市）贴顶，旧记录（利率）往下。
    const firstQuery = wrapper.get('.record .record-query')
    expect(firstQuery.text()).toBe('楼市')
    wrapper.unmount()
  })

  it('removes chunk mode controls entirely (no switch in the composer)', async () => {
    const wrapper = mount(SearchPage, {
      attachTo: document.body,
      global: { plugins: [[VueQueryPlugin, { queryClient: makeQueryClient() }]] },
    })

    expect(wrapper.find('.mode-switch').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('按片段')
    wrapper.unmount()
  })

  it('clear-stream empties the records back to the empty state', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify([documentResult('第一篇')]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SearchPage, {
      attachTo: document.body,
      global: { plugins: [[VueQueryPlugin, { queryClient: makeQueryClient() }]] },
    })

    await wrapper.get('textarea').setValue('央行')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.findAll('.record')).toHaveLength(1)

    await wrapper.get('.clear-button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.empty-state').exists()).toBe(true)
    expect(wrapper.findAll('.record')).toHaveLength(0)
    wrapper.unmount()
  })
})

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}
