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

const session = vi.hoisted(() => ({ logout: vi.fn() }))

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
    logout: session.logout,
  },
}))

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
    scripted([{ event: 'done', thread_id: THREAD_ID }])
    // jsdom 没有实现 scrollIntoView。
    Element.prototype.scrollIntoView = vi.fn()
  })

  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
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
      { event: 'tool_call', tool: 'search_news', arguments: { query: '利率' } },
      { event: 'tool_result', tool: 'search_news', content: '找到 2 篇。', failed: false },
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
      [{ event: 'error', code: 'llm_timeout', detail: '超时。', retryable: true }],
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
})
