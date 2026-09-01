import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentChatEvent, StreamAgentChatOptions } from '@/api/agent-chat'

const api = vi.hoisted(() => ({
  streamAgentChat: vi.fn(),
  fetchAgentDefaultPrompt: vi.fn(),
}))

vi.mock('../api/agent-chat', () => api)

// 会话列表也要打桩：不打的话页面挂载时会真去 fetch，jsdom 里表现成一堆未处理的 rejection，
// 而且列表永远停在错误态——本文件那些与列表无关的断言会在一个「侧栏报错」的界面上跑。
const threadsApi = vi.hoisted(() => ({
  listAgentThreads: vi.fn(),
  getAgentThreadMessages: vi.fn(),
  deleteAgentThread: vi.fn(),
}))

vi.mock('../api/agent-threads', () => threadsApi)

const session = vi.hoisted(() => ({ logout: vi.fn() }))

/* 账号管理入口的可见性要按角色断言，所以 user 必须可改，不能像原来那样写死
   is_superuser: true。ref 在 mock 工厂里建（`vi.hoisted` 跑在 import 之前，那时还没有 `ref`），
   容器只持有它的引用，于是用例改的和页面读的是同一个 ref。
   每个用例前 resetAuthUser() 复位成超管，避免顺序相关的假失败。 */
const auth = vi.hoisted(() => ({ user: null as unknown as ReturnType<typeof ref> }))

const SUPERUSER = {
  id: '10000000-0000-4000-8000-000000000001',
  email: 'admin@example.com',
  is_active: true,
  is_superuser: true,
  is_verified: true,
  is_environment_admin: true,
}

vi.mock('../features/auth/auth-session', () => {
  auth.user = ref({
    id: '10000000-0000-4000-8000-000000000001',
    email: 'admin@example.com',
    is_active: true,
    is_superuser: true,
    is_verified: true,
    is_environment_admin: true,
  })
  return {
    authSession: {
      status: ref('authenticated'),
      user: auth.user,
      logout: session.logout,
    },
  }
})

function resetAuthUser(): void {
  auth.user.value = { ...SUPERUSER }
}

import AgentChatPage from './AgentChatPage.vue'

const THREAD_ID = '30000000-0000-4000-8000-000000000001'

/** 让流按脚本产出事件，并记下每次调用参数。 */
function scripted(...runs: AgentChatEvent[][]): void {
  let index = 0
  api.streamAgentChat.mockImplementation(async function* () {
    for (const event of runs[index++] ?? []) yield event
  })
}

function testRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'search', component: { template: '<div>search</div>' } },
      { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
      { path: '/agent', name: 'agent-chat', component: AgentChatPage },
      // 页面在服务端新建会话后会 replace 到这条路由。少了它，vue-router 抛「No match」，
      // 而那个异常发生在 watch 回调里，只表现成未处理的 rejection，不会让用例失败。
      { path: '/agent/:threadId', name: 'agent-thread', component: AgentChatPage },
      // 顶栏的账号管理入口指向这条。少了它 RouterLink 解析不到目标，
      // 本文件所有用例都会在挂载时炸掉，而不只是与入口相关的那两条。
      { path: '/admin/users', name: 'user-admin', component: { template: '<div>admin</div>' } },
    ],
  })
}

async function mountPage() {
  const router = testRouter()
  await router.push('/agent')
  await router.isReady()
  const wrapper = mount(AgentChatPage, {
    attachTo: document.body,
    global: { plugins: [router] },
  })
  await flushPromises()
  return { wrapper, router }
}

/* 系统提示词从输入框下的 <details> 改成了底部齿轮浮层（Q8），面板只在展开时进 DOM。
   要碰 .prompt-input / .prompt-actions 的用例先过这里。 */
async function openPromptPanel(wrapper: VueWrapper): Promise<void> {
  await wrapper.get('.prompt-trigger button').trigger('click')
}

describe('AgentChatPage', () => {
  beforeEach(() => {
    api.streamAgentChat.mockReset()
    api.fetchAgentDefaultPrompt.mockReset()
    api.fetchAgentDefaultPrompt.mockResolvedValue('你是新闻检索助手。')
    session.logout.mockReset()
    session.logout.mockResolvedValue(undefined)
    resetAuthUser()
    threadsApi.listAgentThreads.mockReset()
    threadsApi.listAgentThreads.mockResolvedValue({ items: [], total: 0 })
    threadsApi.getAgentThreadMessages.mockReset()
    threadsApi.deleteAgentThread.mockReset()
    scripted([{ event: 'done', thread_id: THREAD_ID }])
    // jsdom 没有实现 scrollIntoView。
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
  })

  /* 断言 aria-label 而不是图标组件：入口对用户和读屏的可见性由它决定，
     换图标不该让这两条失败。 */
  it('超管在本页顶栏能直接进账号管理，不必先退回检索页', async () => {
    const { wrapper } = await mountPage()

    const link = wrapper
      .findAll('.topbar-nav-link')
      .find((item) => item.attributes('aria-label') === '账号管理')

    expect(link?.attributes('href')).toBe('/admin/users')
    wrapper.unmount()
  })

  it('非超管在本页顶栏看不到账号管理入口', async () => {
    auth.user.value = { ...SUPERUSER, is_superuser: false, is_environment_admin: false }
    const { wrapper } = await mountPage()

    const labels = wrapper.findAll('.topbar-nav-link').map((item) => item.attributes('aria-label'))

    expect(labels).not.toContain('账号管理')
    expect(labels).toContain('语义检索')
    wrapper.unmount()
  })

  it('进入页面时取默认提示词，让「填入默认提示词」可用', async () => {
    const { wrapper } = await mountPage()
    await openPromptPanel(wrapper)

    expect(api.fetchAgentDefaultPrompt).toHaveBeenCalledOnce()
    expect(wrapper.get('.prompt-actions button').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('默认提示词取不到时仍能进页面提问', async () => {
    api.fetchAgentDefaultPrompt.mockRejectedValue(new Error('boom'))
    const { wrapper } = await mountPage()
    await openPromptPanel(wrapper)

    // 不传 system_prompt 时后端用同一份默认值，所以这次失败不该阻断对话。
    expect(wrapper.get('.prompt-actions button').attributes('disabled')).toBeDefined()

    await wrapper.get('.message-input').setValue('央行利率')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    expect(api.streamAgentChat).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('提交表单把回答渲染到对话区', async () => {
    scripted([
      { event: 'token', text: '央行' },
      { event: 'token', text: '维持利率不变。' },
      { event: 'done', thread_id: THREAD_ID },
    ])
    const { wrapper } = await mountPage()

    await wrapper.get('.message-input').setValue('央行利率')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('.question-text').text()).toBe('央行利率')
    // 答案正文改由 MarkdownAnswer 渲染，容器类名跟着换成 .answer-body。
    expect(wrapper.get('.answer-body').text()).toBe('央行维持利率不变。')
    expect(wrapper.find('.empty-state').exists()).toBe(false)
    wrapper.unmount()
  })

  it('点空态示例问题直接发出去', async () => {
    const { wrapper } = await mountPage()

    await wrapper.get('.example-button').trigger('click')
    await flushPromises()

    expect(api.streamAgentChat.mock.calls[0]?.[0]).toMatchObject({
      message: '最近有哪些关于利率的报道？',
    })
    wrapper.unmount()
  })

  it('工具调用轨迹显示在回答上方', async () => {
    scripted([
      {
        event: 'tool_call',
        tool_call_id: 'call-1',
        tool: 'search_news',
        arguments: { query: '利率' },
      },
      {
        event: 'tool_result',
        tool_call_id: 'call-1',
        tool: 'search_news',
        content: '找到 2 篇。',
        failed: false,
      },
      { event: 'token', text: '维持不变。' },
      { event: 'done', thread_id: THREAD_ID },
    ])
    const { wrapper } = await mountPage()

    await wrapper.get('.message-input').setValue('利率')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    /* 轨迹现在整块折叠（Q10），落定后是收起状态。收起的 <details> 里子节点仍在 DOM 中，
       所以下面这两条读得到内容，读到的是「渲染对了」而不是「展开着」。 */
    expect(wrapper.get('.trace-list').text()).toContain('检索新闻')
    expect(wrapper.get('.trace-arguments').text()).toBe('query=利率')
    wrapper.unmount()
  })

  it('第二轮带上第一轮拿到的会话 id', async () => {
    scripted([{ event: 'done', thread_id: THREAD_ID }], [{ event: 'done', thread_id: THREAD_ID }])
    const { wrapper } = await mountPage()

    await wrapper.get('.message-input').setValue('第一问')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    await wrapper.get('.message-input').setValue('第二问')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    expect(api.streamAgentChat.mock.calls[0]?.[0]).toMatchObject({ threadId: null })
    expect(api.streamAgentChat.mock.calls[1]?.[0]).toMatchObject({ threadId: THREAD_ID })
    wrapper.unmount()
  })

  it('新会话清空历史，下一轮不再带旧会话 id', async () => {
    const { wrapper } = await mountPage()

    await wrapper.get('.message-input').setValue('第一问')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    await wrapper.get('.secondary-button').trigger('click')
    await flushPromises()

    expect(wrapper.find('.turn').exists()).toBe(false)
    expect(wrapper.find('.empty-state').exists()).toBe(true)

    await wrapper.get('.message-input').setValue('重新开始')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    expect(api.streamAgentChat.mock.calls[1]?.[0]).toMatchObject({ threadId: null })
    wrapper.unmount()
  })

  it('错误事件给出重发按钮，点了再问一次', async () => {
    scripted(
      [
        {
          event: 'error',
          thread_id: THREAD_ID,
          code: 'llm_timeout',
          detail: '超时。',
          retryable: true,
        },
      ],
      [
        { event: 'token', text: '这次成了' },
        { event: 'done', thread_id: THREAD_ID },
      ],
    )
    const { wrapper } = await mountPage()

    await wrapper.get('.message-input').setValue('会超时的问题')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    expect(wrapper.get('.turn-error').text()).toContain('模型响应超时')

    await wrapper.get('.retry-button').trigger('click')
    await flushPromises()

    expect(api.streamAgentChat.mock.calls[1]?.[0]).toMatchObject({ message: '会超时的问题' })
    expect(wrapper.findAll('.turn')).toHaveLength(2)
    wrapper.unmount()
  })

  it('首轮失败也把会话并进侧栏，不用等刷新页面', async () => {
    /*
     * 归属行在流开始之前就写好了，所以失败的这一轮在服务端已经是一个会话。侧栏是在挂载时
     * 取的列表（那时还是空的），如果前端不认 error 里的 thread_id，用户就要刷新页面才能
     * 看见自己刚发出的这一段——而上游限流是最常撞见的失败，不是边角情况。
     */
    scripted([
      {
        event: 'error',
        thread_id: THREAD_ID,
        code: 'llm_rate_limited',
        detail: '上游限流。',
        retryable: true,
      },
    ])
    const { wrapper } = await mountPage()

    expect(wrapper.get('.thread-rail').text()).toContain('还没有会话')

    await wrapper.get('.message-input').setValue('会被限流的问题')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    const rail = wrapper.get('.thread-rail')
    expect(rail.text()).not.toContain('还没有会话')
    expect(rail.text()).toContain('会被限流的问题')
    wrapper.unmount()
  })

  it('自定义系统提示词随下一轮发出', async () => {
    const { wrapper } = await mountPage()
    await openPromptPanel(wrapper)

    await wrapper.get('.prompt-input').setValue('你是财经记者。')
    await wrapper.get('.message-input').setValue('问题')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    expect(api.streamAgentChat.mock.calls[0]?.[0]).toMatchObject({
      systemPrompt: '你是财经记者。',
    })
    wrapper.unmount()
  })

  it('退出登录先掐掉在途的流，再回登录页', async () => {
    let release: (() => void) | undefined
    let aborted = false
    api.streamAgentChat.mockImplementation(async function* (options: StreamAgentChatOptions) {
      options.signal?.addEventListener('abort', () => (aborted = true))
      yield { event: 'token', text: '在写' } as AgentChatEvent
      await new Promise<void>((resolve) => (release = resolve))
    })
    const { wrapper, router } = await mountPage()

    await wrapper.get('.message-input').setValue('问题')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    await wrapper.get('button[aria-label="退出登录"]').trigger('click')
    await flushPromises()

    // 留着在途的流会在退出后继续读一条已经没有权限的连接。
    expect(aborted).toBe(true)
    expect(session.logout).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.name).toBe('login')

    release?.()
    wrapper.unmount()
  })

  it('退出失败时留在本页并提示', async () => {
    session.logout.mockRejectedValue(new Error('boom'))
    const { wrapper, router } = await mountPage()

    await wrapper.get('button[aria-label="退出登录"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.logout-error').text()).toBe('退出失败')
    expect(router.currentRoute.value.name).toBe('agent-chat')
    wrapper.unmount()
  })

  it('顶栏文案说明本页会生成答案且只读', async () => {
    const { wrapper } = await mountPage()

    expect(wrapper.get('.mode-note').text()).toContain('模型生成答案')
    expect(wrapper.get('.mode-note').text()).toContain('只读检索')
    wrapper.unmount()
  })

  it('输入区下方常驻「可能有误」与「只读」的细则，且每一轮都在', async () => {
    /* 这两句原来一句挂在空态、一句挂在页脚。空态那句在第一轮答案出现后就消失，
       而「回答可能有误」对每一轮都成立；页脚在这一页已经撤掉（底部固定输入区之下
       再放页脚，用户要多滚一屏才看得到）。所以合并到输入区下面的细则行，
       并且这条用例特意在提问之后断言它还在。 */
    const { wrapper } = await mountPage()
    const note = () => wrapper.get('.dock-note').text()

    expect(note()).toContain('可能有误')
    expect(note()).toContain('只读数据')
    expect(wrapper.find('.site-footer').exists()).toBe(false)

    await wrapper.get('.message-input').setValue('央行利率')
    await wrapper.get('.agent-form').trigger('submit')
    await flushPromises()

    expect(note()).toContain('可能有误')
    wrapper.unmount()
  })

  describe('会话记录', () => {
    const REPLAY = {
      thread_id: THREAD_ID,
      turns: [{ question: '之前问过的', answer: '之前答过的' }],
      summarized: false,
      summary: null,
    }

    /** 直接从一条会话深链进入，模拟刷新或点开分享链接。 */
    async function mountThreadPage(threadId = THREAD_ID) {
      const router = testRouter()
      await router.push(`/agent/${threadId}`)
      await router.isReady()
      const wrapper = mount(AgentChatPage, {
        attachTo: document.body,
        global: { plugins: [router] },
      })
      await flushPromises()
      return { wrapper, router }
    }

    it('挂载时就读会话列表，侧栏立刻有内容', async () => {
      threadsApi.listAgentThreads.mockResolvedValue({
        items: [
          {
            thread_id: THREAD_ID,
            title: '央行降息了吗',
            created_at: '2026-08-18T00:00:00Z',
            last_active_at: '2026-08-18T00:00:00Z',
          },
        ],
        total: 1,
      })
      const { wrapper } = await mountPage()

      expect(threadsApi.listAgentThreads).toHaveBeenCalledOnce()
      expect(wrapper.get('.thread-item .title').text()).toBe('央行降息了吗')
      wrapper.unmount()
    })

    it('直接访问 /agent/:id 就载入那个会话的历史', async () => {
      // 刷新页面后还在同一个会话里，是这条路由存在的全部理由。
      threadsApi.getAgentThreadMessages.mockResolvedValue(REPLAY)
      const { wrapper } = await mountThreadPage()

      expect(threadsApi.getAgentThreadMessages).toHaveBeenCalledWith(THREAD_ID, expect.anything())
      expect(wrapper.text()).toContain('之前问过的')
      expect(wrapper.text()).toContain('之前答过的')
      wrapper.unmount()
    })

    it('点侧栏里的会话会改地址，并载入它的历史', async () => {
      threadsApi.listAgentThreads.mockResolvedValue({
        items: [
          {
            thread_id: THREAD_ID,
            title: '央行降息了吗',
            created_at: '2026-08-18T00:00:00Z',
            last_active_at: '2026-08-18T00:00:00Z',
          },
        ],
        total: 1,
      })
      threadsApi.getAgentThreadMessages.mockResolvedValue(REPLAY)
      const { wrapper, router } = await mountPage()

      await wrapper.get('.thread-item .open-button').trigger('click')
      await flushPromises()

      expect(router.currentRoute.value.name).toBe('agent-thread')
      expect(router.currentRoute.value.params.threadId).toBe(THREAD_ID)
      expect(wrapper.text()).toContain('之前问过的')
      wrapper.unmount()
    })

    it('新建会话后地址补上 id，用的是 replace 不是 push', async () => {
      // push 会让后退键先回到 /agent（同一段对话、地址上没有 id），要点两次才真正离开。
      // 断言直接看调用了哪个方法：createMemoryHistory 不碰 window.history，
      // 所以数 window.history.length 是个永远成立的空断言。
      const router = testRouter()
      await router.push('/agent')
      await router.isReady()
      const replace = vi.spyOn(router, 'replace')
      const push = vi.spyOn(router, 'push')
      const wrapper = mount(AgentChatPage, {
        attachTo: document.body,
        global: { plugins: [router] },
      })
      await flushPromises()

      await wrapper.get('.message-input').setValue('央行利率')
      await wrapper.get('.agent-form').trigger('submit')
      await flushPromises()

      expect(router.currentRoute.value.params.threadId).toBe(THREAD_ID)
      expect(replace).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'agent-thread', params: { threadId: THREAD_ID } }),
      )
      expect(push).not.toHaveBeenCalled()
      wrapper.unmount()
    })

    it('新建会话后不会再去回放它一遍', async () => {
      // 补地址那次 replace 会触发监听路由的 watch。不守卫的话它立刻回放这个刚建出来的会话，
      // 把刚流式生成的那一轮覆盖成从服务端读回来的版本。
      const { wrapper } = await mountPage()

      await wrapper.get('.message-input').setValue('央行利率')
      await wrapper.get('.agent-form').trigger('submit')
      await flushPromises()

      expect(threadsApi.getAgentThreadMessages).not.toHaveBeenCalled()
      wrapper.unmount()
    })

    it('会话打不开时说明情况，且不把地址悄悄改掉', async () => {
      const { ApiError } = await import('@/api/client')
      threadsApi.getAgentThreadMessages.mockRejectedValue(
        new ApiError({ message: '没有', code: 'agent_thread_not_found', status: 404 }),
      )
      const { wrapper, router } = await mountThreadPage()

      expect(wrapper.get('.thread-error').text()).toContain('会话不存在或已被删除')
      // 留在原地址：跳回 /agent 的话用户不知道自己点的那个会话到底怎么了。
      expect(router.currentRoute.value.name).toBe('agent-thread')
      wrapper.unmount()
    })

    it('历史被压缩过时页面上有说明', async () => {
      threadsApi.getAgentThreadMessages.mockResolvedValue({
        ...REPLAY,
        summarized: true,
        summary: 'Here is a summary…',
      })
      const { wrapper } = await mountThreadPage()

      expect(wrapper.get('.history-note').text()).toContain('压缩成摘要')
      wrapper.unmount()
    })

    it('删掉当前会话后清空界面并退回 /agent', async () => {
      threadsApi.getAgentThreadMessages.mockResolvedValue(REPLAY)
      threadsApi.listAgentThreads.mockResolvedValue({
        items: [
          {
            thread_id: THREAD_ID,
            title: '央行降息了吗',
            created_at: '2026-08-18T00:00:00Z',
            last_active_at: '2026-08-18T00:00:00Z',
          },
        ],
        total: 1,
      })
      threadsApi.deleteAgentThread.mockResolvedValue({ thread_id: THREAD_ID })
      vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
      const { wrapper, router } = await mountThreadPage()
      expect(wrapper.text()).toContain('之前问过的')

      threadsApi.listAgentThreads.mockResolvedValue({ items: [], total: 0 })
      await wrapper.get('.thread-item .remove-button').trigger('click')
      await flushPromises()

      expect(router.currentRoute.value.name).toBe('agent-chat')
      expect(wrapper.text()).not.toContain('之前问过的')
      wrapper.unmount()
    })

    it('点「新对话」回到 /agent 并清空界面', async () => {
      threadsApi.getAgentThreadMessages.mockResolvedValue(REPLAY)
      const { wrapper, router } = await mountThreadPage()

      await wrapper.get('.sidebar-head button').trigger('click')
      await flushPromises()

      expect(router.currentRoute.value.name).toBe('agent-chat')
      expect(wrapper.text()).not.toContain('之前问过的')
      wrapper.unmount()
    })

    it('后退回 /agent 时清空界面，下一轮开新会话', async () => {
      // 不清的话旧会话的历史留在界面上，而 threadId 已经没了——用户以为在续聊，
      // 实际上发出去的是一个新会话的第一轮。
      threadsApi.getAgentThreadMessages.mockResolvedValue(REPLAY)
      const { wrapper, router } = await mountPage()
      await router.push(`/agent/${THREAD_ID}`)
      await flushPromises()
      expect(wrapper.text()).toContain('之前问过的')

      await router.back()
      await flushPromises()

      expect(wrapper.text()).not.toContain('之前问过的')
      wrapper.unmount()
    })

    it('会话列表读不出来不影响正常提问', async () => {
      // 侧栏是导航，不是对话的前提。列表挂了还能聊，这条把它钉住。
      const { ApiError } = await import('@/api/client')
      threadsApi.listAgentThreads.mockRejectedValue(
        new ApiError({
          message: '库挂了',
          code: 'agent_thread_database_unavailable',
          status: 503,
          retryable: true,
        }),
      )
      const { wrapper } = await mountPage()

      expect(wrapper.get('.sidebar-error').text()).toContain('会话记录暂时读不出来')

      await wrapper.get('.message-input').setValue('央行利率')
      await wrapper.get('.agent-form').trigger('submit')
      await flushPromises()

      expect(api.streamAgentChat).toHaveBeenCalledOnce()
      wrapper.unmount()
    })
  })
})
