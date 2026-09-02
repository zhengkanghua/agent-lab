import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import type { AgentChatEvent, StreamAgentChatOptions } from '@/api/agent-chat'
import { ApiError } from '@/api/client'
import {
  useAgentChat,
  type AgentChatStream,
  type AgentThreadLoader,
} from '../composables/useAgentChat'

const THREAD_ID = '30000000-0000-4000-8000-000000000001'
const OTHER_THREAD_ID = '30000000-0000-4000-8000-000000000002'

/** 仍然挂载组件而不是裸调 composable：onScopeDispose 的取消语义需要真实的 effect scope。 */
function mountHarness(stream: AgentChatStream, loader?: AgentThreadLoader) {
  let composable: ReturnType<typeof useAgentChat> | undefined
  const Harness = defineComponent({
    setup() {
      composable = loader ? useAgentChat(stream, loader) : useAgentChat(stream)
      return () => h('div')
    },
  })
  const wrapper = mount(Harness)
  if (!composable) throw new Error('Test harness did not initialize composable')
  return { wrapper, chat: composable }
}

/** 回放接口的解析结果。挂起的 Promise 用它做 resolve 的参数类型。 */
type ReplayResult = Awaited<ReturnType<AgentThreadLoader>>

/** 一个按脚本回放的假历史读取器，记下收到的会话 id。 */
function scriptedLoader(...results: unknown[]): AgentThreadLoader & { calls: string[] } {
  const calls: string[] = []
  let index = 0
  const loader = async (threadId: string) => {
    calls.push(threadId)
    const result = results[index++]
    if (result instanceof Error) throw result
    return result
  }
  return Object.assign(loader as AgentThreadLoader, { calls })
}

/**
 * 造一份回放响应。
 *
 * 参数刻意比 DTO 松（traces 可省、条目结构不写全），因为这些用例要验证的正是「后端少给字段时
 * 前端怎么办」。返回处收一次 cast，把松散的字面量交给按 DTO 定型的读取器。
 */
function replay(
  turns: Array<{ question: string; answer: string; traces?: unknown[] }>,
  extra: { summarized?: boolean; summary?: string | null } = {},
): ReplayResult {
  return {
    thread_id: THREAD_ID,
    turns,
    summarized: extra.summarized ?? false,
    summary: extra.summary ?? null,
  } as ReplayResult
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

  it('同名并发调用的结果乱序返回时，仍按 tool_call_id 配到正确的检索词上', async () => {
    // 结果的到达顺序和调用顺序相反，这在并发调用时完全可能。按工具名先来先配的话，「甲」
    // 那条轨迹会挂上乙的结果——界面显示的检索词和结果对不上，用户看不出来哪个是哪个。
    const { wrapper, chat } = mountHarness(
      scriptedStream([
        {
          event: 'tool_call',
          tool_call_id: 'call-1',
          tool: 'search_news',
          arguments: { query: '甲' },
        },
        {
          event: 'tool_call',
          tool_call_id: 'call-2',
          tool: 'search_news',
          arguments: { query: '乙' },
        },
        {
          event: 'tool_result',
          tool_call_id: 'call-2',
          tool: 'search_news',
          content: '乙的结果',
          failed: false,
        },
        {
          event: 'tool_result',
          tool_call_id: 'call-1',
          tool: 'search_news',
          content: '甲的结果',
          failed: false,
        },
        { event: 'done', thread_id: THREAD_ID },
      ]),
    )
    chat.draft.value = '两个都查'

    await chat.send()
    await flushPromises()

    expect(
      chat.turns.value[0]?.traces.map((trace) => [trace.arguments.query, trace.content]),
    ).toEqual([
      ['甲', '甲的结果'],
      ['乙', '乙的结果'],
    ])
    wrapper.unmount()
  })

  it('工具失败原样标记，回答仍然保留', async () => {
    const { wrapper, chat } = mountHarness(
      scriptedStream([
        { event: 'tool_call', tool_call_id: 'call-1', tool: 'read_document', arguments: {} },
        {
          event: 'tool_result',
          tool_call_id: 'call-1',
          tool: 'read_document',
          content: '读取失败。',
          failed: true,
        },
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
        {
          event: 'error',
          thread_id: THREAD_ID,
          code: 'llm_timeout',
          detail: '模型超时。',
          retryable: true,
        },
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
        {
          event: 'tool_call',
          tool_call_id: 'call-1',
          tool: 'search_news',
          arguments: { query: '利率' },
        },
        {
          event: 'error',
          thread_id: THREAD_ID,
          code: 'llm_unavailable',
          detail: '模型不可用。',
          retryable: true,
        },
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

  it('失败那一轮就认下 thread_id，重发落在同一个会话里', async () => {
    /*
     * 这条防的是列表里冒出重复会话。
     *
     * 归属行在流开始之前就写好了，所以失败的这一轮也已经属于一个存在的会话。如果前端只在
     * done 里认 thread_id，那第一轮失败后 threadId 仍是 null，重发的请求不带 id，服务端只能
     * 当成新会话再建一行——同一次提问在列表里占两条，都是「有提问、没答案」。
     *
     * 断言第二次调用带上了 id，而不只是断言 threadId 有值：前者才是「重发落在同一个会话」。
     */
    const stream = scriptedStream(
      [
        {
          event: 'error',
          thread_id: THREAD_ID,
          code: 'llm_rate_limited',
          detail: '上游限流。',
          retryable: true,
        },
      ],
      [{ event: 'done', thread_id: THREAD_ID }],
    )
    const { wrapper, chat } = mountHarness(stream)
    chat.draft.value = '会被限流的问题'

    await chat.send()
    await flushPromises()

    // 第一次是新会话，所以请求里没有 id；失败后前端应当已经认下服务端给的那个。
    expect(stream.calls[0]?.threadId).toBeFalsy()
    expect(chat.threadId.value).toBe(THREAD_ID)

    await chat.retry()
    await flushPromises()

    expect(stream.calls[1]?.threadId).toBe(THREAD_ID)
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

  describe('loadThread', () => {
    it('把历史灌进界面，并把 threadId 指向它，之后就能接着聊', async () => {
      const stream = scriptedStream([{ event: 'done', thread_id: THREAD_ID }])
      const loader = scriptedLoader(
        replay([
          { question: '央行降息了吗', answer: '降了 25 个基点。' },
          { question: '什么时候', answer: '上周四。' },
        ]),
      )
      const { wrapper, chat } = mountHarness(stream, loader)

      await chat.loadThread(THREAD_ID)

      expect(chat.turns.value.map((turn) => turn.question)).toEqual(['央行降息了吗', '什么时候'])
      expect(chat.threadId.value).toBe(THREAD_ID)

      // 关键的接续断言：下一轮必须带上这个 id，否则「点进历史接着聊」实际是开了个新会话。
      chat.draft.value = '还有别的吗'
      await chat.send()
      expect(stream.calls[0]?.threadId).toBe(THREAD_ID)
      wrapper.unmount()
    })

    it('回放出来的轮次一律是 done，不带 error', async () => {
      // 历史里没存当时的失败原因，编一个会让人以为那一轮报过某个具体错误。
      const loader = scriptedLoader(replay([{ question: '问', answer: '答' }]))
      const { wrapper, chat } = mountHarness(scriptedStream(), loader)

      await chat.loadThread(THREAD_ID)

      expect(chat.turns.value[0]?.status).toBe('done')
      expect(chat.turns.value[0]?.error).toBeNull()
      wrapper.unmount()
    })

    it('历史被压缩过时把这件事标出来', async () => {
      const loader = scriptedLoader(
        replay([{ question: '问', answer: '答' }], {
          summarized: true,
          summary: 'Here is a summary…',
        }),
      )
      const { wrapper, chat } = mountHarness(scriptedStream(), loader)

      await chat.loadThread(THREAD_ID)

      expect(chat.isHistoryTruncated.value).toBe(true)
      wrapper.unmount()
    })

    it('打不开时不设 threadId，用户下一轮是新会话而不是被拒', async () => {
      // 设上 threadId 的话，用户在一个打不开的会话里发问，后端按归属拒掉，
      // 界面上却像是模型出错——错误指向完全错误的方向。
      const loader = scriptedLoader(
        new ApiError({ message: '没有', code: 'agent_thread_not_found', status: 404 }),
      )
      const stream = scriptedStream([{ event: 'done', thread_id: OTHER_THREAD_ID }])
      const { wrapper, chat } = mountHarness(stream, loader)

      await chat.loadThread(THREAD_ID)

      expect(chat.threadId.value).toBeNull()
      expect(chat.threadError.value?.title).toBe('会话不存在或已被删除')
      expect(chat.turns.value).toEqual([])

      chat.draft.value = '新问题'
      await chat.send()
      expect(stream.calls[0]?.threadId).toBeNull()
      wrapper.unmount()
    })

    it('切会话时先清掉上一个会话的界面，不残留旧轮次', async () => {
      const loader = scriptedLoader(
        replay([{ question: '旧会话的问题', answer: '旧答案' }]),
        replay([{ question: '新会话的问题', answer: '新答案' }]),
      )
      const { wrapper, chat } = mountHarness(scriptedStream(), loader)
      await chat.loadThread(THREAD_ID)

      await chat.loadThread(OTHER_THREAD_ID)

      expect(chat.turns.value.map((turn) => turn.question)).toEqual(['新会话的问题'])
      wrapper.unmount()
    })

    it('连点两个会话时后发的赢，先发的响应不覆盖界面', async () => {
      let resolveStale: ((value: ReplayResult) => void) | undefined
      let call = 0
      const loader = (async (threadId: string) => {
        call += 1
        if (call === 1) {
          return new Promise<ReplayResult>((resolve) => {
            resolveStale = resolve
          })
        }
        return replay([{ question: `来自 ${threadId}`, answer: '答' }])
      }) as AgentThreadLoader
      const { wrapper, chat } = mountHarness(scriptedStream(), loader)

      const stale = chat.loadThread(THREAD_ID)
      const fresh = chat.loadThread(OTHER_THREAD_ID)
      resolveStale?.(replay([{ question: '陈旧的问题', answer: '陈旧的答案' }]))
      await Promise.all([stale, fresh])

      expect(chat.turns.value.map((turn) => turn.question)).toEqual([`来自 ${OTHER_THREAD_ID}`])
      expect(chat.threadId.value).toBe(OTHER_THREAD_ID)
      wrapper.unmount()
    })

    it('读历史期间不许发送，避免悄悄开一个新会话', async () => {
      let release: ((value: ReplayResult) => void) | undefined
      const loader = (async () =>
        new Promise<ReplayResult>((resolve) => {
          release = resolve
        })) as AgentThreadLoader
      const stream = scriptedStream([{ event: 'done', thread_id: OTHER_THREAD_ID }])
      const { wrapper, chat } = mountHarness(stream, loader)

      const loading = chat.loadThread(THREAD_ID)
      await nextTick()
      chat.draft.value = '趁机发一条'

      expect(chat.isLoadingThread.value).toBe(true)
      expect(chat.canSend.value).toBe(false)
      await chat.send()
      expect(stream.calls).toEqual([])

      release?.(replay([]))
      await loading
      wrapper.unmount()
    })

    it('切会话会掐掉在途的那一轮，旧会话的 token 不写进新会话', async () => {
      const aborted = vi.fn()
      let release: (() => void) | undefined
      const stream = async function* (options: StreamAgentChatOptions) {
        options.signal?.addEventListener('abort', aborted)
        yield { event: 'token', text: '旧会话还在写' } as AgentChatEvent
        await new Promise<void>((resolve) => (release = resolve))
      } as AgentChatStream
      const loader = scriptedLoader(replay([{ question: '新会话', answer: '新答案' }]))
      const { wrapper, chat } = mountHarness(stream, loader)

      chat.draft.value = '旧会话的问题'
      const running = chat.send()
      await flushPromises()

      await chat.loadThread(THREAD_ID)

      expect(aborted).toHaveBeenCalledOnce()
      expect(chat.turns.value.map((turn) => turn.question)).toEqual(['新会话'])

      release?.()
      await running
      // 在途那一轮结束后也不该再往界面上写字。
      expect(chat.turns.value.map((turn) => turn.question)).toEqual(['新会话'])
      wrapper.unmount()
    })

    it('历史里没有结果的工具轨迹收成一句说明，不一直转圈', async () => {
      const loader = scriptedLoader(
        replay([
          {
            question: '查一下',
            answer: '',
            traces: [{ tool: 'search_news', arguments: { query: '利率' }, content: null }],
          },
        ]),
      )
      const { wrapper, chat } = mountHarness(scriptedStream(), loader)

      await chat.loadThread(THREAD_ID)

      const trace = chat.turns.value[0]?.traces[0]
      expect(trace?.content).toBe('这次工具调用没有结果记录，当时的对话中断了。')
      expect(trace?.failed).toBe(true)
      wrapper.unmount()
    })

    it('开新对话会清掉回放留下的错误与压缩标记', async () => {
      const loader = scriptedLoader(
        new ApiError({ message: '没有', code: 'agent_thread_not_found', status: 404 }),
      )
      const { wrapper, chat } = mountHarness(scriptedStream(), loader)
      await chat.loadThread(THREAD_ID)

      chat.startNewConversation()

      expect(chat.threadError.value).toBeNull()
      expect(chat.isHistoryTruncated.value).toBe(false)
      wrapper.unmount()
    })
  })
})
