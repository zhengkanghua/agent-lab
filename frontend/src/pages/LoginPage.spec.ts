import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  initialize: vi.fn(),
  /* 会话「用着用着失效」的标记，由全局 401 处理器置位。
     用例都在挂载之前改它，所以这里用一个普通对象就够，不需要 ref 的响应性。 */
  expiredMidSession: { value: false },
}))

vi.mock('../features/auth/auth-session', () => ({
  authSession: {
    status: ref('anonymous'),
    user: ref(null),
    error: ref(null),
    expiredMidSession: mocks.expiredMidSession,
    login: mocks.login,
    initialize: mocks.initialize,
  },
}))

import LoginPage from './LoginPage.vue'

function testRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: LoginPage },
      { path: '/', name: 'search', component: { template: '<div>search</div>' } },
      { path: '/saved', name: 'saved', component: { template: '<div>saved</div>' } },
    ],
  })
}

describe('LoginPage', () => {
  afterEach(() => {
    mocks.login.mockReset()
    mocks.initialize.mockReset()
    mocks.expiredMidSession.value = false
    document.body.replaceChildren()
  })

  /* 被 401 弹回来时，用户在整个流程里唯一的变化就是「页面突然跳到了登录页」。
     没有这一句，他只会以为自己点错了什么，或者以为数据丢了。 */
  it('会话用着用着失效时，说明被弹回来的原因', async () => {
    mocks.expiredMidSession.value = true
    const router = testRouter()
    await router.push('/login?redirect=/admin/users')
    await router.isReady()
    const wrapper = mount(LoginPage, { global: { plugins: [router] } })

    expect(wrapper.text()).toContain('登录已过期')
    expect(wrapper.text()).toContain('重新登录')
    wrapper.unmount()
  })

  /* 陌生访客从没登录过，不该被通知「你的登录过期了」——那是在描述一个不存在的会话。 */
  it('普通进入登录页时不提过期', async () => {
    const router = testRouter()
    await router.push('/login')
    await router.isReady()
    const wrapper = mount(LoginPage, { global: { plugins: [router] } })

    expect(wrapper.text()).not.toContain('登录已过期')
    wrapper.unmount()
  })

  it('logs in and returns to a safe in-app redirect', async () => {
    mocks.login.mockResolvedValue(undefined)
    const router = testRouter()
    await router.push('/login?redirect=/saved')
    await router.isReady()
    const wrapper = mount(LoginPage, {
      attachTo: document.body,
      global: { plugins: [router] },
    })

    await wrapper.get('input[name="username"]').setValue('reader@example.com')
    await wrapper.get('input[name="password"]').setValue('private-password')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mocks.login).toHaveBeenCalledWith('reader@example.com', 'private-password')
    expect(router.currentRoute.value.fullPath).toBe('/saved')
    wrapper.unmount()
  })

  it('shows a stable message for invalid credentials without echoing the password', async () => {
    mocks.login.mockRejectedValue(
      new ApiError({ message: 'LOGIN_BAD_CREDENTIALS', status: 400, code: 'unknown_error' }),
    )
    const router = testRouter()
    await router.push('/login')
    await router.isReady()
    const wrapper = mount(LoginPage, { global: { plugins: [router] } })

    await wrapper.get('input[name="username"]').setValue('reader@example.com')
    await wrapper.get('input[name="password"]').setValue('do-not-echo-this')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toBe('账号或密码不正确，请重新输入。')
    expect(wrapper.text()).not.toContain('do-not-echo-this')
    wrapper.unmount()
  })
})
