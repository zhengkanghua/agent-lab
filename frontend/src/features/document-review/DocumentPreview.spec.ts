import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { reviewPreview } from '@/api/document-review.fixture'
import DocumentPreview from './DocumentPreview.vue'
import DocumentOriginal from './DocumentOriginal.vue'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.body.replaceChildren()
})

it('Chunk 展示真实向量化文本和标题路径，并按结构 ID 定位内容', async () => {
  const wrapper = mount(DocumentPreview, {
    props: { preview: reviewPreview() },
    attachTo: document.body,
  })
  const click = async (label: string) => {
    await wrapper
      .findAll('button')
      .find((button) => button.text() === label)!
      .trigger('click')
  }
  await click('结构目录')
  expect(wrapper.get('nav[aria-label="标题目录"]').text()).toContain('H2备份')
  const target = wrapper
    .findAll('li[tabindex="-1"]')
    .find((item) => item.text().includes('备份保留 14 天'))!.element
  // jsdom 无布局，滚动由后续浏览器核验；这里只保留原生方法形状。
  const scroll = vi.fn()
  const prototype = Object.getPrototypeOf(target)
  const original = prototype.scrollIntoView
  prototype.scrollIntoView = scroll
  try {
    await wrapper
      .findAll('button')
      .find((button) => button.text().startsWith('Chunk'))!
      .trigger('click')
    expect(wrapper.text()).toContain('维护手册 / 备份')
    expect(wrapper.text()).toContain('18 / 512 token')
    expect(wrapper.get('details pre').text()).toBe('维护手册\n备份\n备份保留 14 天。')
    await click('定位结构内容')
    await flushPromises()
    expect(document.activeElement?.textContent).toContain('备份保留 14 天')
    expect(scroll).toHaveBeenCalledOnce()
  } finally {
    prototype.scrollIntoView = original
    wrapper.unmount()
  }
})

it('原始 HTML 仅显示为文本，下载请求经过同域 Cookie 接口', async () => {
  const html = '<script>window.injected=true</script><img src="https://outside.invalid/x">'
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(html, {
      headers: { 'content-type': 'application/octet-stream' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)
  const wrapper = mount(DocumentOriginal, {
    props: {
      processingId: '41000000-0000-4000-8000-000000000003',
      filename: 'source.html',
      stored: true,
    },
  })
  try {
    await wrapper
      .findAll('button')
      .find((item) => item.text() === '查看原件文本')!
      .trigger('click')
    await flushPromises()
    expect(wrapper.get('pre').text()).toBe(html)
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/document-management/candidates/41000000-0000-4000-8000-000000000003/original',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  } finally {
    wrapper.unmount()
  }
})
