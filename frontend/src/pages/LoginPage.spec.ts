import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  initialize: vi.fn(),
}))

vi.mock('../features/auth/auth-session', () => ({
  authSession: {
    status: ref('anonymous'),
    user: ref(null),
    error: ref(null),
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
    document.body.innerHTML = ''
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
