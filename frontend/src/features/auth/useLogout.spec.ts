import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const session = vi.hoisted(() => ({ logout: vi.fn() }))
const cache = vi.hoisted(() => ({ clear: vi.fn() }))

vi.mock('./auth-session', () => ({ authSession: { logout: session.logout } }))
vi.mock('@/app/query-client', () => ({ queryClient: { clear: cache.clear } }))

import { useLogout, type UseLogoutOptions, type UseLogoutResult } from './useLogout'

function testRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
    ],
  })
}

/* useLogout 需要 useRouter，只能在组件里调。挂一个空组件把返回值取出来。 */
async function setup(options: UseLogoutOptions = {}) {
  const router = testRouter()
  await router.push('/')
  await router.isReady()

  let api!: UseLogoutResult
  const host = defineComponent({
    setup() {
      api = useLogout(options)
      return () => null
    },
  })
  const wrapper = mount(host, { global: { plugins: [router] } })
  return { api, router, wrapper }
}

beforeEach(() => {
  session.logout.mockReset().mockResolvedValue(undefined)
  cache.clear.mockReset()
})

describe('useLogout', () => {
  it('退登成功后清缓存并 replace 到登录页', async () => {
    const { api, router, wrapper } = await setup()

    await api.logout()

    expect(session.logout).toHaveBeenCalledOnce()
    expect(cache.clear).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.name).toBe('login')
    expect(api.loggingOut.value).toBe(false)
    expect(api.logoutError.value).toBe(false)
    wrapper.unmount()
  })

  it('用 replace 而不是 push，后退回不到已登出的页面', async () => {
    const { api, router, wrapper } = await setup()
    const before = router.currentRoute.value.fullPath

    await api.logout()
    router.back()
    await flushPromises()

    // push 的话这里会退回 /。
    expect(before).toBe('/')
    expect(router.currentRoute.value.fullPath).toBe('/login')
    wrapper.unmount()
  })

  it('退登失败时置错、留在原页，缓存不清', async () => {
    session.logout.mockRejectedValue(new Error('boom'))
    const { api, router, wrapper } = await setup()

    await api.logout()

    expect(api.logoutError.value).toBe(true)
    expect(api.loggingOut.value).toBe(false)
    expect(cache.clear).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('search')
    wrapper.unmount()
  })

  it('连点两次只发一次退登请求', async () => {
    let release!: () => void
    session.logout.mockImplementation(() => new Promise<void>((resolve) => (release = resolve)))
    const { api, wrapper } = await setup()

    const first = api.logout()
    expect(api.loggingOut.value).toBe(true)
    await api.logout()

    expect(session.logout).toHaveBeenCalledOnce()
    release()
    await first
    wrapper.unmount()
  })

  it('第二次退登会清掉上一次的错误', async () => {
    session.logout.mockRejectedValueOnce(new Error('boom'))
    const { api, wrapper } = await setup()

    await api.logout()
    expect(api.logoutError.value).toBe(true)

    await api.logout()
    expect(api.logoutError.value).toBe(false)
    wrapper.unmount()
  })

  /* 两个钩子的位置是这个 composable 唯一的设计取舍，也是最容易在后续改动里被
     合成一个的地方，所以逐条钉住顺序。 */
  it('beforeLogout 在退登请求之前跑', async () => {
    const order: string[] = []
    session.logout.mockImplementation(() => {
      order.push('logout')
      return Promise.resolve()
    })
    const { api, wrapper } = await setup({ beforeLogout: () => order.push('before') })

    await api.logout()

    // 反过来的话，在途的流会在退出后继续读一条已经没有权限的连接。
    expect(order).toEqual(['before', 'logout'])
    wrapper.unmount()
  })

  it('afterLogout 在退登成功后跑', async () => {
    const order: string[] = []
    session.logout.mockImplementation(() => {
      order.push('logout')
      return Promise.resolve()
    })
    const { api, wrapper } = await setup({ afterLogout: () => order.push('after') })

    await api.logout()

    expect(order).toEqual(['logout', 'after'])
    wrapper.unmount()
  })

  it('退登失败时不跑 afterLogout', async () => {
    session.logout.mockRejectedValue(new Error('boom'))
    const afterLogout = vi.fn()
    const { api, wrapper } = await setup({ afterLogout })

    await api.logout()

    /* 会话还在，用户可能就地重试，这时不该把他刚输的密码抹掉。
       这一条是把 afterLogout 从 finally 里挪出来的理由。 */
    expect(afterLogout).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('beforeLogout 在失败路径上也已经跑过', async () => {
    session.logout.mockRejectedValue(new Error('boom'))
    const beforeLogout = vi.fn()
    const { api, wrapper } = await setup({ beforeLogout })

    await api.logout()

    // 掐流是不可逆的，退登失败也不会把流接回来——这是已知代价，不是漏洞。
    expect(beforeLogout).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
})
