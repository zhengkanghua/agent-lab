import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { fetchDocument } from '@/api/documents'
import { useDocumentReader } from '../composables/useDocumentReader'

vi.mock('../../../api/documents', () => ({
  fetchDocument: vi.fn(),
}))

const mockedFetchDocument = vi.mocked(fetchDocument)

const result = {
  documentId: '20000000-0000-4000-8000-000000000001',
  contentHash: 'a'.repeat(64),
  title: '政策利率维持不变',
  url: 'https://example.com/news',
  sourceName: '测试来源',
  publishedAt: null,
  authors: [],
  labels: ['宏观'],
  chunkCount: 2,
  bestScore: 0.91,
  bestMatch: {
    id: '10000000-0000-4000-8000-000000000001',
    excerpt: '片段',
    score: 0.91,
    chunkIndex: 0,
    chunkCount: 2,
  },
  additionalMatches: [],
}

const detail = {
  document_id: result.documentId,
  content_hash: 'b'.repeat(64),
  revision: 4,
  title: result.title,
  url: result.url,
  source_name: result.sourceName,
  published_at: null,
  authors: [],
  labels: ['宏观'],
  content_text: '完整正文。',
}

function mountHarness() {
  let reader: ReturnType<typeof useDocumentReader> | undefined
  const Harness = defineComponent({
    setup() {
      reader = useDocumentReader()
      return () => h('button')
    },
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const wrapper = mount(Harness, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  if (!reader) throw new Error('Test harness did not initialize document reader')
  return { wrapper, reader }
}

describe('useDocumentReader', () => {
  beforeEach(() => mockedFetchDocument.mockReset())

  it('does not request full text until open, then caches by document and content hash', async () => {
    mockedFetchDocument.mockResolvedValue(detail)
    const { wrapper, reader } = mountHarness()

    expect(mockedFetchDocument).not.toHaveBeenCalled()
    await reader.open(result)
    await flushPromises()
    expect(mockedFetchDocument).toHaveBeenCalledTimes(1)
    expect(reader.detail.value?.contentText).toBe('完整正文。')
    expect(reader.contentHashMismatch.value).toBe(true)

    await reader.close()
    await reader.open(result)
    await flushPromises()
    expect(mockedFetchDocument).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('exposes retryable 503 failures and recovers on retry', async () => {
    mockedFetchDocument
      .mockRejectedValueOnce(
        new ApiError({
          message: 'unavailable',
          code: 'postgresql_unavailable',
          status: 503,
          retryable: true,
        }),
      )
      .mockResolvedValueOnce(detail)
    const { wrapper, reader } = mountHarness()

    await reader.open(result)
    await flushPromises()
    expect(reader.error.value?.status).toBe(503)
    expect(reader.detail.value).toBeNull()
    await reader.retry()
    await flushPromises()
    expect(reader.error.value).toBeNull()
    expect(reader.detail.value?.contentText).toBe('完整正文。')
    wrapper.unmount()
  })

  it('cancels and isolates an older response when switching documents quickly', async () => {
    let resolveFirst: ((value: typeof detail) => void) | undefined
    let resolveSecond: ((value: typeof detail) => void) | undefined
    const firstDocument = { ...result, documentId: '20000000-0000-4000-8000-000000000002' }
    mockedFetchDocument
      .mockImplementationOnce(({ signal }) => {
        return new Promise((resolve) => {
          signal?.addEventListener('abort', () => undefined)
          resolveFirst = resolve
        })
      })
      .mockImplementationOnce(() => new Promise((resolve) => (resolveSecond = resolve)))
    const { wrapper, reader } = mountHarness()

    await reader.open(result)
    await nextTick()
    await reader.open(firstDocument)
    resolveSecond?.({ ...detail, document_id: firstDocument.documentId, title: '第二篇' })
    await flushPromises()
    expect(reader.detail.value?.title).toBe('第二篇')
    resolveFirst?.(detail)
    await flushPromises()
    expect(reader.detail.value?.title).toBe('第二篇')
    wrapper.unmount()
  })

  it('cancels the older cache key when the same document has a new index hash', async () => {
    let firstSignal: AbortSignal | undefined
    const updatedResult = { ...result, contentHash: 'c'.repeat(64) }
    mockedFetchDocument
      .mockImplementationOnce(({ signal }) => {
        firstSignal = signal
        return new Promise((_resolve, reject) => {
          signal?.addEventListener('abort', () => {
            reject(new DOMException('aborted', 'AbortError'))
          })
        })
      })
      .mockResolvedValueOnce({ ...detail, content_hash: updatedResult.contentHash })
    const { wrapper, reader } = mountHarness()

    await reader.open(result)
    await nextTick()
    expect(firstSignal?.aborted).toBe(false)

    await reader.open(updatedResult)
    await flushPromises()

    expect(firstSignal?.aborted).toBe(true)
    expect(reader.selectedResult.value?.contentHash).toBe(updatedResult.contentHash)
    expect(reader.detail.value?.contentHash).toBe(updatedResult.contentHash)
    wrapper.unmount()
  })

  it('treats UUID and hash casing as the same document identity', async () => {
    const upperCaseResult = {
      ...result,
      documentId: result.documentId.toUpperCase(),
      contentHash: result.contentHash.toUpperCase(),
    }
    mockedFetchDocument.mockResolvedValue({
      ...detail,
      document_id: upperCaseResult.documentId,
      content_hash: upperCaseResult.contentHash,
    })
    const { wrapper, reader } = mountHarness()

    await reader.open(upperCaseResult)
    await flushPromises()

    expect(reader.detail.value?.documentId).toBe(upperCaseResult.documentId)
    expect(reader.contentHashMismatch.value).toBe(false)
    wrapper.unmount()
  })
})
