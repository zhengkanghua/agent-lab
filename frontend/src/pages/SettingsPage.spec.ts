import { flushPromises, mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  // 设置页会读写个人偏好，也会读用量。
  fetchPreferences: vi.fn(),
  savePreferences: vi.fn(),
  fetchUsageSummary: vi.fn(),
  fetchUsageRecords: vi.fn(),
  fetchUsageModels: vi.fn(),
}))

vi.mock('@/api/preferences', () => ({
  fetchPreferences: api.fetchPreferences,
  savePreferences: api.savePreferences,
}))

vi.mock('@/api/usage', () => ({
  USAGE_PAGE_SIZE: 50,
  fetchUsageSummary: api.fetchUsageSummary,
  fetchUsageRecords: api.fetchUsageRecords,
  fetchUsageModels: api.fetchUsageModels,
}))

/** 后端那一份的形状：字段名与前端不同，默认是两个数量参数的默认档。 */
function remote(overrides: Record<string, unknown> = {}) {
  return { documentLimit: 10, matchesPerDocument: 3, ...overrides }
}

const session = vi.hoisted(() => ({
  user: { value: null as { email: string; is_superuser: boolean } | null },
  logout: vi.fn(),
}))

vi.mock('@/features/auth/auth-session', () => ({
  authSession: { user: session.user, status: { value: 'authenticated' }, logout: session.logout },
}))

import SettingsPage from './SettingsPage.vue'
import { usePreferences } from '@/features/settings'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div />' } },
      // 外壳侧栏给超管渲染 Agent 导航，路由表里没有它会在挂载时就 resolve 失败。
      { path: '/agent', name: 'agent-chat', component: { template: '<div />' } },
      { path: '/settings/:section?', name: 'settings', component: SettingsPage },
      { path: '/admin/:section?', name: 'admin', component: { template: '<div />' } },
    ],
  })
}

async function mountAt(path: string) {
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(
    { template: '<RouterView />' },
    {
      attachTo: document.body,
      global: {
        // 用量分区走 Vue Query；其余分区用不到它，装上也不影响。
        plugins: [
          router,
          [
            VueQueryPlugin,
            {
              queryClient: new QueryClient({
                defaultOptions: { queries: { retry: false, gcTime: 0 } },
              }),
            },
          ],
        ],
      },
    },
  )
  await flushPromises()
  return { wrapper, router }
}

describe('SettingsPage', () => {
  beforeEach(() => {
    usePreferences().resetForTests()
    api.fetchPreferences.mockReset()
    api.fetchPreferences.mockResolvedValue(remote())
    api.savePreferences.mockReset()
    api.savePreferences.mockImplementation((next: unknown) => Promise.resolve(next))
    session.user.value = { email: 'admin@example.com', is_superuser: true }
    session.logout.mockReset()
    api.fetchUsageSummary.mockReset()
    api.fetchUsageSummary.mockResolvedValue({
      inputTokens: 100,
      outputTokens: 40,
      cachedTokens: null,
      totalTokens: 140,
      callCount: 1,
    })
    api.fetchUsageRecords.mockReset()
    api.fetchUsageRecords.mockResolvedValue({ items: [], hasMore: false })
    api.fetchUsageModels.mockReset()
    api.fetchUsageModels.mockResolvedValue(['gpt-x'])
    Element.prototype.scrollIntoView = vi.fn()
    window.scrollTo = vi.fn()
  })

  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
  })

  it('默认落在账号分区：登录信息与改密表单都在', async () => {
    const { wrapper } = await mountAt('/settings')

    expect(wrapper.find('#account-heading').exists()).toBe(true)
    expect(wrapper.text()).toContain('admin@example.com')
    expect(wrapper.text()).toContain('修改密码')
    wrapper.unmount()
  })

  it('用量分区可以从设置中心打开，导航里也有它', async () => {
    const { wrapper } = await mountAt('/settings/usage')

    expect(wrapper.find('#usage-heading').exists()).toBe(true)
    expect(wrapper.text()).toContain('调用次数')
    expect(wrapper.find('#usage-model').text()).toContain('gpt-x')
    const nav = wrapper.get('.settings-nav')
    expect(nav.text()).toContain('用量')
    wrapper.unmount()
  })

  it('桌面端浮层自带关闭键：它是离开设置中心的可见出口', async () => {
    const { wrapper, router } = await mountAt('/settings/account')

    // 2026-10：遮罩盖满整页、含侧栏，侧栏不再是第二出口（见 SettingsPage 的样式注释），
    // 所以这枚键必须真的在、而且真的能关。
    const close = wrapper.get('.panel-close')
    expect(close.attributes('aria-label')).toBe('关闭设置')
    await close.trigger('click')
    await flushPromises()
    // 无来路时回默认入口（Agent 对话）——与 `/` 的落点一致。
    expect(router.currentRoute.value.path).toBe('/agent')
    wrapper.unmount()
  })

  it('检索偏好分区提供两个数量参数，改动立即写进偏好单例', async () => {
    const { wrapper } = await mountAt('/settings/search')

    expect(wrapper.find('#search-prefs-heading').exists()).toBe(true)
    const selects = wrapper.findAll('select')
    expect(selects).toHaveLength(2)

    await selects[0]!.setValue('20')
    await selects[1]!.setValue('1')

    const { preferences } = usePreferences()
    expect(preferences.documentLimit).toBe(20)
    expect(preferences.matchesPerDocument).toBe(1)
    // 改动即保存：两次改动各发一次请求，不是等某个「保存」按钮。
    expect(api.savePreferences).toHaveBeenCalledTimes(2)
    expect(api.savePreferences.mock.calls[1]![0]).toMatchObject({
      documentLimit: 20,
      matchesPerDocument: 1,
    })
    wrapper.unmount()
  })

  it('偏好还没读回来时两个下拉禁用，改动不会把服务端配置覆盖成默认值', async () => {
    // 这条守的是一次真实的数据丢失：写入是整体覆盖（提交 `{...preferences}` 整份），
    // 读还没回来时 store 里是默认值，此时放行一次改动就会把账号上的两个数量参数一起
    // 覆盖成默认——用户只是想调个下拉。用「永远不 resolve」把加载态钉住。
    let release!: (value: unknown) => void
    api.fetchPreferences.mockImplementation(() => new Promise((resolve) => (release = resolve)))
    const { wrapper } = await mountAt('/settings/search')

    const selects = wrapper.findAll('select')
    expect(selects).toHaveLength(2)
    expect(selects.every((each) => each.attributes('disabled') !== undefined)).toBe(true)

    // 即使有人绕过 disabled 直接触发 change，也不该发出请求。
    await selects[0]!.setValue('20')
    expect(api.savePreferences).not.toHaveBeenCalled()

    // 读回来之后才可编辑。
    release(remote({ documentLimit: 10 }))
    await flushPromises()
    expect(
      wrapper.findAll('select').every((each) => each.attributes('disabled') === undefined),
    ).toBe(true)
    wrapper.unmount()
  })

  it('读偏好失败时禁用编辑并说明原因，不静默用默认值覆盖', async () => {
    api.fetchPreferences.mockRejectedValue(new Error('boom'))
    const { wrapper } = await mountAt('/settings/search')

    expect(wrapper.text()).toContain('读取账号偏好失败')
    await wrapper.findAll('select')[0]!.setValue('20')
    expect(api.savePreferences).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('普通账号能进设置页，导航有三项', async () => {
    // 原先这里断言的是「普通用户被拉回账号分区」。那条行为随 ADR 0030 作废：设置中心的
    // 三个分区（账号安全、检索偏好、用量）都对所有登录账号开放。
    session.user.value = { email: 'user@example.com', is_superuser: false }
    const { wrapper } = await mountAt('/settings')

    expect(wrapper.find('#account-heading').exists()).toBe(true)
    const labels = wrapper.findAll('.section-link').map((link) => link.text())
    expect(labels).toHaveLength(3)
    expect(labels.join()).toContain('账号安全')
    expect(labels.join()).toContain('检索偏好')
    expect(labels.join()).toContain('用量')
    wrapper.unmount()
  })

  it('旧深链 /settings/agent 被改写到账号分区', async () => {
    // 收藏里可能还留着这条地址。非法分区走的是既有机制：回落到 account 并把 URL 一起改写，
    // 不换 URL 静默吞掉会让地址栏与界面各说一句话。
    const { wrapper, router } = await mountAt('/settings/agent')

    expect(wrapper.find('#account-heading').exists()).toBe(true)
    expect(router.currentRoute.value.path).toBe('/settings/account')
    wrapper.unmount()
  })

  it('后台入口不在设置导航里：设置导航只放三个设置分区', async () => {
    const { wrapper } = await mountAt('/settings')

    const labels = wrapper.findAll('.section-link').map((link) => link.text())
    expect(labels.join()).toContain('账号安全')
    expect(labels.join()).not.toContain('账号管理')
    expect(labels.join()).not.toContain('定时任务')
    wrapper.unmount()
  })

  it('桌面端是居中浮层：dialog 语义，Esc 关回上一页', async () => {
    const { wrapper, router } = await mountAt('/settings/account')

    const panel = wrapper.get('[role="dialog"]')
    expect(panel.attributes('aria-label')).toBe('设置中心')
    expect(panel.attributes('aria-modal')).toBe('true')

    // 浮层下面是空内容区，关闭就是回检索页（memory history 的来路）。
    // Esc 由 useModalLayer 挂在 document 上（模态打开时焦点可能在容器外，
    // 挂在面板上会漏掉那些情况），所以这里派发到 document。
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    // 无来路时回默认入口（Agent 对话）——与 `/` 的落点一致。
    expect(router.currentRoute.value.path).toBe('/agent')
    wrapper.unmount()
  })

  it('桌面端浮层点击外部半透明遮罩关回上一页', async () => {
    const { wrapper, router } = await mountAt('/settings/account')

    const overlay = wrapper.get('.settings-overlay')
    await overlay.trigger('click')
    await flushPromises()
    // 无来路时回默认入口（Agent 对话）——与 `/` 的落点一致。
    expect(router.currentRoute.value.path).toBe('/agent')
    wrapper.unmount()
  })

  it('窄屏回退整页形态：没有 dialog 语义，Esc 不再关闭', async () => {
    // jsdom 的 window 与 vitest 全局不是同一对象，stubGlobal 够不到 innerWidth。
    Object.defineProperty(window, 'innerWidth', { value: 500, configurable: true })
    try {
      const { wrapper, router } = await mountAt('/settings/account')

      // 面板自身不带 dialog 语义（AppShell 抽屉在这个宽度下反而有，不能全局 find）。
      expect(wrapper.get('.settings-panel').attributes('role')).toBeUndefined()
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/settings/account')
      wrapper.unmount()
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true })
    }
  })
})
