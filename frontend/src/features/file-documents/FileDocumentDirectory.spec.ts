import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { API_REQUEST_TIMEOUT_MS } from '@/api/client'
import type { FileDocumentDto } from '@/api/file-documents'
import { newsKnowledgeBase, techKnowledgeBase } from '@/api/knowledge-bases.fixture'
import FileDocumentDirectory from './FileDocumentDirectory.vue'

const file: FileDocumentDto = {
  document_id: '20000000-0000-4000-8000-000000000001',
  knowledge_base_id: newsKnowledgeBase.id,
  knowledge_base_name: newsKnowledgeBase.name,
  knowledge_base_active: true,
  upload_filename: 'notes.txt',
  title: 'notes',
  mime_type: 'text/plain',
  content_hash: null,
  revision: 1,
  updated_at: '2026-09-08T00:00:00Z',
  processing_status: 'pending',
  processing_error: null,
  deletion_pending: false,
  deletion_error: null,
  management_revision: 7,
  processing_id: '20000000-0000-4000-8000-000000000002',
  candidate_revision: 1,
  candidate_state: 'pending',
  candidate_error: null,
  current_version_id: null,
  usage_status: 'active',
}

const wrappers: VueWrapper[] = []

function mountDirectory() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/admin/:section?', name: 'admin', component: { template: '<div />' } }],
  })
  const wrapper = mount(FileDocumentDirectory, {
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
  })
  wrappers.push(wrapper)
  return wrapper
}

async function click(wrapper: VueWrapper, label: string) {
  const button = wrapper.findAll('button').find((item) => item.text() === label)
  if (!button) throw new Error(`Missing button: ${label}`)
  await button.trigger('click')
}

async function selectFile(wrapper: VueWrapper, name: string) {
  const input = wrapper.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', {
    configurable: true,
    value: [new File(['正文'], name)],
  })
  await input.trigger('change')
}

function listResponse(items: FileDocumentDto[]) {
  return Response.json({ items, has_more: false, max_file_bytes: 2 * 1024 * 1024 })
}

describe('文件资料管理', () => {
  it('文件接口未提供时显示服务提示，服务恢复后刷新可以读到列表', async () => {
    let available = false
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes('/knowledge-bases')) return Response.json([newsKnowledgeBase])
        return available
          ? listResponse([file])
          : Response.json({ detail: 'Not Found' }, { status: 404 })
      }),
    )
    const wrapper = mountDirectory()
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('文件管理服务尚未就绪')
    expect(wrapper.find('tbody').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('还没有上传文档')

    available = true
    await click(wrapper, '刷新状态')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.get('tbody').text()).toContain(file.upload_filename)
  })

  it('替换冲突保留文件，明确选用刷新记录后携带新的管理修订', async () => {
    let item = { ...file }
    const revisions: FormDataEntryValue[] = []
    const managementRevisions: FormDataEntryValue[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).includes('/knowledge-bases')) return Response.json([newsKnowledgeBase])
        if (init?.method === 'PUT') {
          revisions.push((init.body as FormData).get('revision')!)
          managementRevisions.push((init.body as FormData).get('management_revision')!)
          if (revisions.length === 1) {
            item = { ...file, management_revision: 8, title: '另一页面的有效更新' }
            return Response.json(
              { code: 'file_revision_conflict', detail: '资料已更新', retryable: false },
              { status: 409 },
            )
          }
          item = { ...item, management_revision: 9 }
          return Response.json(item)
        }
        return listResponse([item])
      }),
    )
    const wrapper = mountDirectory()
    await flushPromises()
    await click(wrapper, '替换文件')
    await selectFile(wrapper, 'updated.txt')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('input[type="file"]').exists()).toBe(true)
    expect((wrapper.get('input[type="file"]').element as HTMLInputElement).files?.[0]?.name).toBe(
      'updated.txt',
    )
    expect(wrapper.text()).toContain('另一页面的有效更新')
    await click(wrapper, '使用刷新后的记录继续替换')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(revisions).toEqual(['1', '1'])
    expect(managementRevisions).toEqual(['7', '8'])
  })

  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('上传明确指定归属，替换携带两种修订，尚未采用时不能打开全文', async () => {
    let items: FileDocumentDto[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/knowledge-bases'))
        return Response.json([newsKnowledgeBase, { ...techKnowledgeBase, is_active: true }])
      if (init?.method === 'POST') {
        const body = init.body as FormData
        expect(body.get('knowledge_base_id')).toBe(techKnowledgeBase.id)
        items = [
          {
            ...file,
            knowledge_base_id: techKnowledgeBase.id,
            knowledge_base_name: techKnowledgeBase.name,
          },
        ]
        return Response.json(items[0], { status: 201 })
      }
      if (init?.method === 'PUT') {
        expect(input).toBe(`/api/file-documents/${file.document_id}/file`)
        const body = init.body as FormData
        expect(body.get('revision')).toBe('1')
        expect(body.get('management_revision')).toBe('7')
        expect(body.has('knowledge_base_id')).toBe(false)
        expect((body.get('file') as File).name).toBe('renamed.md')
        items = [{ ...items[0]!, management_revision: 8, upload_filename: 'renamed.md' }]
        return Response.json(items[0])
      }
      return listResponse(items)
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mountDirectory()
    await flushPromises()
    await click(wrapper, '上传文件')
    await wrapper.get('select').setValue(techKnowledgeBase.id)
    await selectFile(wrapper, 'notes.txt')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('tbody').text()).toContain('等待解析')
    expect(wrapper.get('tbody').text()).not.toContain('可检索')
    expect(
      wrapper
        .findAll('button')
        .find((button) => button.text() === '查看')
        ?.attributes('disabled'),
    ).toBeDefined()
    await click(wrapper, '替换文件')
    expect(wrapper.find('select').exists()).toBe(false)
    await selectFile(wrapper, 'renamed.md')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('tbody').text()).toContain('renamed.md')
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1)
  })

  it('上传超时后提示核对列表，不自动重发新增', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/knowledge-bases'))
        return Promise.resolve(Response.json([newsKnowledgeBase]))
      if (init?.method === 'POST')
        return new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          )
        })
      return Promise.resolve(listResponse([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    const wrapper = mountDirectory()
    await flushPromises()
    await click(wrapper, '上传文件')
    await wrapper.get('select').setValue(newsKnowledgeBase.id)
    await selectFile(wrapper, 'notes.txt')
    await wrapper.get('form').trigger('submit')
    await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS + 1)
    await flushPromises()
    expect(wrapper.text()).toContain('结果尚未确认')
    expect(wrapper.text()).toContain('刷新列表核对')
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('删除失败保留目标和待办，继续删除仍提交相同文档身份', async () => {
    let item = { ...file }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).includes('/knowledge-bases')) return Response.json([newsKnowledgeBase])
        if (init?.method === 'DELETE') {
          expect(input).toBe(
            `/api/file-documents/${file.document_id}?revision=1&management_revision=7`,
          )
          item = { ...file, deletion_pending: true, deletion_error: '索引删除尚未确认' }
          return Response.json(
            { code: 'file_delete_failed', detail: '删除尚未完成', retryable: true },
            { status: 503 },
          )
        }
        return listResponse([item])
      }),
    )
    const wrapper = mountDirectory()
    await flushPromises()
    await click(wrapper, '删除')
    await click(wrapper, '确认删除')
    await flushPromises()
    expect(wrapper.get('tbody').text()).toContain('删除未完成')
    expect(wrapper.get('tbody').text()).toContain('继续删除')
    expect(wrapper.text()).not.toContain('文档及其索引已删除')
  })
})
