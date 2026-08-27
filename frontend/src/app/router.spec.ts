import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  status: { value: 'anonymous' },
  user: { value: null as { is_superuser: boolean } | null },
  initialize: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../features/auth/auth-session', () => ({
  authSession: auth,
}))

async function freshRouter() {
  vi.resetModules()
  return (await import('./router')).default
}

describe('application router authentication guard', () => {
  beforeEach(() => {
    auth.status.value = 'anonymous'
    auth.user.value = null
    auth.initialize.mockClear()
    vi.stubGlobal('scrollTo', vi.fn())
    window.history.replaceState({}, '', '/')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('redirects anonymous visitors to login and preserves the intended route', async () => {
    const router = await freshRouter()

    await router.push('/')
    await router.isReady()

    expect(auth.initialize).toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/')
  })

  it('keeps authenticated users out of the login page', async () => {
    auth.status.value = 'authenticated'
    const router = await freshRouter()

    await router.push('/login')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('search')
  })

  it('allows only superusers to enter account management', async () => {
    auth.status.value = 'authenticated'
    auth.user.value = { is_superuser: false }
    const regularRouter = await freshRouter()

    await regularRouter.push('/admin/users')
    await regularRouter.isReady()
    expect(regularRouter.currentRoute.value.name).toBe('search')

    auth.user.value = { is_superuser: true }
    const superuserRouter = await freshRouter()
    await superuserRouter.push('/admin/users')
    await superuserRouter.isReady()
    expect(superuserRouter.currentRoute.value.name).toBe('user-admin')
  })
})
