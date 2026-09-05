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
}))

vi.mock('@/features/auth/auth-session', () => ({
  authSession: { user: session.user, status: { value: 'authenticated' } },
}))

import SettingsPage from './SettingsPage.vue'
import { usePreferences } from '@/features/settings'
import { DEFAULT_PREFERENCES } from '@/features/settings'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div />' } },
      { path: '/settings/:section?', name: 'settings', component: SettingsPage },
      { path: '/admin/users', name: 'user-admin', component: { template: '<div />' } },
      { path: '/admin/scheduled-jobs', name: 'scheduled-jobs', component: { template: '<div />' } },
    ],
  })
}

async function mountAt(path: string) {
  const router = makeRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = mount(SettingsPage, {
    attachTo: document.body,
    global: { plugins: [router] },
  })
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
    Element.prototype.scrollIntoView = vi.fn()
    window.scrollTo = vi.fn()
  })

  afterEach(() => {
    document.body.replaceChildren()
  })

  it('默认落在账号分区：登录信息与改密表单都在', async () => {
    const { wrapper } = await mountAt('/settings')

    expect(wrapper.find('#account-heading').exists()).toBe(true)
    expect(wrapper.text()).toContain('admin@example.com')
    expect(wrapper.text()).toContain('修改密码')
    wrapper.unmount()
  })

  it('顶栏有带标签的返回入口，不让人把退出键当返回用', async () => {
    const { wrapper } = await mountAt('/settings/account')

    const back = wrapper.get('.account-control .base-button')
    expect(back.text()).toContain('返回工作台')
    expect(back.attributes('href')).toBe('/')
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

  it('超管在导航里能看到后台管理直达链接', async () => {
    const { wrapper } = await mountAt('/settings')

    const labels = wrapper.findAll('.section-link').map((link) => link.text())
    expect(labels.join()).toContain('账号管理')
    expect(labels.join()).toContain('定时任务')
    wrapper.unmount()
  })
})
