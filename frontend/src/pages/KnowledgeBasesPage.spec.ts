import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { newsKnowledgeBase, techKnowledgeBase } from '@/api/knowledge-bases.fixture'
import KnowledgeBasesPage from './KnowledgeBasesPage.vue'

const api = vi.hoisted(() => ({
  listKnowledgeBases: vi.fn(),
  createKnowledgeBase: vi.fn(),
  updateKnowledgeBase: vi.fn(),
}))
vi.mock('@/api/knowledge-bases', () => api)

let wrapper: VueWrapper | undefined
let queryClient: QueryClient

async function mountPage() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  wrapper = mount(KnowledgeBasesPage, {
    attachTo: document.body,
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  api.listKnowledgeBases.mockReset().mockResolvedValue([newsKnowledgeBase, techKnowledgeBase])
  api.createKnowledgeBase.mockReset()
  api.updateKnowledgeBase.mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  queryClient?.clear()
  document.body.replaceChildren()
})

describe('KnowledgeBasesPage', () => {
  it('shows enabled and disabled knowledge bases with their stable keys', async () => {
    const page = await mountPage()
    expect(api.listKnowledgeBases).toHaveBeenCalledWith(true, expect.any(AbortSignal))
    expect(page.findAll('tbody tr')).toHaveLength(2)
    expect(page.text()).toContain('tech-notes')
    expect(page.text()).toContain('已停用')
    expect(page.text()).toContain('未填写说明')
    expect(page.find('[aria-label*="删除"]').exists()).toBe(false)
  })

  it('validates creation, then inserts the confirmed record and restores focus', async () => {
    const page = await mountPage()
    const openButton = page.findAll('button').find((button) => button.text() === '创建知识库')!
    await openButton.trigger('click')
    expect(document.activeElement?.getAttribute('name')).toBe('knowledge-base-name')
    await page.get('form').trigger('submit')
    expect(api.createKnowledgeBase).not.toHaveBeenCalled()
    expect(page.text()).toContain('名称不能为空')
    await page.get('input[name="knowledge-base-name"]').setValue(' 研究资料 ')
    await page.get('input[name="knowledge-base-key"]').setValue('research')
    const created = {
      ...newsKnowledgeBase,
      id: '10000000-0000-4000-8000-000000000012',
      key: 'research',
      name: '研究资料',
      description: null,
    }
    api.createKnowledgeBase.mockResolvedValue(created)
    await page.get('form').trigger('submit')
    await flushPromises()
    expect(api.createKnowledgeBase).toHaveBeenCalledWith({
      key: 'research',
      name: '研究资料',
      description: null,
      is_active: true,
    })
    expect(page.findAll('tbody tr')).toHaveLength(3)
    expect(page.find('form').exists()).toBe(false)
    expect(page.get('[role="status"]').text()).toContain('已创建知识库 研究资料')
    expect(document.activeElement).toBe(openButton.element)
  })

  it('edits name and clears description while preserving the stable key and status', async () => {
    const page = await mountPage()
    await page.get('[aria-label="编辑知识库 新闻"]').trigger('click')
    expect(page.get('input[name="knowledge-base-key"]').attributes('disabled')).toBeDefined()
    expect((page.get('textarea').element as HTMLTextAreaElement).value).toBe(
      newsKnowledgeBase.description,
    )
    await page.get('input[name="knowledge-base-name"]').setValue('新闻档案')
    await page.get('textarea').setValue('')
    api.updateKnowledgeBase.mockResolvedValue({
      ...newsKnowledgeBase,
      name: '新闻档案',
      description: null,
    })
    await page.get('form').trigger('submit')
    await flushPromises()
    expect(api.updateKnowledgeBase).toHaveBeenCalledWith(newsKnowledgeBase.id, {
      name: '新闻档案',
      description: null,
    })
    expect(page.text()).toContain('新闻档案')
  })

  it('retains form data and shows a conflict without adding a row', async () => {
    const page = await mountPage()
    await page
      .findAll('button')
      .find((button) => button.text() === '创建知识库')!
      .trigger('click')
    await page.get('input[name="knowledge-base-name"]').setValue('重复新闻')
    await page.get('input[name="knowledge-base-key"]').setValue('news')
    api.createKnowledgeBase.mockRejectedValue(
      new ApiError({ code: 'knowledge_base_key_conflict', status: 409, message: 'private' }),
    )
    await page.get('form').trigger('submit')
    await flushPromises()
    expect(page.get('[role="alert"]').text()).toContain('该稳定键已被使用')
    expect(page.text()).not.toContain('private')
    expect((page.get('input[name="knowledge-base-name"]').element as HTMLInputElement).value).toBe(
      '重复新闻',
    )
    expect(page.findAll('tbody tr')).toHaveLength(2)
  })

  it('leaves a failed switch unchanged and supports disable then re-enable', async () => {
    const page = await mountPage()
    api.updateKnowledgeBase.mockRejectedValueOnce(new Error('private'))
    await page.get('[aria-label="启用知识库 新闻"]').setValue(false)
    await flushPromises()
    expect((page.get('[aria-label="启用知识库 新闻"]').element as HTMLInputElement).checked).toBe(
      true,
    )
    expect(page.get('[role="alert"]').text()).toContain('暂时不可用')
    api.updateKnowledgeBase.mockResolvedValueOnce({ ...newsKnowledgeBase, is_active: false })
    await page.get('[aria-label="启用知识库 新闻"]').setValue(false)
    await flushPromises()
    expect((page.get('[aria-label="启用知识库 新闻"]').element as HTMLInputElement).checked).toBe(
      false,
    )
    api.updateKnowledgeBase.mockResolvedValueOnce(newsKnowledgeBase)
    await page.get('[aria-label="启用知识库 新闻"]').setValue(true)
    await flushPromises()
    expect(api.updateKnowledgeBase).toHaveBeenLastCalledWith(newsKnowledgeBase.id, {
      is_active: true,
    })
    expect(page.text()).toContain('已启用知识库 新闻')
  })

  it('shows a list failure and can retry to an empty state', async () => {
    api.listKnowledgeBases.mockRejectedValueOnce(new Error('private'))
    const page = await mountPage()
    expect(page.get('[role="alert"]').text()).toContain('暂时不可用')
    api.listKnowledgeBases.mockResolvedValueOnce([])
    await page
      .findAll('button')
      .find((button) => button.text() === '重试')!
      .trigger('click')
    await flushPromises()
    expect(page.text()).toContain('暂无知识库')
  })

  it('shows loading and blocks duplicate mutations while a request is pending', async () => {
    let finishList!: (value: (typeof newsKnowledgeBase)[]) => void
    api.listKnowledgeBases.mockReturnValueOnce(
      new Promise((resolve) => {
        finishList = resolve
      }),
    )
    const page = await mountPage()
    expect(page.get('[role="status"]').text()).toContain('正在读取知识库')
    finishList([newsKnowledgeBase])
    await flushPromises()
    let finishUpdate!: (value: typeof newsKnowledgeBase) => void
    api.updateKnowledgeBase.mockReturnValueOnce(
      new Promise((resolve) => {
        finishUpdate = resolve
      }),
    )
    await page.get('[aria-label="启用知识库 新闻"]').setValue(false)
    await flushPromises()
    expect(page.get('[aria-label="启用知识库 新闻"]').attributes('disabled')).toBeDefined()
    expect(page.get('[aria-label="编辑知识库 新闻"]').attributes('disabled')).toBeDefined()
    page.unmount()
    wrapper = undefined
    queryClient.clear()
    finishUpdate({ ...newsKnowledgeBase, is_active: false })
    await flushPromises()
    expect(queryClient.getQueryData(['knowledge-bases', 'management'])).toBeUndefined()
  })
})
