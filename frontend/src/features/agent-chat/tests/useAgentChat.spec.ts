import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import type { AgentChatEvent, StreamAgentChatOptions } from '@/api/agent-chat'
import { ApiError } from '@/api/client'
import { useAgentChat, type AgentChatStream } from '../composables/useAgentChat'

const THREAD_ID = '30000000-0000-4000-8000-000000000001'
const OTHER_THREAD_ID = '30000000-0000-4000-8000-000000000002'

/** 仍然挂载组件而不是裸调 composable：onScopeDispose 的取消语义需要真实的 effect scope。 */
function mountHarness(stream: AgentChatStream) {
  let composable: ReturnType<typeof useAgentChat> | undefined
  const Harness = defineComponent({
    setup() {
      composable = useAgentChat(stream)
      return () => h('div')
    },
  })
  const wrapper = mount(Harness)
  if (!composable) throw new Error('Test harness did not initialize composable')
  return { wrapper, chat: composable }
}

/** 一条按脚本产出的假流，同时记下每次调用的参数。 */
function scriptedStream(...runs: AgentChatEvent[][]): AgentChatStream & {
  calls: StreamAgentChatOptions[]
} {
  const calls: StreamAgentChatOptions[] = []
  let index = 0
  const stream = async function* (options: StreamAgentChatOptions) {
    calls.push(options)
    for (const event of runs[index++] ?? []) yield event
  }
  return Object.assign(stream as AgentChatStream, { calls })
}

function failingStream(error: unknown): AgentChatStream {
  return async function* () {
    yield { event: 'token', text: '半句' } as AgentChatEvent
    throw error
  } as AgentChatStream
}

describe('useAgentChat', () => {
  it('拒绝空提问且不发请求', async () => {
    const stream = scriptedStream()
    const { wrapper, chat } = mountHarness(stream)

    await chat.send()

    expect(chat.inputError.value).toContain('请输入')
    expect(stream.calls).toHaveLength(0)
    expect(chat.turns.value).toHaveLength(0)
    wrapper.unmount()
  })

  it('输入变合法后清掉校验错误', async () => {
    const { wrapper, chat } = mountHarness(scriptedStream())

    await chat.send()
    expect(chat.inputError.value).not.toBeNull()

    chat.draft.value = '央行利率'
    await nextTick()

    expect(chat.inputError.value).toBeNull()
    wrapper.unmount()
  })

  it('把 token 拼成回答并记下服务端给的会话 id', async () => {
    const { wrapper, chat } = mountHarness(
      scriptedStream([
        { event: 'token', text: '央行' },
        { event: 'token', text: '维持利率不变。' },
        { event: 'done', thread_id: THREAD_ID },
      ]),
    )
    chat.draft.value = '央行利率'

    await chat.send()
    await flushPromises()

    expect(chat.turns.value).toHaveLength(1)
    expect(chat.turns.value[0]).toMatchObject({
      question: '央行利率',
      answer: '央行维持利率不变。',
      status: 'done',
      error: null,
    })
    expect(chat.threadId.value).toBe(THREAD_ID)
    // 发出后清空输入框，否则用户要手删上一条才能问下一个问题。
    expect(chat.draft.value).toBe('')
    expect(chat.status.value).toBe('idle')
    wrapper.unmount()
  })

  it('第二轮带上第一轮拿到的 thread_id', async () => {
    const stream = scriptedStream(
      [{ event: 'done', thread_id: THREAD_ID }],
      [{ event: 'done', thread_id: THREAD_ID }],
    )
    const { wrapper, chat } = mountHarness(stream)

    chat.draft.value = '第一问'
    await chat.send()
    await flushPromises()

    chat.draft.value = '第二问'
    await chat.send()
    await flushPromises()

    expect(stream.calls[0]?.threadId).toBeNull()
    expect(stream.calls[1]?.threadId).toBe(THREAD_ID)
    expect(chat.turns.value).toHaveLength(2)
    wrapper.unmount()
  })

  it('把工具调用与结果并成一条轨迹', async () => {
    const { wrapper, chat } = mountHarness(
      scriptedStream([
        { event: 'tool_call', tool: 'search_news', arguments: { query: '利率' } },
        { event: 'tool_result', tool: 'search_news', content: '找到 2 篇。', failed: false },
        { event: 'done', thread_id: THREAD_ID },
      ]),
    )
    chat.draft.value = '利率'

    await chat.send()
    await flushPromises()

    expect(chat.turns.value[0]?.traces).toHaveLength(1)
    expect(chat.turns.value[0]?.traces[0]).toMatchObject({
      tool: 'search_news',
      arguments: { query: '利率' },
      content: '找到 2 篇。',
      failed: false,
    })
    wrapper.unmount()
  })

  it('同名并发调用按到达顺序配对，不会把两个结果塞进同一条', async () => {
    const { wrapper, chat } = mountHarness(
      scriptedStream([
        { event: 'tool_call', tool: 'search_news', arguments: { query: '甲' } },
        { event: 'tool_call', tool: 'search_news', arguments: { query: '乙' } },
        { event: 'tool_result', tool: 'search_news', content: '甲的结果', failed: false },
        { event: 'tool_result', tool: 'search_news', content: '乙的结果', failed: false },
        { event: 'done', thread_id: THREAD_ID },
      ]),
    )
    chat.draft.value = '两个都查'

    await chat.send()
    await flushPromises()

    expect(chat.turns.value[0]?.traces.map((trace) => trace.content)).toEqual([
      '甲的结果',
      '乙的结果',
    ])
    wrapper.unmount()
  })

  it('工具失败原样标记，回答仍然保留', async () => {
    const { wrapper, chat } = mountHarness(
      scriptedStream([
        { event: 'tool_call', tool: 'read_document', arguments: {} },
        { event: 'tool_result', tool: 'read_document', content: '读取失败。', failed: true },
        { event: 'token', text: '我没读到全文，但根据摘要…' },
        { event: 'done', thread_id: THREAD_ID },
      ]),
    )
    chat.draft.value = '读一下'

    await chat.send()
    await flushPromises()

    expect(chat.turns.value[0]?.traces[0]?.failed).toBe(true)
    expect(chat.turns.value[0]?.answer).toContain('我没读到全文')
    // 工具失败不等于这一轮失败：模型仍然作答了。
    expect(chat.turns.value[0]?.status).toBe('done')
    expect(chat.turns.value[0]?.error).toBeNull()
    wrapper.unmount()
  })

  it('error 事件翻译成用户文案，已收到的半句回答保留', async () => {
    const { wrapper, chat } = mountHarness(
      scriptedStream([
        { event: 'token', text: '正在看…' },
        { event: 'error', code: 'llm_timeout', detail: '模型超时。', retryable: true },
      ]),
    )
    chat.draft.value = '问题'

    await chat.send()
    await flushPromises()

    expect(chat.turns.value[0]?.status).toBe('error')
    expect(chat.turns.value[0]?.answer).toBe('正在看…')
    expect(chat.turns.value[0]?.error).toEqual({
      title: '模型响应超时',
      description: expect.stringContaining('重发'),
      retryable: true,
    })
    wrapper.unmount()
  })

  it('未完成的工具轨迹在中断时被收尾，不会一直转圈', async () => {
    const { wrapper, chat } = mountHarness(
      scriptedStream([
        { event: 'tool_call', tool: 'search_news', arguments: { query: '利率' } },
        { event: 'error', code: 'llm_unavailable', detail: '模型不可用。', retryable: true },
      ]),
    )
    chat.draft.value = '问题'

    await chat.send()
    await flushPromises()

    expect(chat.turns.value[0]?.traces[0]?.content).not.toBeNull()
    expect(chat.turns.value[0]?.traces[0]?.failed).toBe(true)
    wrapper.unmount()
  })

  it('流抛出的 ApiError 翻译成文案', async () => {
    const { wrapper, chat } = mountHarness(
      failingStream(
        new ApiError({
          message: 'unavailable',
          code: 'agent_runtime_unavailable',
          status: 503,
          retryable: true,
        }),
      ),
    )
    chat.draft.value = '问题'

    await chat.send()
    await flushPromises()

    expect(chat.turns.value[0]?.error?.title).toBe('Agent 尚未就绪')
    // 表里把配置类错误钉成不可重试，覆盖后端给的 retryable：重发同一个问题不会让配置变好。
    expect(chat.turns.value[0]?.error?.retryable).toBe(false)
    wrapper.unmount()
  })

  it('非 ApiError 的意外异常退到兜底文案', async () => {
    const { wrapper, chat } = mountHarness(failingStream(new TypeError('boom')))
    chat.draft.value = '问题'

    await chat.send()
    await flushPromises()

    expect(chat.turns.value[0]?.status).toBe('error')
    expect(chat.turns.value[0]?.error?.title).toBe('本轮对话未完成')
    wrapper.unmount()
  })

  it('取消把这一轮标成 cancelled 并保留已收到的文字', async () => {
    let release: (() => void) | undefined
    const stream = async function* () {
      yield { event: 'token', text: '开头' } as AgentChatEvent
      await new Promise<void>((resolve) => (release = resolve))
      yield { event: 'token', text: '不该出现' } as AgentChatEvent
    } as AgentChatStream

    const { wrapper, chat } = mountHarness(stream)
    chat.draft.value = '问题'

    const running = chat.send()
    await flushPromises()
    expect(chat.turns.value[0]?.answer).toBe('开头')

    chat.cancel()
    release?.()
    await running
    await flushPromises()

    expect(chat.turns.value[0]).toMatchObject({ answer: '开头', status: 'cancelled' })
    expect(chat.status.value).toBe('idle')
    wrapper.unmount()
  })

  it('取消后的事件不再写进界面', async () => {
    let release: (() => void) | undefined
    const stream = async function* () {
      yield { event: 'token', text: '开头' } as AgentChatEvent
      await new Promise<void>((resolve) => (release = resolve))
      yield { event: 'token', text: '陈旧' } as AgentChatEvent
      yield { event: 'done', thread_id: OTHER_THREAD_ID } as AgentChatEvent
    } as AgentChatStream

    const { wrapper, chat } = mountHarness(stream)
    chat.draft.value = '问题'
    const running = chat.send()
    await flushPromises()

    chat.cancel()
    release?.()
    await running
    await flushPromises()

    expect(chat.turns.value[0]?.answer).toBe('开头')
    // done 事件也不能生效：否则会把一个已取消运行的 thread 记成当前会话。
    expect(chat.threadId.value).toBeNull()
    wrapper.unmount()
  })

  it('流式期间不接受新的一轮', async () => {
    let release: (() => void) | undefined
    let starts = 0
    const blocking = async function* () {
      starts += 1
      yield { event: 'token', text: '在写' } as AgentChatEvent
      await new Promise<void>((resolve) => (release = resolve))
    } as AgentChatStream

    const { wrapper, chat } = mountHarness(blocking)
    chat.draft.value = '第一问'
    const running = chat.send()
    await flushPromises()

    chat.draft.value = '第二问'
    await chat.send()

    expect(starts).toBe(1)
    expect(chat.turns.value).toHaveLength(1)
    expect(chat.canSend.value).toBe(false)
    // 第二问留在输入框里，没被当成已发送清掉。
    expect(chat.draft.value).toBe('第二问')

    release?.()
    await running
    wrapper.unmount()
  })

  it('重发用最后一轮的提问再问一次，历史里保留失败那轮', async () => {
    const stream = scriptedStream(
      [{ event: 'error', code: 'llm_timeout', detail: '超时。', retryable: true }],
      [
        { event: 'token', text: '这次成了' },
        { event: 'done', thread_id: THREAD_ID },
      ],
    )
    const { wrapper, chat } = mountHarness(stream)
    chat.draft.value = '会超时的问题'

    await chat.send()
    await flushPromises()
    await chat.retry()
    await flushPromises()

    expect(stream.calls.map((call) => call.message)).toEqual(['会超时的问题', '会超时的问题'])
    expect(chat.turns.value).toHaveLength(2)
    expect(chat.turns.value[1]?.answer).toBe('这次成了')
    wrapper.unmount()
  })

  it('新会话清掉历史与 thread_id', async () => {
    const stream = scriptedStream(
      [{ event: 'done', thread_id: THREAD_ID }],
      [{ event: 'done', thread_id: OTHER_THREAD_ID }],
    )
    const { wrapper, chat } = mountHarness(stream)

    chat.draft.value = '第一问'
    await chat.send()
    await flushPromises()
    expect(chat.threadId.value).toBe(THREAD_ID)

    chat.startNewConversation()
    expect(chat.turns.value).toHaveLength(0)
    expect(chat.threadId.value).toBeNull()

    chat.draft.value = '重新开始'
    await chat.send()
    await flushPromises()

    // 不清 thread_id 的话，模型还看得见用户以为已经删掉的历史。
    expect(stream.calls[1]?.threadId).toBeNull()
    wrapper.unmount()
  })

  it('把自定义系统提示词交给流', async () => {
    const stream = scriptedStream([{ event: 'done', thread_id: THREAD_ID }])
    const { wrapper, chat } = mountHarness(stream)
    chat.systemPrompt.value = '你是财经记者。'
    chat.draft.value = '问题'

    await chat.send()
    await flushPromises()

    expect(stream.calls[0]?.systemPrompt).toBe('你是财经记者。')
    wrapper.unmount()
  })

  it('流正常结束但没有 done 事件时按完成处理，保留已收到的回答', async () => {
    const { wrapper, chat } = mountHarness(scriptedStream([{ event: 'token', text: '只有半句' }]))
    chat.draft.value = '问题'

    await chat.send()
    await flushPromises()

    expect(chat.turns.value[0]).toMatchObject({ answer: '只有半句', status: 'done' })
    expect(chat.threadId.value).toBeNull()
    wrapper.unmount()
  })

  it('卸载时取消在途运行', async () => {
    const aborted = vi.fn()
    let release: (() => void) | undefined
    const stream = async function* (options: StreamAgentChatOptions) {
      options.signal?.addEventListener('abort', aborted)
      yield { event: 'token', text: '在写' } as AgentChatEvent
      await new Promise<void>((resolve) => (release = resolve))
    } as AgentChatStream

    const { wrapper, chat } = mountHarness(stream)
    chat.draft.value = '问题'
    const running = chat.send()
    await flushPromises()

    wrapper.unmount()
    expect(aborted).toHaveBeenCalledOnce()

    release?.()
    await running
  })
})
