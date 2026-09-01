import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AGENT_STREAM_IDLE_TIMEOUT_MS,
  fetchAgentDefaultPrompt,
  streamAgentChat,
  type AgentChatEvent,
} from './agent-chat'
import { setUnauthorizedHandler } from './client'

const encoder = new TextEncoder()

/** 把若干段文本包成一条 SSE 响应。分段本身就是断言对象：帧可以被任意切开。 */
function sseResponse(chunks: string[], init: ResponseInit = {}): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
    ...init,
  })
}

function frame(event: Record<string, unknown>): string {
  return `data: ${JSON.stringify(event)}\n\n`
}

async function collect(chunks: string[]): Promise<AgentChatEvent[]> {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(chunks)))
  const events: AgentChatEvent[] = []
  for await (const event of streamAgentChat({ message: '利率' })) events.push(event)
  return events
}

const THREAD_ID = '30000000-0000-4000-8000-000000000001'

describe('streamAgentChat', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    setUnauthorizedHandler(null)
  })

  it('yields每个事件并把 token 拼成完整回答', async () => {
    const events = await collect([
      frame({ event: 'token', text: '央行' }),
      frame({ event: 'token', text: '维持' }),
      frame({ event: 'done', thread_id: THREAD_ID }),
    ])

    expect(events.map((event) => event.event)).toEqual(['token', 'token', 'done'])
    expect(
      events
        .filter(
          (event): event is Extract<AgentChatEvent, { event: 'token' }> => event.event === 'token',
        )
        .map((event) => event.text)
        .join(''),
    ).toBe('央行维持')
  })

  it('把跨 chunk 切断的帧和多字节汉字拼回来', async () => {
    // '答' 的 UTF-8 是三个字节，这里刻意在第二个字节后切断；帧分隔符也被切开。
    const payload = frame({ event: 'token', text: '答' })
    const bytes = encoder.encode(payload)
    const decoder = new TextDecoder()
    const head = decoder.decode(bytes.slice(0, bytes.length - 4), { stream: true })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(bytes.slice(0, bytes.length - 4))
              controller.enqueue(bytes.slice(bytes.length - 4))
              controller.enqueue(encoder.encode(frame({ event: 'done', thread_id: THREAD_ID })))
              controller.close()
            },
          }),
          { status: 200, headers: { 'content-type': 'text/event-stream' } },
        ),
      ),
    )

    // 先确认这个切点真的把帧切残了，否则本用例什么都没验证。
    expect(head.endsWith('\n\n')).toBe(false)

    const events: AgentChatEvent[] = []
    for await (const event of streamAgentChat({ message: '问' })) events.push(event)
    expect(events).toEqual([
      { event: 'token', text: '答' },
      { event: 'done', thread_id: THREAD_ID },
    ])
  })

  it('跳过心跳注释帧', async () => {
    const events = await collect([
      ': keep-alive\n\n',
      frame({ event: 'token', text: '好' }),
      ': keep-alive\n\n',
      frame({ event: 'done', thread_id: THREAD_ID }),
    ])

    expect(events).toEqual([
      { event: 'token', text: '好' },
      { event: 'done', thread_id: THREAD_ID },
    ])
  })

  it('接受 CRLF 分帧', async () => {
    const events = await collect([
      `data: ${JSON.stringify({ event: 'token', text: 'x' })}\r\n\r\n`,
      `data: ${JSON.stringify({ event: 'done', thread_id: THREAD_ID })}\r\n\r\n`,
    ])

    expect(events.map((event) => event.event)).toEqual(['token', 'done'])
  })

  it('解析没有收尾空行的最后一帧', async () => {
    const events = await collect([`data: ${JSON.stringify({ event: 'token', text: '尾' })}`])

    expect(events).toEqual([{ event: 'token', text: '尾' }])
  })

  it('把 error 事件当作正常事件产出，而不是抛异常', async () => {
    const events = await collect([
      frame({ event: 'token', text: '半句' }),
      frame({
        event: 'error',
        thread_id: THREAD_ID,
        code: 'llm_timeout',
        detail: '模型超时。',
        retryable: true,
      }),
    ])

    // 流已经开始就改不了 HTTP 状态码，所以失败只能作为事件送达。
    expect(events.at(-1)).toEqual({
      event: 'error',
      thread_id: THREAD_ID,
      code: 'llm_timeout',
      detail: '模型超时。',
      retryable: true,
    })
  })

  it('把 tool_call 与 tool_result 原样交给上层', async () => {
    const events = await collect([
      frame({ event: 'tool_call', tool: 'search_news', arguments: { query: '利率' } }),
      frame({ event: 'tool_result', tool: 'search_news', content: '找到 2 篇。', failed: false }),
      frame({ event: 'done', thread_id: THREAD_ID }),
    ])

    expect(events[0]).toEqual({
      event: 'tool_call',
      tool: 'search_news',
      arguments: { query: '利率' },
    })
    expect(events[1]).toMatchObject({ event: 'tool_result', tool: 'search_news', failed: false })
  })

  it.each([
    { name: '未知事件类型', payload: { event: 'thinking', text: 'x' } },
    { name: 'token 缺 text', payload: { event: 'token' } },
    { name: 'done 的 thread_id 不是 UUID', payload: { event: 'done', thread_id: 'not-a-uuid' } },
    // 这条带上 thread_id 是有意的：不带的话它会因为缺 thread_id 被拒，名字说的
    // 「缺 retryable」就没被验到。
    {
      name: 'error 缺 retryable',
      payload: { event: 'error', thread_id: THREAD_ID, code: 'x', detail: 'y' },
    },
    // 失败那一轮同样属于一个已存在的会话，所以 error 也必须带 thread_id——前端靠它
    // 把重试发回同一个会话，而不是另开一个。
    {
      name: 'error 缺 thread_id',
      payload: { event: 'error', code: 'x', detail: 'y', retryable: true },
    },
    {
      name: 'error 的 thread_id 不是 UUID',
      payload: { event: 'error', thread_id: 'not-a-uuid', code: 'x', detail: 'y', retryable: true },
    },
    { name: 'tool_result 缺 content', payload: { event: 'tool_result', tool: 'search_news' } },
  ])('拒绝契约漂移：$name', async ({ payload }) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([frame(payload)])))

    await expect(async () => {
      for await (const _event of streamAgentChat({ message: '问' })) void _event
    }).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it('拒绝解析不出 JSON 的帧', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(['data: {不是 JSON\n\n'])))

    await expect(async () => {
      for await (const _event of streamAgentChat({ message: '问' })) void _event
    }).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it('只在给了 thread_id 和非空提示词时才发这两个字段', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    for await (const _event of streamAgentChat({ message: '问', systemPrompt: '   ' })) void _event
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body as string)).toEqual({ message: '问' })

    fetchMock.mockResolvedValue(sseResponse([]))
    for await (const _event of streamAgentChat({
      message: '再问',
      threadId: THREAD_ID,
      systemPrompt: '你是记者。',
    })) {
      void _event
    }
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body as string)).toEqual({
      message: '再问',
      thread_id: THREAD_ID,
      system_prompt: '你是记者。',
    })
  })

  it('用 POST 与 same-origin 凭据请求 SSE 端点', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    for await (const _event of streamAgentChat({ message: '问' })) void _event

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent/chat',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        headers: expect.objectContaining({ Accept: 'text/event-stream' }),
      }),
    )
  })

  it('流开始前的失败仍然走 HTTP 错误契约，并通知未认证', async () => {
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Unauthorized' }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(async () => {
      for await (const _event of streamAgentChat({ message: '问' })) void _event
    }).rejects.toMatchObject({ status: 401, code: 'authentication_required' })
    expect(unauthorized).toHaveBeenCalledOnce()
  })

  it('把 503 的 agent_runtime_unavailable 原样带给上层', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: 'agent_runtime_unavailable',
            detail: 'Agent 运行时不可用。',
            retryable: true,
          }),
          { status: 503, headers: { 'content-type': 'application/json' } },
        ),
      ),
    )

    await expect(async () => {
      for await (const _event of streamAgentChat({ message: '问' })) void _event
    }).rejects.toMatchObject({ status: 503, code: 'agent_runtime_unavailable', retryable: true })
  })

  it('网络不通标成可重试', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))

    await expect(async () => {
      for await (const _event of streamAgentChat({ message: '问' })) void _event
    }).rejects.toMatchObject({ code: 'network_error', retryable: true, status: 0 })
  })

  it('调用方主动取消时抛出的是 AbortError，不包成 ApiError', async () => {
    const controller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
        return new Promise((_resolve, reject) => {
          init?.signal?.addEventListener(
            'abort',
            () => reject(new DOMException('aborted', 'AbortError')),
            { once: true },
          )
        })
      }),
    )

    const consume = (async () => {
      for await (const _event of streamAgentChat({ message: '问', signal: controller.signal })) {
        void _event
      }
    })()

    controller.abort()
    await expect(consume).rejects.toBeInstanceOf(DOMException)
  })

  it('两帧之间静默过久判为超时', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
        Promise.resolve(
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                controller.enqueue(encoder.encode(frame({ event: 'token', text: '开头' })))
                // 之后不再产出任何字节，也不关闭：模拟链路挂住。
                init?.signal?.addEventListener('abort', () =>
                  controller.error(new DOMException('aborted', 'AbortError')),
                )
              },
            }),
            { status: 200, headers: { 'content-type': 'text/event-stream' } },
          ),
        ),
      ),
    )

    const assertion = expect(async () => {
      for await (const _event of streamAgentChat({ message: '问' })) void _event
    }).rejects.toMatchObject({ code: 'request_timeout', retryable: true })

    await vi.advanceTimersByTimeAsync(AGENT_STREAM_IDLE_TIMEOUT_MS * 2)
    await assertion
  })

  it('上层提前 break 时取消底层读取，不让后端继续跑', async () => {
    let cancelled = false
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode(frame({ event: 'token', text: '一' })))
              controller.enqueue(encoder.encode(frame({ event: 'token', text: '二' })))
            },
            cancel() {
              cancelled = true
            },
          }),
          { status: 200, headers: { 'content-type': 'text/event-stream' } },
        ),
      ),
    )

    for await (const event of streamAgentChat({ message: '问' })) {
      void event
      break
    }

    expect(cancelled).toBe(true)
  })
})

describe('fetchAgentDefaultPrompt', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('返回默认提示词全文', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ system_prompt: '你是新闻研究助手。' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(fetchAgentDefaultPrompt()).resolves.toBe('你是新闻研究助手。')
  })

  it('拒绝空提示词或缺字段的响应', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ system_prompt: '  ' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(fetchAgentDefaultPrompt()).rejects.toMatchObject({ code: 'response_invalid' })
  })
})
