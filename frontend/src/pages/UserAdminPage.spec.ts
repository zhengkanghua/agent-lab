import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  resetUserPassword: vi.fn(),
  revokeUserSessions: vi.fn(),
}))

vi.mock('../api/user-admin', () => api)

const session = vi.hoisted(() => ({
  initialize: vi.fn(),
  logout: vi.fn(),
}))

vi.mock('../features/auth/auth-session', () => ({
  authSession: {
    status: ref('authenticated'),
    user: ref({
      id: '10000000-0000-4000-8000-000000000001',
      email: 'admin@example.com',
      is_active: true,
      is_superuser: true,
      is_verified: true,
      is_environment_admin: true,
    }),
    initialize: session.initialize,
    logout: session.logout,
  },
}))

import UserAdminPage from './UserAdminPage.vue'

const environmentAdmin = {
  id: '10000000-0000-4000-8000-000000000001',
  email: 'admin@example.com',
  is_active: true,
  is_superuser: true,
  is_verified: true,
  is_environment_admin: true,
  created_at: '2026-08-17T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
}

const regularUser = {
  id: '20000000-0000-4000-8000-000000000001',
  email: 'reader@example.com',
  is_active: true,
  is_superuser: false,
  is_verified: true,
  is_environment_admin: false,
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
}

function testRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div>search</div>' } },
      { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
      { path: '/admin/users', name: 'user-admin', component: UserAdminPage },
      { path: '/account', name: 'account', component: { template: '<div>account</div>' } },
    ],
  })
}

import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'

async function mountPage() {
  const router = testRouter()
  await router.push('/admin/users')
  await router.isReady()
  
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

  const wrapper = mount(UserAdminPage, {
    attachTo: document.body,
    global: { plugins: [router, [VueQueryPlugin, { queryClient }]] },
  })
  await flushPromises()
  return wrapper
}

describe('UserAdminPage', () => {
  beforeEach(() => {
    api.listUsers.mockResolvedValue([environmentAdmin, regularUser])
    api.createUser.mockReset()
    api.updateUser.mockReset()
    api.resetUserPassword.mockReset()
    api.revokeUserSessions.mockReset()
    session.initialize.mockReset()
    session.logout.mockReset()
  })

  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
  })

  it('renders the deployment-managed administrator with mutation controls disabled', async () => {
    const wrapper = await mountPage()

    expect(wrapper.text()).toContain('admin@example.com')
    expect(wrapper.text()).toContain('由部署 Secret 托管')
    expect(
      wrapper.get(`[data-testid="active-${environmentAdmin.id}"]`).attributes(),
    ).toHaveProperty('disabled')
    expect(
      wrapper.get(`[data-testid="superuser-${environmentAdmin.id}"]`).attributes(),
    ).toHaveProperty('disabled')
    expect(wrapper.get(`[data-testid="reset-${environmentAdmin.id}"]`).attributes()).toHaveProperty(
      'disabled',
    )
    expect(
      wrapper.get(`[data-testid="sessions-${environmentAdmin.id}"]`).attributes(),
    ).not.toHaveProperty('disabled')
    wrapper.unmount()
  })

  it('validates creation locally and clears the password after a successful request', async () => {
    const wrapper = await mountPage()
    const createButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('创建账号'))
    await createButton?.trigger('click')

    await wrapper.get('input[name="new-email"]').setValue('new@example.com')
    await wrapper.get('input[name="new-password"]').setValue('short')
    await wrapper.get('.create-form').trigger('submit')
    expect(api.createUser).not.toHaveBeenCalled()
    expect(wrapper.get('.create-editor [role="alert"]').text()).toContain('12 到 128')

    const privatePassword = 'private-new-password'
    const created = {
      ...regularUser,
      id: '30000000-0000-4000-8000-000000000001',
      email: 'new@example.com',
    }
    api.createUser.mockResolvedValue(created)
    await wrapper.get('input[name="new-password"]').setValue(privatePassword)
    await wrapper.get('.create-form').trigger('submit')
    await flushPromises()

    expect(api.createUser).toHaveBeenCalledWith({
      email: 'new@example.com',
      password: privatePassword,
      isSuperuser: false,
    })
    expect(wrapper.find('input[name="new-password"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(privatePassword)
    expect(wrapper.text()).toContain('已创建账号 new@example.com')
    wrapper.unmount()
  })

  it('updates status, resets a password, and revokes sessions without rendering secrets', async () => {
    const wrapper = await mountPage()
    api.updateUser.mockResolvedValue({ ...regularUser, is_active: false })

    await wrapper.get(`[data-testid="active-${regularUser.id}"]`).setValue(false)
    await flushPromises()
    expect(api.updateUser).toHaveBeenCalledWith({
      userId: regularUser.id,
      isActive: false,
    })

    api.resetUserPassword.mockResolvedValue(regularUser)
    await wrapper.get(`[data-testid="reset-${regularUser.id}"]`).trigger('click')
    const privatePassword = 'private-reset-password'
    await wrapper.get('input[name="reset-password"]').setValue(privatePassword)
    await wrapper.get('.reset-editor').trigger('submit')
    await flushPromises()
    expect(api.resetUserPassword).toHaveBeenCalledWith({
      userId: regularUser.id,
      password: privatePassword,
    })
    expect(wrapper.find('input[name="reset-password"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(privatePassword)

    vi.stubGlobal(
      'confirm',
      vi.fn(() => true),
    )
    api.revokeUserSessions.mockResolvedValue({ revoked_sessions: 2 })
    await wrapper.get(`[data-testid="sessions-${regularUser.id}"]`).trigger('click')
    await flushPromises()
    expect(api.revokeUserSessions).toHaveBeenCalledWith(regularUser.id)
    expect(wrapper.text()).toContain('2 个会话')
    wrapper.unmount()
  })
})
