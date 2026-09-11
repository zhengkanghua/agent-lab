import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { ref } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

enableAutoUnmount(afterEach)

const session = vi.hoisted(() => ({ user: { value: null as { email: string } | null } }))

vi.mock('@/features/auth/auth-session', () => ({ authSession: { user: session.user } }))

// useLogout 会 import queryClient 与 authSession；这里只关心外壳渲染，logout 行为不走真调用。
vi.mock('@/features/auth/useLogout', () => ({
  useLogout: () => ({
    loggingOut: ref(false),
    logoutError: ref(false),
    logout: vi.fn(),
  }),
}))

import AdminShell from './AdminShell.vue'

function makeRouter(): Router {
  // 外壳不再参与路由（AdminPage 像前台页嵌 AppShell 一样嵌它）；路由表只需要能解析
  // 菜单里的 RouterLink 目标。
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div>search</div>' } },
      {
        path: '/settings/:section?',
        name: 'settings',
        component: { template: '<div>settings</div>' },
      },
      { path: '/admin/:section?', name: 'admin', component: { template: '<div>admin</div>' } },
    ],
  })
  void router.push('/admin/users')
  return router
}

async function mountShell() {
  const router = makeRouter()
  await router.isReady()
  const wrapper = mount(AdminShell, {
    props: { headingTitle: '账号管理', headingSubtitle: '访问控制' },
    slots: { default: '<section class="probe">账号管理正文</section>' },
    global: { plugins: [router] },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  session.user.value = { email: 'admin@example.com' }
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
  vi.stubGlobal('scrollTo', vi.fn())
})

afterEach(() => {
  document.body.replaceChildren()
  vi.unstubAllGlobals()
})

describe('AdminShell', () => {
  it('侧边栏有返回工作台与后台菜单，菜单指向单一路由的分区参数', async () => {
    const { wrapper } = await mountShell()

    const back = wrapper.get('.menu-back')
    expect(back.attributes('href')).toBe('/')
    expect(back.text()).toContain('返回工作台')

    const menu = wrapper.get('.menu-item')
    expect(menu.attributes('href')).toBe('/admin/users')
    expect(menu.text()).toContain('账号管理')

    const labels = wrapper.findAll('.menu-item').map((item) => item.attributes('href'))
    expect(labels).toEqual([
      '/admin/users',
      '/admin/knowledge-bases',
      '/admin/files',
      '/admin/documents',
      '/admin/sources',
      '/admin/scheduled-jobs',
    ])
  })

  it('顶栏标题与分区说明来自 props（一条路由没有逐子 meta 了）', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.get('.topbar-title').text()).toBe('账号管理')
    expect(wrapper.get('.topbar-subtitle').text()).toBe('访问控制')
  })

  it('当前分区对应菜单高亮(router-link-active)', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.get('.menu-item').classes()).toContain('router-link-active')
  })

  it('正文经默认插槽进入内容区，跳转链接指向内容 main', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.get('.probe').text()).toBe('账号管理正文')
    expect(wrapper.get('.skip-link').attributes('href')).toBe('#admin-content')
  })

  it('侧边栏默认收起为抽屉，点汉堡打开、点遮罩关闭', async () => {
    const { wrapper } = await mountShell()

    const toggle = wrapper.get('button[aria-label="打开导航"]')
    expect(wrapper.get('.admin-sidebar').classes()).not.toContain('is-open')
    expect(wrapper.get('.admin-sidebar').attributes('inert')).toBeDefined()

    await toggle.trigger('click')
    expect(wrapper.get('.admin-sidebar').classes()).toContain('is-open')
    expect(wrapper.get('.admin-sidebar').attributes('inert')).toBeUndefined()

    await wrapper.get('.sidebar-overlay').trigger('click')
    expect(wrapper.get('.admin-sidebar').classes()).not.toContain('is-open')
  })

  it('打开抽屉后把焦点送入、Esc 关闭并把焦点还给汉堡键', async () => {
    const { wrapper } = await mountShell()
    const toggle = wrapper.get('button[aria-label="打开导航"]')

    await toggle.trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(wrapper.get('button[aria-label="关闭导航"]').element)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.get('.admin-sidebar').classes()).not.toContain('is-open')
    expect(document.activeElement).toBe(toggle.element)
  })

  it('抽屉内 Tab 在首尾控件之间循环，背景内容在打开时不可聚焦', async () => {
    const { wrapper } = await mountShell()
    await wrapper.get('button[aria-label="打开导航"]').trigger('click')
    await flushPromises()

    const sidebar = wrapper.get('.admin-sidebar').element
    const controls = sidebar.querySelectorAll<HTMLElement>('a[href], button:not(:disabled)')
    const last = controls[controls.length - 1]!
    last.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(controls[0])
    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true }),
    )
    expect(document.activeElement).toBe(last)
    expect(wrapper.get('.admin-main-wrap').attributes('inert')).toBeDefined()
  })

  it.each(['desktop', 'unmount'])('离开抽屉模式时恢复原有滚动状态：%s', async (exit) => {
    document.body.style.overflow = 'auto'
    const { wrapper } = await mountShell()
    await wrapper.get('button[aria-label="打开导航"]').trigger('click')
    expect(document.body.style.overflow).toBe('hidden')

    if (exit === 'desktop') {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 })
      window.dispatchEvent(new Event('resize'))
      await flushPromises()
      expect(wrapper.get('.admin-sidebar').attributes('inert')).toBeUndefined()
      expect(wrapper.get('.admin-main-wrap').attributes('inert')).toBeUndefined()
      expect(wrapper.find('.sidebar-overlay').exists()).toBe(false)
    } else {
      wrapper.unmount()
    }
    expect(document.body.style.overflow).toBe('auto')
    document.body.style.overflow = ''
  })

  it('未登录时不渲染账号邮箱，退出键仍在', async () => {
    session.user.value = null
    const { wrapper } = await mountShell()

    expect(wrapper.find('.account-identity').exists()).toBe(false)
    expect(wrapper.find('button[aria-label="退出登录"]').exists()).toBe(true)
  })

  it('已登录时右上角显示邮箱且指向账号与设置', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.get('.account-identity span').text()).toBe('admin@example.com')
    expect(wrapper.get('.account-identity').attributes('href')).toBe('/settings/account')
  })
})
