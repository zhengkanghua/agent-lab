import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import DocumentReader from '../components/DocumentReader.vue'

const result = {
  documentId: '20000000-0000-4000-8000-000000000001',
  contentHash: 'a'.repeat(64),
  title: '政策利率维持不变',
  url: 'https://example.com/news',
  sourceName: '测试来源',
  publishedAt: null,
  labels: [],
  authors: [],
  chunkCount: 1,
  bestScore: 0.91,
  bestMatch: {
    id: '10000000-0000-4000-8000-000000000001',
    excerpt: '片段',
    score: 0.91,
    chunkIndex: 0,
    chunkCount: 1,
  },
  additionalMatches: [],
}

const detail = {
  documentId: result.documentId,
  contentHash: 'b'.repeat(64),
  revision: 2,
  title: result.title,
  url: result.url,
  sourceName: result.sourceName,
  publishedAt: null,
  authors: [],
  labels: [],
  contentText: '<script>not markup</script>\n正文',
}

describe('DocumentReader', () => {
  afterEach(() => {
    document.body.replaceChildren()
    vi.restoreAllMocks()
  })

  it('renders plain text, version warning, and closes with Escape', async () => {
    const wrapper = mount(DocumentReader, {
      attachTo: document.body,
      props: {
        open: true,
        result,
        detail,
        loading: false,
        error: null,
        hashMismatch: true,
      },
    })

    expect(document.body.textContent).toContain('该新闻已更新，当前全文与搜索时的索引版本不同。')
    expect(document.body.textContent).toContain('<script>not markup</script>')
    expect(document.body.querySelector('script')).toBeNull()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps loading and error states inside the reader panel', async () => {
    const loading = mount(DocumentReader, {
      attachTo: document.body,
      props: {
        open: true,
        result,
        detail: null,
        loading: true,
        error: null,
        hashMismatch: false,
      },
    })
    expect(document.body.textContent).toContain('正在读取全文')
    loading.unmount()

    const error = mount(DocumentReader, {
      attachTo: document.body,
      props: {
        open: true,
        result,
        detail: null,
        loading: false,
        error: new ApiError({
          status: 503,
          code: 'postgresql_unavailable',
          message: 'unavailable',
          retryable: true,
        }),
        hashMismatch: false,
      },
    })
    expect(document.body.textContent).toContain('全文服务暂时不可用')
    const retryButton = document.body.querySelector<HTMLButtonElement>('.reader-retry')
    expect(retryButton).not.toBeNull()
    retryButton?.click()
    expect(error.emitted('retry')).toHaveLength(1)
    error.unmount()
  })

  /* 收纳键换成 BaseIconButton 后，ref 拿到的是组件实例而不是 button 元素。
     少了这条，聚焦悄悄失效也没人知道——而抽屉是模态的，焦点不进去键盘就出不来。 */
  it('打开后把焦点交给收纳键', async () => {
    const wrapper = mount(DocumentReader, {
      attachTo: document.body,
      props: {
        open: false,
        result,
        detail,
        loading: false,
        error: null,
        hashMismatch: false,
      },
    })

    await wrapper.setProps({ open: true })
    await nextTick()

    const closeButton = document.body.querySelector('button[aria-label="关闭全文"]')
    expect(closeButton).not.toBeNull()
    expect(document.activeElement).toBe(closeButton)
    wrapper.unmount()
  })

  it('explains a non-retryable 404 without offering a futile retry', () => {
    const wrapper = mount(DocumentReader, {
      attachTo: document.body,
      props: {
        open: true,
        result,
        detail: null,
        loading: false,
        error: new ApiError({
          status: 404,
          code: 'unknown_error',
          message: 'not found',
          retryable: false,
        }),
        hashMismatch: false,
      },
    })

    expect(document.body.textContent).toContain('未找到这篇新闻全文')
    expect(document.body.querySelector('.reader-retry')).toBeNull()
    wrapper.unmount()
  })
})
