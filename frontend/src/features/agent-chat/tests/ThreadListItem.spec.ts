import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ThreadListItem from '../components/ThreadListItem.vue'

const THREAD_ID = '30000000-0000-4000-8000-000000000001'

function thread(overrides: Record<string, unknown> = {}) {
  return {
    thread_id: THREAD_ID,
    title: '央行降息了吗',
    created_at: '2026-08-18T00:00:00Z',
    last_active_at: '2026-08-18T00:00:00Z',
    ...overrides,
  }
}

function render(props: Record<string, unknown> = {}) {
  return mount(ThreadListItem, {
    props: { thread: thread(), active: false, deleting: false, ...props },
  })
}

describe('ThreadListItem', () => {
  afterEach(() => vi.useRealTimers())

  it('标题按纯文本渲染，不解析成 HTML', () => {
    // 标题来自用户提问原文。用 v-html 渲染它就是一个直通的 XSS。
    const wrapper = render({ thread: thread({ title: '<img src=x onerror="alert(1)">' }) })

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.get('.title').text()).toBe('<img src=x onerror="alert(1)">')
  })

  it('点整行触发 open，点删除键只触发 remove', async () => {
    const wrapper = render()

    await wrapper.get('.open-button').trigger('click')
    expect(wrapper.emitted('open')).toHaveLength(1)
    expect(wrapper.emitted('remove')).toBeUndefined()

    await wrapper.get('.remove-button').trigger('click')
    expect(wrapper.emitted('remove')).toHaveLength(1)
    // 删除键在行内，但不该顺带触发打开——那会在删除的同时跳进这个会话。
    expect(wrapper.emitted('open')).toHaveLength(1)
  })

  it('当前会话带 aria-current，让读屏用户也知道在哪', () => {
    const active = render({ active: true })
    const inactive = render()

    expect(active.get('.open-button').attributes('aria-current')).toBe('true')
    expect(inactive.get('.open-button').attributes('aria-current')).toBeUndefined()
  })

  it('删除中禁用打开，避免跳进一个正在消失的会话', () => {
    const wrapper = render({ deleting: true })

    expect(wrapper.get('.open-button').attributes('disabled')).toBeDefined()
  })

  it('删除键始终有可读名称', () => {
    // 纯图标按钮没有可见文案，读屏用户只会听到「按钮」。
    expect(render().get('.remove-button').attributes('aria-label')).toBe('删除这个会话')
  })

  it('时间按距离显示：一小时内给分钟，超过一周退回日期', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-18T12:00:00Z'))

    const justNow = render({ thread: thread({ last_active_at: '2026-08-18T11:59:40Z' }) })
    const minutes = render({ thread: thread({ last_active_at: '2026-08-18T11:30:00Z' }) })
    const hours = render({ thread: thread({ last_active_at: '2026-08-18T06:00:00Z' }) })
    const days = render({ thread: thread({ last_active_at: '2026-08-15T12:00:00Z' }) })
    const old = render({ thread: thread({ last_active_at: '2026-07-01T12:00:00Z' }) })

    expect(justNow.get('.time').text()).toBe('刚刚')
    expect(minutes.get('.time').text()).toBe('30 分钟前')
    expect(hours.get('.time').text()).toBe('6 小时前')
    expect(days.get('.time').text()).toBe('3 天前')
    // 超过一周时「48 天前」不比日期更好认。
    expect(old.get('.time').text()).toMatch(/7|月/)
  })

  it('时间戳坏掉时不显示时间，也不显示 NaN', () => {
    const wrapper = render({ thread: thread({ last_active_at: '不是时间' }) })

    expect(wrapper.get('.time').text()).toBe('')
    expect(wrapper.text()).not.toContain('NaN')
  })
})
