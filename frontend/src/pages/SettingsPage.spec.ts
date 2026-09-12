import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  // 设置页会经 useDefaultAgentPrompt 拉默认提示词；检索分区不发任何请求。
  fetchAgentDefaultPrompt: vi.fn(),
}))

vi.mock('@/api/agent-chat', () => ({
  fetchAgentDefaultPrompt: api.fetchAgentDefaultPrompt,
  MAX_SYSTEM_PROMPT_CHARACTERS: 4000,
}))

const session = vi.hoisted(() => ({
  user: { value: null as { email: string; is_superuser: boolean } | null },
  logout: vi.fn(),
}))

vi.mock('@/features/auth/auth-session', () => ({
  authSession: { user: session.user, status: { value: 'authenticated' }, logout: session.logout },
}))

import SettingsPage from './SettingsPage.vue'
import { usePreferences } from '@/features/settings'
import { DEFAULT_PREFERENCES } from '@/features/settings'

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
      global: { plugins: [router] },
    },
  )
  await flushPromises()
  return { wrapper, router }
}

describe('SettingsPage', () => {
  beforeEach(() => {
    localStorage.clear()
    Object.assign(usePreferences().preferences, DEFAULT_PREFERENCES)
    api.fetchAgentDefaultPrompt.mockReset()
    api.fetchAgentDefaultPrompt.mockResolvedValue('你是新闻检索助手。')
    session.user.value = { email: 'admin@example.com', is_superuser: true }
    session.logout.mockReset()
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

  it('返回工作台不再需要页面级按钮：外壳导航始终在侧栏', async () => {
    const { wrapper } = await mountAt('/settings/account')

    expect(wrapper.find('.settings-heading a').exists()).toBe(false)
    const nav = wrapper.get('.sidebar-nav .nav-item')
    expect(nav.text()).toContain('语义检索')
    expect(nav.attributes('href')).toBe('/')
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
    wrapper.unmount()
  })

  it('Agent 偏好分区：保存把草稿提交进偏好并内联确认', async () => {
    const { wrapper } = await mountAt('/settings/agent')

    expect(wrapper.find('#agent-prefs-heading').exists()).toBe(true)
    expect(api.fetchAgentDefaultPrompt).toHaveBeenCalledOnce()

    const editor = wrapper.get('textarea')
    await editor.setValue('你是财经记者。')
    expect(wrapper.get('.status-badge').text()).toContain('使用服务端默认提示词')

    await wrapper.get('.editor-actions button[type="button"]').trigger('click')

    expect(usePreferences().preferences.agentSystemPrompt).toBe('你是财经记者。')
    expect(wrapper.get('.status-badge').text()).toContain('已启用自定义提示词')
    expect(wrapper.get('.saved-note').text()).toContain('已保存')
    wrapper.unmount()
  })

  it('普通用户看不到 Agent 偏好分区，导航里也没有它', async () => {
    session.user.value = { email: 'user@example.com', is_superuser: false }
    const { wrapper } = await mountAt('/settings/agent')

    // 页面兜底把地址拉回账号分区。
    expect(wrapper.find('#account-heading').exists()).toBe(true)
    expect(wrapper.find('#agent-prefs-heading').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Agent 偏好')
    wrapper.unmount()
  })

  it('分区切换保留提示词草稿，离开时可取消，保存后可直接离开', async () => {
    const confirm = vi.fn().mockReturnValue(false)
    vi.stubGlobal('confirm', confirm)
    const { wrapper, router } = await mountAt('/settings/agent')
    await wrapper.get('textarea').setValue('尚未保存的完整提示词')
    await router.push('/settings/search')
    await router.push('/settings/agent')
    expect(wrapper.get<HTMLTextAreaElement>('textarea').element.value).toBe('尚未保存的完整提示词')
    expect(usePreferences().preferences.agentSystemPrompt).toBe('')
    expect(confirm).not.toHaveBeenCalled()
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/settings/agent')
    expect(confirm).toHaveBeenCalledOnce()
    await wrapper.get('.editor-actions button').trigger('click')
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/')
    expect(confirm).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('有提示词草稿时刷新页面触发离开提醒', async () => {
    const { wrapper } = await mountAt('/settings/agent')
    await wrapper.get('textarea').setValue('需要保留的草稿')
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    wrapper.unmount()
  })

  it('退出前确认未保存草稿，取消不发请求，退出失败仍保留草稿', async () => {
    const confirm = vi.fn().mockReturnValue(false)
    vi.stubGlobal('confirm', confirm)
    const { wrapper } = await mountAt('/settings/agent')
    await wrapper.get('textarea').setValue('退出前尚未保存的草稿')
    const logout = wrapper.get('button[aria-label="退出登录"]')
    await logout.trigger('click')
    expect(confirm).toHaveBeenCalledOnce()
    expect(session.logout).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    session.logout.mockRejectedValue(new Error('offline'))
    await logout.trigger('click')
    await flushPromises()
    expect(session.logout).toHaveBeenCalledOnce()
    expect(wrapper.get<HTMLTextAreaElement>('textarea').element.value).toBe('退出前尚未保存的草稿')
    expect(wrapper.text()).toContain('退出失败')
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
    await panel.trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    wrapper.unmount()
  })

  it('桌面端浮层点击外部半透明遮罩关回上一页', async () => {
    const { wrapper, router } = await mountAt('/settings/account')

    const overlay = wrapper.get('.settings-overlay')
    await overlay.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    wrapper.unmount()
  })

  it('吸附保存条提供放弃修改：草稿退回已保存值，不落盘', async () => {
    const { wrapper } = await mountAt('/settings/agent')
    await wrapper.get('textarea').setValue('改了一半的提示词')

    const discard = wrapper
      .findAll('.editor-actions button')
      .find((button) => button.text() === '放弃修改')!
    await discard.trigger('click')

    expect(wrapper.get<HTMLTextAreaElement>('textarea').element.value).toBe('')
    expect(usePreferences().preferences.agentSystemPrompt).toBe('')
    wrapper.unmount()
  })

  it('窄屏回退整页形态：没有 dialog 语义，Esc 不再关闭', async () => {
    // jsdom 的 window 与 vitest 全局不是同一对象，stubGlobal 够不到 innerWidth。
    Object.defineProperty(window, 'innerWidth', { value: 500, configurable: true })
    try {
      const { wrapper, router } = await mountAt('/settings/account')

      // 面板自身不带 dialog 语义（AppShell 抽屉在这个宽度下反而有，不能全局 find）。
      expect(wrapper.get('.settings-panel').attributes('role')).toBeUndefined()
      await wrapper.get('.settings-panel').trigger('keydown', { key: 'Escape' })
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/settings/account')
      wrapper.unmount()
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true })
    }
  })
})
