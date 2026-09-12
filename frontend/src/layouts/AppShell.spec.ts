import { h } from 'vue'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

enableAutoUnmount(afterEach)

/* vi.mock 的工厂会被提到文件顶部，普通顶层变量在那时还没初始化，所以走 hoisted。
   替身用普通对象而不是 ref：外壳只在渲染时读一次 user.value，本文件每个用例都在
   挂载前把它设好，用不上响应式。 */
const session = vi.hoisted(() => ({
  user: { value: null as { email: string; is_superuser?: boolean } | null },
}))

vi.mock('@/features/auth/auth-session', () => ({ authSession: { user: session.user } }))

import AppShell from './AppShell.vue'

type Props = InstanceType<typeof AppShell>['$props']

function testRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div />' } },
      { path: '/agent', name: 'agent-chat', component: { template: '<div />' } },
      { path: '/admin', name: 'admin', component: { template: '<div />' } },
      { path: '/settings/:section?', name: 'settings', component: { template: '<div />' } },
    ],
  })
}

const base: Props = {
  active: 'search',
  mainId: 'workspace',
  skipLabel: '跳到工作台',
}

async function mountShell(props: Partial<Props> = {}, slots: Record<string, unknown> = {}) {
  const router = testRouter()
  await router.push('/')
  await router.isReady()
  const wrapper = mount(AppShell, {
    props: { ...base, ...props },
    slots: { default: () => h('main', { id: 'workspace' }, '正文'), ...slots },
    global: { plugins: [router] },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  session.user.value = { email: 'admin@example.com' }
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 })
})

afterEach(() => {
  document.body.replaceChildren()
})

describe('AppShell', () => {
  it('跳转链接指向调用方的 mainId', async () => {
    const { wrapper } = await mountShell({ mainId: 'account-workspace' })

    const skip = wrapper.get('.skip-link')
    expect(skip.attributes('href')).toBe('#account-workspace')
    expect(skip.text()).toBe('跳到工作台')
  })

  it('品牌是全站唯一落点：链接回检索首页，文案与无障碍名分开', async () => {
    const { wrapper } = await mountShell()

    const brand = wrapper.get('.brand-lockup')
    expect(brand.attributes('href')).toBe('/')
    expect(brand.attributes('aria-label')).toBe('Signal Desk 首页')
    expect(brand.get('.brand-text strong').text()).toBe('Signal Desk')
    expect(brand.get('.brand-text small').text()).toBe('知识库工作台')
  })

  it('主导航写死在外壳里：检索人人可见，Agent 入口只给超管', async () => {
    const { wrapper } = await mountShell()
    expect(wrapper.findAll('.sidebar-nav .nav-item').map((item) => item.text())).toEqual([
      '语义检索',
    ])
    expect(wrapper.find('a[aria-label="后台管理"]').exists()).toBe(false)

    session.user.value = { email: 'admin@example.com', is_superuser: true }
    const admin = await mountShell()
    expect(admin.wrapper.findAll('.sidebar-nav .nav-item').map((item) => item.text())).toEqual([
      '语义检索',
      'Agent 对话',
    ])
    expect(admin.wrapper.get('a[aria-label="后台管理"]').attributes('href')).toBe('/admin')
  })

  it('active 决定哪枚导航亮，并给出 aria-current', async () => {
    session.user.value = { email: 'admin@example.com', is_superuser: true }
    const { wrapper } = await mountShell({ active: 'agent' })

    const items = wrapper.findAll('.sidebar-nav .nav-item')
    expect(items[0]?.attributes('aria-current')).toBeUndefined()
    expect(items[1]?.attributes('aria-current')).toBe('page')
    expect(items[1]?.classes()).toContain('is-active')
  })

  it('primaryLabel 渲染主操作，点击只发事件（做什么由页面决定）', async () => {
    const { wrapper } = await mountShell({ primaryLabel: '新对话' })

    const primary = wrapper.get('.primary-button')
    expect(primary.text()).toContain('新对话')
    await primary.trigger('click')
    expect(wrapper.emitted('primary')).toHaveLength(1)
  })

  it('省略 primaryLabel 时主操作整块不渲染', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.find('.primary-button').exists()).toBe(false)
  })

  it('#rail 插槽落在侧栏的滚动区里', async () => {
    const { wrapper } = await mountShell(
      {},
      { rail: () => h('nav', { class: 'probe-rail' }, '会话') },
    )

    expect(wrapper.get('.sidebar-rail .probe-rail').text()).toBe('会话')
  })

  it('设置入口在底栏，active=settings 时亮', async () => {
    const { wrapper } = await mountShell({ active: 'settings' })

    const link = wrapper.get('.sidebar-footer .nav-item')
    expect(link.attributes('href')).toBe('/settings')
    expect(link.classes()).toContain('is-active')
    expect(link.attributes('aria-current')).toBe('page')
  })

  it('未登录时不渲染账号邮箱，退出键仍在', async () => {
    session.user.value = null
    const { wrapper } = await mountShell()

    expect(wrapper.find('.account-identity').exists()).toBe(false)
    expect(wrapper.find('button[aria-label="退出登录"]').exists()).toBe(true)
  })

  it('已登录时显示邮箱，入口指向设置中心的账号分区', async () => {
    const { wrapper } = await mountShell()

    const identity = wrapper.get('.account-identity')
    expect(identity.text()).toContain('admin@example.com')
    expect(identity.attributes('href')).toBe('/settings/account')
  })

  it('点退出键只发事件，退登逻辑不在外壳里', async () => {
    const { wrapper } = await mountShell()

    await wrapper.get('button[aria-label="退出登录"]').trigger('click')

    expect(wrapper.emitted('logout')).toHaveLength(1)
  })

  it('loggingOut 时退出键禁用，连点不会再发事件', async () => {
    const { wrapper } = await mountShell({ loggingOut: true })

    const button = wrapper.get('button[aria-label="退出登录"]')
    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click')
    expect(wrapper.emitted('logout')).toBeUndefined()
  })

  it('logoutError 才渲染提示，且带 role=alert', async () => {
    const { wrapper } = await mountShell()
    expect(wrapper.find('.logout-error').exists()).toBe(false)

    await wrapper.setProps({ logoutError: true })
    const alert = wrapper.get('.logout-error')
    expect(alert.text()).toBe('退出失败')
    expect(alert.attributes('role')).toBe('alert')
  })

  it('桌面端侧栏常驻，抽屉相关态不生效', async () => {
    const { wrapper } = await mountShell()

    const sidebar = wrapper.get('.shell-sidebar')
    expect(sidebar.attributes('inert')).toBeUndefined()
    expect(sidebar.attributes('aria-hidden')).toBeUndefined()
    expect(sidebar.attributes('role')).toBeUndefined()
    expect(wrapper.find('.sidebar-overlay').exists()).toBe(false)
  })

  it('窄屏收起为抽屉：点汉堡打开、点遮罩关闭', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    const { wrapper } = await mountShell()

    const toggle = wrapper.get('button[aria-label="打开导航"]')
    expect(wrapper.get('.shell-sidebar').classes()).not.toContain('is-open')
    expect(wrapper.get('.shell-sidebar').attributes('inert')).toBeDefined()

    await toggle.trigger('click')
    expect(wrapper.get('.shell-sidebar').classes()).toContain('is-open')
    expect(wrapper.get('.shell-sidebar').attributes('inert')).toBeUndefined()

    await wrapper.get('.sidebar-overlay').trigger('click')
    expect(wrapper.get('.shell-sidebar').classes()).not.toContain('is-open')
  })

  it('打开抽屉后把焦点送入、Esc 关闭并把焦点还给汉堡键', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    const { wrapper } = await mountShell()
    const toggle = wrapper.get('button[aria-label="打开导航"]')

    await toggle.trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(wrapper.get('button[aria-label="关闭导航"]').element)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.get('.shell-sidebar').classes()).not.toContain('is-open')
    expect(document.activeElement).toBe(toggle.element)
  })

  it('抽屉内 Tab 在首尾控件之间循环，背景内容在打开时不可聚焦', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    const { wrapper } = await mountShell()
    await wrapper.get('button[aria-label="打开导航"]').trigger('click')
    await flushPromises()

    const sidebar = wrapper.get('.shell-sidebar').element
    const controls = sidebar.querySelectorAll<HTMLElement>('a[href], button:not(:disabled)')
    const last = controls[controls.length - 1]!
    last.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(controls[0])
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }),
    )
    expect(document.activeElement).toBe(last)
    expect(wrapper.get('.shell-body').attributes('inert')).toBeDefined()
  })

  it.each(['desktop', 'unmount'])('离开抽屉模式时恢复原有滚动状态：%s', async (exit) => {
    document.body.style.overflow = 'auto'
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    const { wrapper } = await mountShell()
    await wrapper.get('button[aria-label="打开导航"]').trigger('click')
    expect(document.body.style.overflow).toBe('hidden')

    if (exit === 'desktop') {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 })
      window.dispatchEvent(new Event('resize'))
      await flushPromises()
      expect(wrapper.get('.shell-sidebar').attributes('inert')).toBeUndefined()
      expect(wrapper.get('.shell-body').attributes('inert')).toBeUndefined()
      expect(wrapper.find('.sidebar-overlay').exists()).toBe(false)
    } else {
      wrapper.unmount()
    }
    expect(document.body.style.overflow).toBe('auto')
    document.body.style.overflow = ''
  })

  it('抽屉里点主操作：发事件并顺手关抽屉', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    const { wrapper } = await mountShell({ primaryLabel: '新检索' })
    await wrapper.get('button[aria-label="打开导航"]').trigger('click')

    await wrapper.get('.primary-button').trigger('click')

    expect(wrapper.emitted('primary')).toHaveLength(1)
    expect(wrapper.get('.shell-sidebar').classes()).not.toContain('is-open')
  })
})
