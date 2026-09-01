import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { deleteAgentThread, getAgentThreadMessages, listAgentThreads } from './agent-threads'

const THREAD_ID = '30000000-0000-4000-8000-000000000001'

const summary = {
  thread_id: THREAD_ID,
  title: '央行降息了吗',
  created_at: '2026-08-18T00:00:00Z',
  last_active_at: '2026-08-18T01:00:00Z',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('agent threads API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('把分页参数放进查询串，缺省时不带任何参数', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ items: [summary], total: 1 }))
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 1 }))
    vi.stubGlobal('fetch', fetchMock)

    await listAgentThreads()
    await listAgentThreads({ limit: 20, offset: 40 })

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/agent/threads')
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/agent/threads?limit=20&offset=40')
  })

  it('列表字段缺失时抛 response_invalid，而不是把坏数据交给界面', async () => {
    // total 缺失。界面拿它算总页数，静默当成 0 会让「下一页」永远不可点，
    // 而这个症状看起来像后端只返回了一页。
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ items: [summary] })))

    await expect(listAgentThreads()).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it('回放里 answer 为空串是合法历史，不当成格式错误', async () => {
    // 首轮就失败的会话，checkpointer 里只有提问。用 hasText 校验 answer 会把它判成坏数据，
    // 表现为用户「打不开自己的会话」。
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          thread_id: THREAD_ID,
          turns: [{ question: '没答成的问题', answer: '' }],
          summarized: false,
          summary: null,
        }),
      ),
    )

    const replay = await getAgentThreadMessages(THREAD_ID)

    expect(replay.turns).toHaveLength(1)
    expect(replay.turns[0]?.answer).toBe('')
  })

  it('工具轨迹的 content 为 null 也是合法历史', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          thread_id: THREAD_ID,
          turns: [
            {
              question: '查一下',
              answer: '查不到。',
              traces: [{ tool: 'search_news', arguments: {}, content: null, failed: false }],
            },
          ],
          summarized: false,
        }),
      ),
    )

    const replay = await getAgentThreadMessages(THREAD_ID)

    expect(replay.turns[0]?.traces?.[0]?.content).toBeNull()
  })

  it('把 404 的 code 透传出来，让文案层能认出「会话打不开」', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(
          { code: 'agent_thread_not_found', detail: '会话不存在或已被删除。', retryable: false },
          404,
        ),
      ),
    )

    await expect(getAgentThreadMessages(THREAD_ID)).rejects.toMatchObject({
      code: 'agent_thread_not_found',
      status: 404,
    })
  })

  it('会话 id 经过 URL 编码后才拼进路径', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ thread_id: THREAD_ID }))
    vi.stubGlobal('fetch', fetchMock)

    // 后端会用 422 拒掉这种 id，但拼路径这一步得先安全：不编码的话 `../` 之类的输入
    // 会改变请求的目标路径。
    await deleteAgentThread('a/../b').catch(() => undefined)

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/agent/threads/a%2F..%2Fb')
  })

  it('删除响应缺 thread_id 时报错，不让界面以为删成功了', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({})))

    await expect(deleteAgentThread(THREAD_ID)).rejects.toBeInstanceOf(ApiError)
  })
})
