import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { API_REQUEST_TIMEOUT_MS } from '@/api/client'
import type { ReviewDetailDto } from '@/api/document-review'
import {
  draftId,
  reviewDetail,
  reviewDocumentId,
  reviewPreview,
  sourceId,
  versionId,
} from '@/api/document-review.fixture'
import DocumentManagementPage from './DocumentManagementPage.vue'

const wrappers: VueWrapper[] = []
const clients: QueryClient[] = []
type Handler = (path: string, init: RequestInit) => Response | Promise<Response> | undefined

function receipt(detail: ReviewDetailDto) {
  return Response.json({
    document_id: detail.document.document_id,
    processing_id: detail.candidate.processing_id,
    candidate_revision: detail.candidate.candidate_revision,
    state: detail.candidate.state,
    source_sha256: detail.candidate.source_sha256,
  })
}

function serve(getDetail: () => ReviewDetailDto, handler?: Handler) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = new URL(String(input), 'http://localhost').pathname
    const handled = handler?.(path, init)
    if (handled !== undefined) return handled
    if (path === '/api/document-management/' + reviewDocumentId) return Response.json(getDetail())
    if (path === '/api/document-management') return Response.json({ items: [], has_more: false })
    if (path === '/api/knowledge-bases') return Response.json([])
    if (path.endsWith('/versions') || path.endsWith('/reviews') || path.endsWith('/candidates'))
      return Response.json({ items: [], has_more: false })
    return Response.json({ detail: '测试未配置的接口。' }, { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function mountWorkbench() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { name: 'admin', path: '/admin/:section?', component: DocumentManagementPage },
      { name: 'elsewhere', path: '/elsewhere', component: { template: '<p>其他页面</p>' } },
    ],
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, refetchOnWindowFocus: false } },
  })
  clients.push(queryClient)
  await router.push('/admin/documents?document=' + reviewDocumentId)
  const wrapper = mount(RouterView, {
    attachTo: document.body,
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((item) => item.text() === label)
  if (!found) throw new Error('Missing button: ' + label)
  return found
}
async function click(wrapper: VueWrapper, label: string) {
  await button(wrapper, label).trigger('click')
  await flushPromises()
}
function update(detail: ReviewDetailDto, state: string) {
  detail.candidate.state = state
  detail.document.processing_state = state
  if (detail.draft) detail.draft.state = state
}

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  clients.splice(0).forEach((client) => client.clear())
  document.body.replaceChildren()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('文档审核', () => {
  it('保存使旧预览失效，预览与采用分别受理，采用完成后停止轮询', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const remote = reviewDetail()
    const submitted: string[] = []
    const fetchMock = serve(
      () => remote,
      (path, init) => {
        if (init.method === 'PUT') {
          const body = JSON.parse(String(init.body))
          expect(body).toMatchObject({
            management_revision: 10,
            candidate_revision: 2,
            text: '修正后的正文。',
          })
          submitted.push('save')
          remote.candidate.draft_text = body.text
          remote.candidate.preview = null
          remote.candidate.preview_fingerprint = null
          remote.candidate.candidate_revision = 3
          remote.document.management_revision = 11
          update(remote, 'draft')
          return receipt(remote)
        }
        if (init.method === 'POST' && path.endsWith('/preview')) {
          expect(JSON.parse(String(init.body))).toEqual({
            management_revision: 11,
            candidate_revision: 3,
          })
          submitted.push('preview')
          remote.document.management_revision++
          update(remote, 'pending')
          return receipt(remote)
        }
        if (init.method === 'POST' && path.endsWith('/adopt')) {
          expect(path).toBe('/api/document-management/candidates/' + draftId + '/adopt')
          expect(JSON.parse(String(init.body))).toMatchObject({
            management_revision: 12,
            candidate_revision: 3,
            fingerprint: 'b'.repeat(64),
          })
          submitted.push('adopt')
          update(remote, 'indexing')
          return receipt(remote)
        }
      },
    )
    const { wrapper } = await mountWorkbench()
    await wrapper.get('textarea[aria-describedby="draft-help"]').setValue('修正后的正文。')
    expect(button(wrapper, '确认采用预览').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('上次预览')
    await click(wrapper, '保存草稿')
    expect(wrapper.text()).toContain('尚无有效预览')
    await click(wrapper, '保存并重新生成预览')
    expect(wrapper.text()).toContain('等待解析')
    remote.candidate.preview = reviewPreview('修正后的正文。')
    remote.candidate.preview_fingerprint = 'b'.repeat(64)
    update(remote, 'review')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    await click(wrapper, '确认采用预览')
    await click(wrapper, '采用这份预览')
    expect(wrapper.text()).toContain('候选已冻结')
    expect(wrapper.text()).toContain('第 1 版')
    expect(
      wrapper.get('textarea[aria-describedby="draft-help"]').attributes('readonly'),
    ).toBeDefined()
    update(remote, 'adopted')
    remote.document.revision = 2
    remote.document.draft_processing_id = null
    remote.draft = null
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.text()).toContain('第 2 版')
    const reads = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(20_000)
    expect(fetchMock.mock.calls.length).toBe(reads)
    expect(submitted).toEqual(['save', 'preview', 'adopt'])
  })

  it('并发保存冲突后保留本地正文，明确继续时才使用最新修订', async () => {
    const remote = reviewDetail()
    let saves = 0
    serve(
      () => remote,
      (path, init) => {
        if (init.method === 'PUT') {
          saves++
          if (saves === 1) {
            remote.document.management_revision = 15
            remote.candidate.candidate_revision = 6
            remote.candidate.draft_text = '另一人的修订'
            return Response.json(
              { code: 'document_processing_conflict', detail: '资料已更新。' },
              { status: 409 },
            )
          }
          expect(JSON.parse(String(init.body))).toMatchObject({
            management_revision: 15,
            candidate_revision: 6,
            text: '自己的编辑',
          })
          remote.candidate.draft_text = '自己的编辑'
          remote.document.management_revision++
          return receipt(remote)
        }
        if (init.method === 'POST' && path.endsWith('/draft')) return receipt(remote)
      },
    )
    const { wrapper } = await mountWorkbench()
    await wrapper.get('textarea[aria-describedby="draft-help"]').setValue('自己的编辑')
    await click(wrapper, '保存草稿')
    expect(wrapper.get('textarea[aria-describedby="draft-help"]').element).toHaveProperty(
      'value',
      '自己的编辑',
    )
    expect(wrapper.text()).toContain('本地编辑已保留')
    await click(wrapper, '保留本地编辑并继续')
    expect(wrapper.get('textarea[aria-describedby="draft-help"]').element).toHaveProperty(
      'value',
      '自己的编辑',
    )
    await click(wrapper, '保存草稿')
    expect(saves).toBe(2)
  })

  it('刷新不覆盖未保存文字；离开或刷新页面要求保留或放弃的明确选择', async () => {
    const remote = reviewDetail()
    serve(() => remote)
    const { wrapper, router } = await mountWorkbench()
    await wrapper.get('textarea[aria-describedby="draft-help"]').setValue('还未保存')
    remote.document.management_revision++
    remote.candidate.draft_text = '后台最新正文'
    await click(wrapper, '刷新状态')
    expect(wrapper.get('textarea[aria-describedby="draft-help"]').element).toHaveProperty(
      'value',
      '还未保存',
    )
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await router.push('/elsewhere')
    expect(router.currentRoute.value.path).toBe('/admin/documents')
    confirm.mockReturnValue(true)
    await router.push('/elsewhere')
    expect(router.currentRoute.value.path).toBe('/elsewhere')
  })

  it('采用超时后核对服务端状态，不自动重发采用请求', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const remote = reviewDetail()
    const fetchMock = serve(
      () => remote,
      (path, init) => {
        if (init.method === 'POST' && path.endsWith('/adopt')) {
          update(remote, 'indexing')
          return new Promise<Response>((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            )
          })
        }
      },
    )
    const { wrapper } = await mountWorkbench()
    await click(wrapper, '确认采用预览')
    await button(wrapper, '采用这份预览').trigger('click')
    await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS + 1)
    await flushPromises()
    expect(wrapper.text()).toContain('操作结果尚未确认')
    expect(wrapper.text()).toContain('正在建立新索引')
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('打开确认后目标更新会撤销旧确认，不能把新预览当成已检查结果采用', async () => {
    const remote = reviewDetail()
    const fetchMock = serve(() => remote)
    const { wrapper } = await mountWorkbench()
    await click(wrapper, '确认采用预览')
    remote.document.management_revision++
    remote.candidate.candidate_revision++
    remote.candidate.preview_fingerprint = 'f'.repeat(64)
    await click(wrapper, '刷新状态')
    expect(wrapper.findAll('button').some((item) => item.text() === '采用这份预览')).toBe(false)
    expect(wrapper.text()).toContain('重新确认')
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('来源更新保留草稿，只有明确换用后才替换编辑内容', async () => {
    const remote = reviewDetail()
    remote.latest_source = {
      ...remote.latest_source!,
      state: 'review',
      source_sha256: 'f'.repeat(64),
    }
    const fetchMock = serve(
      () => remote,
      (path, init) => {
        if (init.method === 'POST' && path.endsWith('/use-latest-source')) {
          expect(JSON.parse(String(init.body))).toEqual({ management_revision: 10 })
          remote.candidate.draft_text = '最新来源正文'
          remote.candidate.preview = null
          remote.document.management_revision++
          return receipt(remote)
        }
      },
    )
    const { wrapper } = await mountWorkbench()
    await wrapper.get('textarea[aria-describedby="draft-help"]').setValue('保留人工草稿')
    await click(wrapper, '换用最新来源')
    expect(wrapper.text()).toContain('未保存编辑将被替换')
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
    await click(wrapper, '确认替换草稿')
    expect(wrapper.get('textarea[aria-describedby="draft-help"]').element).toHaveProperty(
      'value',
      '最新来源正文',
    )
    expect(wrapper.text()).toContain('第 1 版')
  })

  it('拒绝明确停止整篇使用，保留管理入口且不调用删除', async () => {
    const remote = reviewDetail()
    const fetchMock = serve(
      () => remote,
      (path, init) => {
        if (init.method === 'POST' && path.endsWith('/reject')) {
          expect(JSON.parse(String(init.body))).toMatchObject({
            candidate_revision: 2,
            management_revision: 10,
          })
          remote.document.usage_status = 'rejected'
          remote.document.management_revision++
          update(remote, 'rejected')
          return receipt(remote)
        }
      },
    )
    const { wrapper } = await mountWorkbench()
    await click(wrapper, '拒绝并停止使用')
    expect(wrapper.text()).toContain('后续检索、Agent 和普通全文')
    await click(wrapper, '确认停止使用')
    expect(wrapper.text()).toContain('已停止使用')
    expect(button(wrapper, '完整删除').exists()).toBe(true)
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
  })

  it('完整删除失败后刷新待办，再次明确删除使用当前管理修订', async () => {
    const remote = reviewDetail()
    let deletes = 0
    const fetchMock = serve(
      () => remote,
      (_path, init) => {
        if (init.method === 'DELETE') {
          deletes++
          if (deletes === 1) {
            remote.document.deletion_pending = true
            remote.document.usage_status = 'deleting'
            remote.document.management_revision = 11
            return Response.json(
              { code: 'document_delete_failed', detail: '删除未完成。' },
              { status: 503 },
            )
          }
          return new Response(null, { status: 204 })
        }
      },
    )
    const { wrapper, router } = await mountWorkbench()
    await click(wrapper, '完整删除')
    expect(wrapper.text()).toContain('所有已采用版本、审核结论和索引都将清除')
    await click(wrapper, '确认完整删除')
    expect(wrapper.text()).toContain('删除未完成')
    await click(wrapper, '继续完整删除')
    await click(wrapper, '确认完整删除')
    expect(router.currentRoute.value.query.document).toBeUndefined()
    const requests = fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')
    expect(String(requests[1]?.[0])).toContain('revision=1&management_revision=11')
  })

  it('历史分页读取冻结版本与当时审核正文，不修改当前草稿', async () => {
    const remote = reviewDetail()
    const oldPreview = reviewPreview('历史正文保留 7 天。')
    const fetchMock = serve(
      () => remote,
      (path) => {
        if (path.endsWith('/versions'))
          return Response.json({
            items: [
              {
                version_id: versionId,
                processing_id: sourceId,
                revision: 1,
                title: '历史手册',
                content_hash: 'd'.repeat(64),
                created_at: '2026-09-01T00:00:00Z',
              },
            ],
            has_more: true,
          })
        if (path.endsWith('/versions/' + versionId))
          return Response.json({
            version_id: versionId,
            processing_id: sourceId,
            revision: 1,
            title: '历史手册',
            content_hash: 'd'.repeat(64),
            created_at: '2026-09-01T00:00:00Z',
            preview: oldPreview,
            metadata: {},
            processing_spec: {},
          })
        if (path.endsWith('/reviews'))
          return Response.json({
            items: [
              {
                review_id: versionId,
                processing_id: sourceId,
                candidate_revision: 1,
                decision: 'adopt',
                decision_source: 'automatic',
                actor_id: null,
                conclusion: null,
                preview_fingerprint: 'd'.repeat(64),
                content_snapshot: { body: '当时正文' },
                created_at: '2026-09-01T00:00:00Z',
              },
            ],
            has_more: false,
          })
      },
    )
    const { wrapper } = await mountWorkbench()
    await click(wrapper, '版本与审核结论')
    await click(wrapper, '下一页')
    expect(
      fetchMock.mock.calls.some(([path]) => String(path).includes('/versions?offset=25')),
    ).toBe(true)
    await click(wrapper, '查看版本')
    expect(wrapper.text()).toContain('历史正文保留 7 天')
    await click(wrapper, '审核结论')
    expect(wrapper.text()).toContain('自动处理')
    expect(wrapper.text()).toContain('当时正文')
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  })
})
