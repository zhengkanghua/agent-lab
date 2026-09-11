import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { reviewDetail } from '@/api/document-review.fixture'
import { newsKnowledgeBase } from '@/api/knowledge-bases.fixture'
import DocumentReviewDirectory from './DocumentReviewDirectory.vue'

let wrapper: VueWrapper
let client: QueryClient
function mountDirectory() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ name: 'admin', path: '/admin/:section?', component: { template: '<div />' } }],
  })
  wrapper = mount(DocumentReviewDirectory, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient: client }]] },
  })
  return wrapper
}
afterEach(() => {
  wrapper?.unmount()
  client?.clear()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('统一列表按知识库、来源、状态筛选；更改筛选后返回第一页', async () => {
  const record = reviewDetail().document
  const requests: URL[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost')
      if (url.pathname.endsWith('/knowledge-bases')) return Response.json([newsKnowledgeBase])
      requests.push(url)
      return Response.json({ items: [record], has_more: true })
    }),
  )
  const wrapper = mountDirectory()
  await flushPromises()
  await wrapper
    .findAll('button')
    .find((item) => item.text() === '下一页')!
    .trigger('click')
  await flushPromises()
  expect(requests.at(-1)?.searchParams.get('offset')).toBe('25')
  const filters = wrapper.findAll('select')
  await filters[0]!.setValue(newsKnowledgeBase.id)
  await filters[1]!.setValue('freshrss')
  await filters[2]!.setValue('review')
  await flushPromises()
  const query = requests.at(-1)!.searchParams
  expect(Object.fromEntries(query)).toEqual({
    offset: '0',
    limit: '25',
    knowledge_base_id: newsKnowledgeBase.id,
    source_kind: 'freshrss',
    state: 'review',
  })
  await wrapper
    .findAll('button')
    .find((item) => item.text() === '查看与审核')!
    .trigger('click')
  expect(wrapper.emitted('open')).toEqual([[record.document_id]])
})

it('仅有在途处理时轮询，终态显示已采用资格并停止轮询', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
  const record = reviewDetail().document
  record.processing_state = 'pending'
  record.current_version_id = null
  let reads = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/knowledge-bases')) return Response.json([newsKnowledgeBase])
      reads++
      return Response.json({ items: [record], has_more: false })
    }),
  )
  const wrapper = mountDirectory()
  await flushPromises()
  expect(wrapper.text()).toContain('尚未采用')
  record.processing_state = 'adopted'
  record.current_version_id = reviewDetail().document.current_version_id
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(wrapper.text()).toContain('已采用版本可用')
  const completed = reads
  await vi.advanceTimersByTimeAsync(20_000)
  expect(reads).toBe(completed)
})
