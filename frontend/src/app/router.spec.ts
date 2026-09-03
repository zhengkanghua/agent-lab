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

  it('allows only superusers to enter the agent workspace', async () => {
    auth.status.value = 'authenticated'
    auth.user.value = { is_superuser: false }
    const regularRouter = await freshRouter()

    await regularRouter.push('/agent')
    await regularRouter.isReady()
    // 后端 /agent/* 挂的是 current_superuser，前端不挡住的话用户只会撞上 403。
    expect(regularRouter.currentRoute.value.name).toBe('search')

    auth.user.value = { is_superuser: true }
    const superuserRouter = await freshRouter()
    await superuserRouter.push('/agent')
    await superuserRouter.isReady()
    expect(superuserRouter.currentRoute.value.name).toBe('agent-chat')
  })

  it('会话深链带上 threadId 参数，并同样只对超级用户开放', async () => {
    const threadId = '30000000-0000-4000-8000-000000000001'

    auth.status.value = 'authenticated'
    auth.user.value = { is_superuser: true }
    const superuserRouter = await freshRouter()
    await superuserRouter.push(`/agent/${threadId}`)
    await superuserRouter.isReady()

    expect(superuserRouter.currentRoute.value.name).toBe('agent-thread')
    expect(superuserRouter.currentRoute.value.params.threadId).toBe(threadId)

    // 少了这一半，一条分享出去的会话链接会绕过超级用户检查——而后端会用 403 拒掉它，
    // 用户看到的是一个报错的空页面。
    auth.user.value = { is_superuser: false }
    const regularRouter = await freshRouter()
    await regularRouter.push(`/agent/${threadId}`)
    await regularRouter.isReady()
    expect(regularRouter.currentRoute.value.name).toBe('search')
  })

  it('未登录时访问会话深链会带着完整地址跳登录页', async () => {
    // redirect 里必须带 threadId，否则登录后回到的是 /agent，用户点开的那个会话丢了。
    const threadId = '30000000-0000-4000-8000-000000000001'
    auth.status.value = 'anonymous'
    auth.user.value = null
    const router = await freshRouter()

    await router.push(`/agent/${threadId}`)
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe(`/agent/${threadId}`)
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

  it('allows only superusers to enter scheduled job management', async () => {
    // /admin 子路由的守卫挂在父路由上，新页面自动继承；这条测试锁住「新页面没有
    // 意外绕过父路由权限」——后端 /scheduled-jobs 同样只对超级用户开放。
    auth.status.value = 'authenticated'
    auth.user.value = { is_superuser: false }
    const regularRouter = await freshRouter()

    await regularRouter.push('/admin/scheduled-jobs')
    await regularRouter.isReady()
    expect(regularRouter.currentRoute.value.name).toBe('search')

    auth.user.value = { is_superuser: true }
    const superuserRouter = await freshRouter()
    await superuserRouter.push('/admin/scheduled-jobs')
    await superuserRouter.isReady()
    expect(superuserRouter.currentRoute.value.name).toBe('scheduled-jobs')
  })
})
