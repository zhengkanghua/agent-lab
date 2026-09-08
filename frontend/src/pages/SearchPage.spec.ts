import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import SearchPage from '@/pages/SearchPage.vue'
import { _resetRecordSequence } from '@/features/semantic-search'
import { scopedSearchResponse } from '@/api/document-search.fixture'
import { newsKnowledgeBase, techKnowledgeBase } from '@/api/knowledge-bases.fixture'

const match = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.91,
  page_content: '新闻分组中的最佳片段。',
  chunk_index: 0,
  chunk_count: 2,
}

function documentResult(title: string) {
  return {
    mime_type: 'text/plain',
    knowledge_base_id: '10000000-0000-4000-8000-000000000010',
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

/** 页面里的顶栏与偏好入口都是 RouterLink，测试路由只放它们会去的地方。 */
function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div />' } },
      { path: '/agent', name: 'agent-chat', component: { template: '<div />' } },
      { path: '/settings/:section?', name: 'settings', component: { template: '<div />' } },
    ],
  })
}

describe('SearchPage search stream', () => {
  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
    _resetRecordSequence()
  })

  it('searches the grouped endpoint and shows the result as a record', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes('/knowledge-bases'))
        return Promise.resolve(Response.json([newsKnowledgeBase]))
      return Promise.resolve(
        new Response(JSON.stringify(scopedSearchResponse([documentResult('第一篇')])), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SearchPage, {
      attachTo: document.body,
      global: {
        plugins: [[VueQueryPlugin, { queryClient: makeQueryClient() }], makeRouter()],
      },
    })

    expect(wrapper.find('.empty-state').exists()).toBe(true)
    expect(wrapper.find('.stream').exists()).toBe(false)
    await flushPromises()

    await wrapper.get('textarea').setValue('央行利率')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(fetchMock.mock.calls.some(([input]) => input === '/api/document-search')).toBe(true)
    expect(wrapper.find('.empty-state').exists()).toBe(false)
    expect(wrapper.findAll('.record')).toHaveLength(1)
    expect(wrapper.findAll('.result-card')).toHaveLength(1)
    wrapper.unmount()
  })

  it('accumulates rounds and keeps the newest record closest to the input', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes('/knowledge-bases'))
        return Promise.resolve(Response.json([newsKnowledgeBase]))
      const title = String(input).includes('楼市') ? '楼市结果' : '利率结果'
      return Promise.resolve(
        new Response(JSON.stringify(scopedSearchResponse([documentResult(title)])), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SearchPage, {
      attachTo: document.body,
      global: {
        plugins: [[VueQueryPlugin, { queryClient: makeQueryClient() }], makeRouter()],
      },
    })

    await flushPromises()
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

  it('clear-stream empties the records back to the empty state', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes('/knowledge-bases'))
        return Promise.resolve(Response.json([newsKnowledgeBase]))
      return Promise.resolve(
        new Response(JSON.stringify(scopedSearchResponse([documentResult('第一篇')])), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SearchPage, {
      attachTo: document.body,
      global: {
        plugins: [[VueQueryPlugin, { queryClient: makeQueryClient() }], makeRouter()],
      },
    })

    await flushPromises()
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

  it('选择多个知识库后发送明确范围，空选择不检索', async () => {
    const fetchMock = vi.fn<typeof fetch>((input) =>
      Promise.resolve(
        Response.json(
          String(input).includes('/knowledge-bases')
            ? [newsKnowledgeBase, { ...techKnowledgeBase, is_active: true }]
            : scopedSearchResponse([]),
        ),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SearchPage, {
      global: { plugins: [[VueQueryPlugin, { queryClient: makeQueryClient() }], makeRouter()] },
    })
    await flushPromises()
    await wrapper.findAll('input[type="radio"]')[1]!.setValue(true)
    await wrapper.get('textarea').setValue('资料')
    await wrapper.get('form').trigger('submit')
    expect(fetchMock.mock.calls.filter(([input]) => input === '/api/document-search')).toHaveLength(
      0,
    )
    for (const checkbox of wrapper.findAll('input[type="checkbox"]')) await checkbox.setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const call = fetchMock.mock.calls.find(([input]) => input === '/api/document-search')
    expect(JSON.parse(call?.[1]?.body as string).scope).toEqual({
      mode: 'selected',
      knowledge_base_ids: [newsKnowledgeBase.id, techKnowledgeBase.id],
    })
    wrapper.unmount()
  })

  it('目录加载失败时显示重载入口，不将失败当作全库查询', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('offline'))
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mount(SearchPage, {
      global: { plugins: [[VueQueryPlugin, { queryClient: makeQueryClient() }], makeRouter()] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('知识库目录加载失败')
    await wrapper.get('textarea').setValue('资料')
    await wrapper.get('form').trigger('submit')
    expect(fetchMock.mock.calls.some(([input]) => input === '/api/document-search')).toBe(false)
    wrapper.unmount()
  })
})

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}
