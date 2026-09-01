import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ThreadSidebar from '../components/ThreadSidebar.vue'

function thread(index: number) {
  return {
    thread_id: `30000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
    title: `会话 ${index}`,
    created_at: '2026-08-18T00:00:00Z',
    last_active_at: '2026-08-18T00:00:00Z',
  }
}

function render(props: Record<string, unknown> = {}) {
  return mount(ThreadSidebar, {
    props: {
      threads: [thread(1), thread(2)],
      total: 2,
      activeThreadId: null,
      listState: 'ready',
      listError: null,
      hasMore: false,
      hasPrevious: false,
      isEmpty: false,
      deletingThreadIds: new Set<string>(),
      ...props,
    },
  })
}

describe('ThreadSidebar', () => {
  it('用 nav 地标暴露自己，读屏用户能直接跳过来', () => {
    // 用 aside 的话它会被播报成「补充内容」，而这一列是这一页的主要导航方式。
    const wrapper = render()

    expect(wrapper.get('nav').attributes('aria-label')).toBe('会话记录')
  })

  it('列出每个会话，并把当前那个标出来', () => {
    const wrapper = render({ activeThreadId: thread(2).thread_id })

    const items = wrapper.findAll('.thread-item')
    expect(items).toHaveLength(2)
    expect(items[0]?.classes()).not.toContain('is-active')
    expect(items[1]?.classes()).toContain('is-active')
  })

  it('把子组件的事件带着会话信息转出去', async () => {
    const wrapper = render()

    await wrapper.findAll('.open-button')[1]?.trigger('click')
    await wrapper.findAll('.remove-button')[0]?.trigger('click')

    expect(wrapper.emitted('open')?.[0]).toEqual([thread(2).thread_id])
    // remove 带整个对象而不只是 id：确认弹窗要显示标题。
    expect(wrapper.emitted('remove')?.[0]?.[0]).toMatchObject({ title: '会话 1' })
  })

  it('载入中显示进度而不是空列表', () => {
    const wrapper = render({ listState: 'loading', threads: [] })

    expect(wrapper.get('.sidebar-state').text()).toContain('正在读取')
    expect(wrapper.find('.thread-list').exists()).toBe(false)
  })

  it('空态告诉用户怎么产生第一条记录', () => {
    // 一句「暂无会话」只是重复了用户已经看到的事实。
    const wrapper = render({ isEmpty: true, threads: [], total: 0 })

    expect(wrapper.get('.sidebar-state').text()).toContain('发出第一个问题')
  })

  it('读取失败时给出文案和重试入口，且是 alert 角色', () => {
    const wrapper = render({
      listState: 'error',
      threads: [],
      listError: { title: '会话记录暂时读不出来', description: '稍后重试即可。', retryable: true },
    })

    const error = wrapper.get('.sidebar-error')
    expect(error.attributes('role')).toBe('alert')
    expect(error.text()).toContain('会话记录暂时读不出来')
    expect(wrapper.find('.retry').exists()).toBe(true)
  })

  it('点重试转出 reload 事件', async () => {
    const wrapper = render({
      listState: 'error',
      threads: [],
      listError: { title: '失败', description: '重试', retryable: true },
    })

    await wrapper.get('.retry').trigger('click')

    expect(wrapper.emitted('reload')).toHaveLength(1)
  })

  it('只有一页时不显示翻页条', () => {
    expect(render().find('.pager').exists()).toBe(false)
  })

  it('翻页按钮按可用性禁用，并各自转出事件', async () => {
    const wrapper = render({ hasMore: true, hasPrevious: false })

    const buttons = wrapper.findAll('.pager button')
    expect(buttons[0]?.attributes('disabled')).toBeDefined()
    expect(buttons[1]?.attributes('disabled')).toBeUndefined()

    await buttons[1]?.trigger('click')
    expect(wrapper.emitted('nextPage')).toHaveLength(1)
  })

  it('总数为 0 时不显示计数徽标', () => {
    const withCount = render({ total: 2 })
    const withoutCount = render({ total: 0, isEmpty: true, threads: [] })

    expect(withCount.get('.count').text()).toBe('2')
    expect(withoutCount.find('.count').exists()).toBe(false)
  })

  it('「新对话」按钮转出事件', async () => {
    const wrapper = render()

    await wrapper.get('.sidebar-head button').trigger('click')

    expect(wrapper.emitted('newConversation')).toHaveLength(1)
  })

  it('正在删除的那一行进入忙态', () => {
    const wrapper = render({ deletingThreadIds: new Set([thread(1).thread_id]) })

    expect(wrapper.findAll('.thread-item')[0]?.classes()).toContain('is-deleting')
    expect(wrapper.findAll('.thread-item')[1]?.classes()).not.toContain('is-deleting')
  })
})
