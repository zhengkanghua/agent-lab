import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'

const auth = vi.hoisted(() => ({
  status: { value: 'anonymous' },
  user: { value: null as { is_superuser: boolean } | null },
  initialize: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../features/auth/auth-session', () => ({
  authSession: auth,
}))

// 本文件验证路由与权限，不挂载页面。页面交互由 pages/*.spec.ts 保护；
// 用占位视图避免每次 resetModules 都重新装配检索、Markdown 和后台组件。
const view = vi.hoisted(() => ({ default: { render: () => null } }))
vi.mock('../pages/LoginPage.vue', () => view)
vi.mock('../pages/SearchPage.vue', () => view)
vi.mock('../pages/AgentChatPage.vue', () => view)
vi.mock('../pages/SettingsPage.vue', () => view)
vi.mock('../pages/AdminPage.vue', () => view)

const routers: Router[] = []

async function freshRouter() {
  vi.resetModules()
  const router = (await import('./router')).default
  routers.push(router)
  return router
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
    for (const router of routers.splice(0)) router.options.history.destroy()
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

  it('普通账号也能进 Agent 工作台，未登录仍然进不去', async () => {
    // 权限已对所有登录账号放开（ADR 0030），所以这里验的是「登录即可用」而不是「只有超管」。
    // 逐条验两个角色，因为一个「谁都放行」的实现也能通过普通账号那一半。
    for (const isSuperuser of [false, true]) {
      auth.status.value = 'authenticated'
      auth.user.value = { is_superuser: isSuperuser }
      const router = await freshRouter()

      await router.push('/agent')
      await router.isReady()

      expect(router.currentRoute.value.name).toBe('agent-chat')
    }

    // 没登录仍然进不去——放开角色不等于放开认证。
    auth.status.value = 'anonymous'
    auth.user.value = null
    const anonymousRouter = await freshRouter()
    await anonymousRouter.push('/agent')
    await anonymousRouter.isReady()
    expect(anonymousRouter.currentRoute.value.name).toBe('login')
  })

  it('会话深链带上 threadId 参数，同样只要求登录', async () => {
    const threadId = '30000000-0000-4000-8000-000000000001'

    // 普通账号带上 threadId 也应当直达那个会话：链接可分享、可收藏是它的设计前提，
    // 而现在能打开它的不再只有超管。
    auth.status.value = 'authenticated'
    auth.user.value = { is_superuser: false }
    const router = await freshRouter()
    await router.push(`/agent/${threadId}`)
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('agent-thread')
    expect(router.currentRoute.value.params.threadId).toBe(threadId)
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

  it('设置中心的三个分区都对普通账号开放', async () => {
    // 这里原先只覆盖了 /settings/agent 的守卫，而且那条守卫随权限放开被删掉了。
    // 改成对三个分区逐个验：新增分区时漏配权限会在这里暴露，而不是等用户点进去才发现。
    auth.status.value = 'authenticated'
    auth.user.value = { is_superuser: false }

    for (const section of ['account', 'search', 'agent']) {
      const router = await freshRouter()
      await router.push(`/settings/${section}`)
      await router.isReady()

      expect(router.currentRoute.value.name).toBe('settings')
      expect(router.currentRoute.value.params.section).toBe(section)
    }
  })

  it.each(['users', 'scheduled-jobs', 'documents'])(
    '后台 %s 分区只对超级用户开放',
    async (section) => {
      auth.status.value = 'authenticated'
      auth.user.value = { is_superuser: false }
      const regularRouter = await freshRouter()

      await regularRouter.push('/admin/' + section)
      await regularRouter.isReady()
      expect(regularRouter.currentRoute.value.name).toBe('search')

      auth.user.value = { is_superuser: true }
      const superuserRouter = await freshRouter()
      await superuserRouter.push('/admin/' + section)
      await superuserRouter.isReady()
      expect(superuserRouter.currentRoute.value.name).toBe('admin')
      expect(superuserRouter.currentRoute.value.params.section).toBe(section)
    },
  )
})
