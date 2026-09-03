import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

const session = vi.hoisted(() => ({ user: { value: null as { email: string } | null } }))

vi.mock('@/features/auth/auth-session', () => ({ authSession: { user: session.user } }))

// useLogout 会 import queryClient 与 authSession；这里只关心外壳渲染，logout 行为不走真调用。
vi.mock('@/features/auth/useLogout', () => ({
  useLogout: () => ({
    loggingOut: { value: false },
    logoutError: { value: false },
    logout: vi.fn(),
  }),
}))

import AdminShell from './AdminShell.vue'

const PAGE = { template: '<section class="probe">账号管理正文</section>' }

function makeRouter(): Router {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div>search</div>' } },
      { path: '/account', name: 'account', component: { template: '<div>account</div>' } },
      {
        path: '/admin',
        component: AdminShell,
        children: [
          {
            path: 'users',
            name: 'user-admin',
            component: PAGE,
            meta: { title: '账号管理', subtitle: '访问控制' },
          },
          /* 外壳菜单新增了「定时任务」项；本地路由表必须能解析它，
             否则 RouterLink 在 vue-router 5 下直接抛 No match。 */
          { path: 'scheduled-jobs', name: 'scheduled-jobs', component: PAGE },
        ],
      },
    ],
  })
  void router.push('/admin/users')
  return router
}

async function mountShell() {
  const router = makeRouter()
  await router.isReady()
  const wrapper = mount(AdminShell, { global: { plugins: [router] }, attachTo: document.body })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  session.user.value = { email: 'admin@example.com' }
  vi.stubGlobal('scrollTo', vi.fn())
})

afterEach(() => {
  document.body.replaceChildren()
  vi.unstubAllGlobals()
})

describe('AdminShell', () => {
  it('侧边栏有返回工作台与后台菜单', async () => {
    const { wrapper } = await mountShell()

    const back = wrapper.get('.menu-back')
    expect(back.attributes('href')).toBe('/')
    expect(back.text()).toContain('返回工作台')

    const menu = wrapper.get('.menu-item')
    expect(menu.attributes('href')).toBe('/admin/users')
    expect(menu.text()).toContain('账号管理')
  })

  it('从路由 meta 渲染顶栏标题与分区', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.get('.topbar-title').text()).toBe('账号管理')
    expect(wrapper.get('.topbar-subtitle').text()).toBe('访问控制')
  })

  it('当前后台页对应菜单高亮(router-link-active)', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.get('.menu-item').classes()).toContain('router-link-active')
  })

  it('内容区用 RouterView 渲染子页面，跳转链接指向内容 main', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.get('.probe').text()).toBe('账号管理正文')
    expect(wrapper.get('.skip-link').attributes('href')).toBe('#admin-content')
  })

  it('侧边栏默认收起为抽屉，点汉堡打开、点遮罩关闭', async () => {
    const { wrapper } = await mountShell()

    const toggle = wrapper.get('button[aria-label="打开导航"]')
    expect(wrapper.get('.admin-sidebar').classes()).not.toContain('is-open')

    await toggle.trigger('click')
    expect(wrapper.get('.admin-sidebar').classes()).toContain('is-open')

    await wrapper.get('.sidebar-overlay').trigger('click')
    expect(wrapper.get('.admin-sidebar').classes()).not.toContain('is-open')
  })

  it('未登录时不渲染账号邮箱，退出键仍在', async () => {
    session.user.value = null
    const { wrapper } = await mountShell()

    expect(wrapper.find('.account-identity').exists()).toBe(false)
    expect(wrapper.find('button[aria-label="退出登录"]').exists()).toBe(true)
  })

  it('已登录时右上角显示邮箱且指向账号设置', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.get('.account-identity span').text()).toBe('admin@example.com')
    expect(wrapper.get('.account-identity').attributes('href')).toBe('/account')
  })
})
