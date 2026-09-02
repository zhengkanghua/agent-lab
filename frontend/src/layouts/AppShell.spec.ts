import { h } from 'vue'
import { mount } from '@vue/test-utils'
import { Bot, Search } from '@lucide/vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/* vi.mock 的工厂会被提到文件顶部，普通顶层变量在那时还没初始化，所以走 hoisted。
   替身用普通对象而不是 ref：外壳只在渲染时读一次 user.value，本文件每个用例都在
   挂载前把它设好，用不上响应式。 */
const session = vi.hoisted(() => ({ user: { value: null as { email: string } | null } }))

vi.mock('@/features/auth/auth-session', () => ({ authSession: { user: session.user } }))

import AppShell from './AppShell.vue'

type Props = InstanceType<typeof AppShell>['$props']

function testRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div />' } },
      { path: '/agent', name: 'agent-chat', component: { template: '<div />' } },
      { path: '/admin', name: 'user-admin', component: { template: '<div />' } },
      { path: '/account', name: 'account', component: { template: '<div />' } },
    ],
  })
}

const base: Props = {
  brandTitle: 'Signal Desk',
  brandSubtitle: '新闻语义研究台',
  brandLabel: 'Signal Desk 首页',
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
  })
  return { wrapper, router }
}

beforeEach(() => {
  session.user.value = { email: 'admin@example.com' }
})

describe('AppShell', () => {
  it('跳转链接指向调用方的 mainId', async () => {
    const { wrapper } = await mountShell({ mainId: 'account-workspace' })

    const skip = wrapper.get('.skip-link')
    expect(skip.attributes('href')).toBe('#account-workspace')
    expect(skip.text()).toBe('跳到工作台')
  })

  it('brandHref 渲染成原生 a，不带 to 属性', async () => {
    const { wrapper } = await mountShell({ brandHref: '/' })

    const brand = wrapper.get('.brand-lockup')
    expect(brand.element.tagName).toBe('A')
    expect(brand.attributes('href')).toBe('/')
    // 给 a 传 to 会落成一个没用的 DOM 属性。
    expect(brand.attributes('to')).toBeUndefined()
  })

  it('brandTo 渲染成路由链接，href 由路由解析', async () => {
    const { wrapper } = await mountShell({ brandTo: { name: 'agent-chat' } })

    const brand = wrapper.get('.brand-lockup')
    expect(brand.element.tagName).toBe('A')
    expect(brand.attributes('href')).toBe('/agent')
  })

  it('品牌文案与无障碍名分开：读屏读 label，不读拼起来的两行', async () => {
    const { wrapper } = await mountShell({ brandHref: '/' })

    const brand = wrapper.get('.brand-lockup')
    expect(brand.attributes('aria-label')).toBe('Signal Desk 首页')
    expect(brand.get('.brand-copy strong').text()).toBe('Signal Desk')
    expect(brand.get('.brand-copy small').text()).toBe('新闻语义研究台')
  })

  it('brand-icon 插槽落在品牌方块里', async () => {
    const { wrapper } = await mountShell({}, { 'brand-icon': () => h(Search, { size: 19 }) })

    expect(wrapper.get('.brand-mark svg').element.tagName).toBe('svg')
  })

  it('navLinks 渲染成路由链接，label 同时作为无障碍名与 title', async () => {
    const { wrapper } = await mountShell({
      navLinks: [{ to: { name: 'agent-chat' }, label: 'Agent 对话', icon: Bot }],
    })

    const link = wrapper.get('.topbar-nav-link')
    expect(link.attributes('href')).toBe('/agent')
    expect(link.attributes('aria-label')).toBe('Agent 对话')
    expect(link.attributes('title')).toBe('Agent 对话')
    expect(link.get('svg').element.tagName).toBe('svg')
  })

  it('visible 为 false 的入口不渲染，省略即渲染', async () => {
    const { wrapper } = await mountShell({
      navLinks: [
        { to: { name: 'agent-chat' }, label: '隐藏的', icon: Bot, visible: false },
        { to: { name: 'user-admin' }, label: '显示的', icon: Bot },
      ],
    })

    const labels = wrapper.findAll('.topbar-nav-link').map((link) => link.attributes('aria-label'))
    expect(labels).toEqual(['显示的'])
  })

  it('nav 插槽的内容排在 navLinks 之前', async () => {
    const { wrapper } = await mountShell(
      { navLinks: [{ to: { name: 'search' }, label: '图标入口', icon: Bot }] },
      { nav: () => h('a', { class: 'probe' }, '返回') },
    )

    const control = wrapper.get('.account-control').element
    const order = Array.from(control.children)
      .map((child) =>
        child.classList.contains('probe')
          ? 'slot'
          : child.classList.contains('topbar-nav-link')
            ? 'link'
            : 'other',
      )
      .filter((mark) => mark !== 'other')
    expect(order).toEqual(['slot', 'link'])
  })

  it('省略 modeLabel 时整块不渲染', async () => {
    const { wrapper } = await mountShell()
    expect(wrapper.find('.mode-note').exists()).toBe(false)

    const withMode = await mountShell({ modeLabel: '模型生成答案', modeDetail: '只读检索' })
    expect(withMode.wrapper.get('.mode-note').text()).toContain('模型生成答案')
    expect(withMode.wrapper.get('.mode-detail').text()).toBe('只读检索')
  })

  it('只给 modeLabel 时不渲染 mode-detail', async () => {
    const { wrapper } = await mountShell({ modeLabel: '按新闻分组' })

    expect(wrapper.get('.mode-note').text()).toContain('按新闻分组')
    expect(wrapper.find('.mode-detail').exists()).toBe(false)
  })

  it('省略 footerBrand 时不渲染页脚', async () => {
    const { wrapper } = await mountShell()
    expect(wrapper.find('.site-footer').exists()).toBe(false)

    const withFooter = await mountShell({ footerBrand: 'Signal Desk', footerNote: '只读访问' })
    const spans = withFooter.wrapper.findAll('.footer-inner span').map((span) => span.text())
    expect(spans).toEqual(['Signal Desk', '只读访问'])
  })

  it('未登录时不渲染账号邮箱，退出键仍在', async () => {
    session.user.value = null
    const { wrapper } = await mountShell()

    expect(wrapper.find('.account-identity').exists()).toBe(false)
    expect(wrapper.find('button[aria-label="退出登录"]').exists()).toBe(true)
  })

  it('已登录时显示邮箱', async () => {
    const { wrapper } = await mountShell()

    expect(wrapper.get('.account-identity').text()).toContain('admin@example.com')
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
    // 提示是瞬时出现的，不带 role 读屏不会念出来。
    expect(alert.attributes('role')).toBe('alert')
  })

  /* compactAt 与 background 只改类名，媒体查询在 jsdom 里读不出胜负；
     断言类名是为了保证两页各自拿到自己那套断点。 */
  it('compactAt 默认 560，可切到 720', async () => {
    const { wrapper } = await mountShell()
    expect(wrapper.classes()).toContain('compact-560')

    await wrapper.setProps({ compactAt: 720 })
    expect(wrapper.classes()).toContain('compact-720')
    expect(wrapper.classes()).not.toContain('compact-560')
  })

  it('background 默认不加 bg-raised', async () => {
    const { wrapper } = await mountShell()
    expect(wrapper.classes()).not.toContain('bg-raised')

    await wrapper.setProps({ background: 'raised' })
    expect(wrapper.classes()).toContain('bg-raised')
  })

  it('默认插槽的 main 落在顶栏之后、页脚之前', async () => {
    const { wrapper } = await mountShell({ footerBrand: 'Signal Desk' })

    const root = wrapper.element as HTMLElement
    const marks = Array.from(root.children).map((child) => child.tagName.toLowerCase())
    expect(marks).toEqual(['a', 'header', 'main', 'footer'])
  })
})
