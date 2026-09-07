import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { newsKnowledgeBase, techKnowledgeBase } from '@/api/knowledge-bases.fixture'
import { boundNewsSource, unboundSource } from '@/api/sources.fixture'
import SourcesPage from './SourcesPage.vue'

const api = vi.hoisted(() => ({
  listSources: vi.fn(),
  bindSource: vi.fn(),
  listKnowledgeBases: vi.fn(),
}))
vi.mock('@/api/sources', () => api)
vi.mock('@/api/knowledge-bases', () => ({ listKnowledgeBases: api.listKnowledgeBases }))

let wrapper: VueWrapper | undefined
let queryClient: QueryClient

async function mountPage() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  wrapper = mount(SourcesPage, {
    attachTo: document.body,
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  api.listSources.mockReset().mockResolvedValue([boundNewsSource, unboundSource])
  api.listKnowledgeBases.mockReset().mockResolvedValue([newsKnowledgeBase])
  api.bindSource.mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  queryClient?.clear()
  document.body.replaceChildren()
})

describe('SourcesPage', () => {
  it('keeps an inactive current binding visible and restores it after failure', async () => {
    api.listKnowledgeBases.mockResolvedValue([
      { ...newsKnowledgeBase, is_active: false },
      { ...techKnowledgeBase, is_active: true },
    ])
    const page = await mountPage()
    expect(api.listKnowledgeBases).toHaveBeenCalledWith(true, expect.any(AbortSignal))
    const select = page.get<HTMLSelectElement>('[aria-label="绑定来源 财经早报"]')
    const selected = select.element.selectedOptions[0]!
    expect(selected.textContent).toContain('新闻（已停用）')
    expect(selected.disabled).toBe(true)
    expect(
      page
        .get('[aria-label="绑定来源 待配置订阅"]')
        .find(`option[value="${newsKnowledgeBase.id}"]`)
        .exists(),
    ).toBe(false)
    api.bindSource.mockRejectedValue(
      new ApiError({ code: 'source_write_recovery_required', status: 409, message: 'private' }),
    )
    await select.setValue(techKnowledgeBase.id)
    await flushPromises()
    expect(select.element.value).toBe(newsKnowledgeBase.id)
    expect(select.element.selectedOptions[0]!.textContent).toContain('已停用')
    expect(page.get('[role="alert"]').text()).toContain('人工核实')
    api.listKnowledgeBases.mockResolvedValue([newsKnowledgeBase, techKnowledgeBase])
    await page.get('[aria-label="刷新来源"]').trigger('click')
    await flushPromises()
    expect(select.element.selectedOptions[0]!.disabled).toBe(false)
    expect(select.element.selectedOptions[0]!.textContent).not.toContain('已停用')
  })

  it('shows the saved binding identity when its option cannot be loaded', async () => {
    api.listKnowledgeBases.mockResolvedValue([])
    const page = await mountPage()
    const select = page.get<HTMLSelectElement>('[aria-label="绑定来源 财经早报"]')
    expect(select.element.value).toBe(newsKnowledgeBase.id)
    expect(select.element.selectedOptions[0]!.textContent).toContain('news（信息不可用）')
  })

  it('shows discovered sources with binding state and sync cursor', async () => {
    const page = await mountPage()
    expect(api.listSources).toHaveBeenCalledWith(expect.any(AbortSignal))
    expect(page.findAll('tbody tr')).toHaveLength(2)
    expect(page.text()).toContain('财经早报')
    expect(page.text()).toContain('freshrss/feed/1')
    expect(page.text()).toContain('待配置')
    expect(page.text()).toContain('未同步')
    const unboundSelect = page.get('[aria-label="绑定来源 待配置订阅"]')
    expect((unboundSelect.element as HTMLSelectElement).value).toBe('')
    const boundSelect = page.get('[aria-label="绑定来源 财经早报"]')
    expect((boundSelect.element as HTMLSelectElement).value).toBe(newsKnowledgeBase.id)
  })

  it('binds an unconfigured source and shows the confirmation', async () => {
    const page = await mountPage()
    api.bindSource.mockResolvedValue({
      ...unboundSource,
      knowledge_base_id: newsKnowledgeBase.id,
      knowledge_base_key: newsKnowledgeBase.key,
      sync_checkpoint: null,
    })
    await page.get('[aria-label="绑定来源 待配置订阅"]').setValue(newsKnowledgeBase.id)
    await flushPromises()
    expect(api.bindSource).toHaveBeenCalledWith(unboundSource.id, newsKnowledgeBase.id)
    expect(page.get('[role="status"]').text()).toContain('已把 待配置订阅 绑定到 新闻')
    expect(page.find('.unbound-badge').exists()).toBe(false)
  })

  it('restores the previous binding and keeps the row when the backend rejects', async () => {
    const page = await mountPage()
    api.bindSource.mockRejectedValue(
      new ApiError({ code: 'source_binding_conflict', status: 409, message: 'private' }),
    )
    const select = page.get('[aria-label="绑定来源 待配置订阅"]')
    await select.setValue(newsKnowledgeBase.id)
    await flushPromises()
    expect(page.get('[role="alert"]').text()).toContain('不能修改绑定')
    expect(page.text()).not.toContain('private')
    expect((select.element as HTMLSelectElement).value).toBe('')
    expect(page.find('.unbound-badge').exists()).toBe(true)
  })

  it('unbinds a bound source and reports the new baseline', async () => {
    const page = await mountPage()
    api.bindSource.mockResolvedValue({
      ...boundNewsSource,
      knowledge_base_id: null,
      knowledge_base_key: null,
      sync_checkpoint: null,
      sync_checkpoint_updated_at: null,
    })
    await page.get('[aria-label="绑定来源 财经早报"]').setValue('')
    await flushPromises()
    expect(api.bindSource).toHaveBeenCalledWith(boundNewsSource.id, null)
    expect(page.get('[role="status"]').text()).toContain('已解除 财经早报 的绑定')
    expect(page.find('.unbound-badge').exists()).toBe(true)
  })

  it('shows a load failure and can retry to the list', async () => {
    api.listSources.mockRejectedValueOnce(new Error('private'))
    const page = await mountPage()
    expect(page.get('[role="alert"]').text()).toContain('暂时不可用')
    api.listSources.mockResolvedValueOnce([boundNewsSource])
    await page
      .findAll('button')
      .find((button) => button.text() === '重试')!
      .trigger('click')
    await flushPromises()
    expect(page.findAll('tbody tr')).toHaveLength(1)
  })

  it('shows loading first and an empty state without sources', async () => {
    let finishList!: (value: (typeof boundNewsSource)[]) => void
    api.listSources.mockReturnValueOnce(
      new Promise((resolve) => {
        finishList = resolve
      }),
    )
    const page = await mountPage()
    expect(page.get('[role="status"]').text()).toContain('正在读取来源')
    finishList([])
    await flushPromises()
    expect(page.text()).toContain('暂无来源')
  })
})
