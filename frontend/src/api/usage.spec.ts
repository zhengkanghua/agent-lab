import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { fetchUsageModels, fetchUsageRecords, fetchUsageSummary } from './usage'

const CALL_ID = '11111111-1111-4111-8111-111111111111'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function summaryBody(overrides: Record<string, unknown> = {}) {
  return {
    input_tokens: 100,
    output_tokens: 40,
    cached_tokens: 12,
    total_tokens: 140,
    call_count: 3,
    ...overrides,
  }
}

function recordBody(overrides: Record<string, unknown> = {}) {
  return {
    call_id: CALL_ID,
    occurred_at: '2026-03-01T04:00:00Z',
    model_name: 'gpt-x',
    status: 'completed',
    source: 'upstream',
    input_tokens: 10,
    output_tokens: 4,
    cached_tokens: 6,
    total_tokens: 14,
    duration_ms: 1234,
    thread_id: null,
    run_id: null,
    ...overrides,
  }
}

/** 记下每次请求的 URL，方便断言查询串。 */
function stubFetch(body: (url: URL) => Response) {
  const urls: URL[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost')
      urls.push(url)
      return body(url)
    }),
  )
  return urls
}

describe('usage API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('汇总把空筛选留成没有查询串，snake_case 翻成 camelCase', async () => {
    const urls = stubFetch(() => jsonResponse(summaryBody()))

    await expect(fetchUsageSummary({ model: '', start: '', end: '' })).resolves.toEqual({
      inputTokens: 100,
      outputTokens: 40,
      cachedTokens: 12,
      totalTokens: 140,
      callCount: 3,
    })
    expect(urls[0]!.pathname).toBe('/api/usage/summary')
    expect(urls[0]!.search).toBe('')
  })

  it('明细与汇总带上同一组筛选，明细另带分页', async () => {
    const urls = stubFetch((url) =>
      url.pathname.endsWith('/usage/summary')
        ? jsonResponse(summaryBody())
        : jsonResponse({ items: [recordBody()], has_more: true }),
    )

    await fetchUsageSummary({ model: 'gpt-x', start: 'A', end: 'B' })
    await fetchUsageRecords({ model: 'gpt-x', start: 'A', end: 'B' }, { limit: 50, offset: 100 })

    expect(urls[0]!.searchParams.get('model')).toBe('gpt-x')
    expect(urls[0]!.searchParams.get('start')).toBe('A')
    expect(urls[0]!.searchParams.get('end')).toBe('B')
    expect(urls[1]!.searchParams.get('model')).toBe('gpt-x')
    expect(urls[1]!.searchParams.get('limit')).toBe('50')
    expect(urls[1]!.searchParams.get('offset')).toBe('100')
  })

  it('缓存缺失保留成 null，不折成 0', async () => {
    stubFetch(() => jsonResponse(summaryBody({ cached_tokens: null })))
    const summary = await fetchUsageSummary({ model: '', start: '', end: '' })
    expect(summary.cachedTokens).toBeNull()

    stubFetch(() => jsonResponse({ items: [recordBody({ cached_tokens: null })], has_more: false }))
    const page = await fetchUsageRecords(
      { model: '', start: '', end: '' },
      { limit: 50, offset: 0 },
    )
    expect(page.items[0]!.cachedTokens).toBeNull()
  })

  it('明细响应形状不对时抛出 response_invalid，而不是把脏数据交给界面', async () => {
    stubFetch(() =>
      jsonResponse({ items: [{ ...recordBody(), call_id: 'not-a-uuid' }], has_more: false }),
    )

    await expect(
      fetchUsageRecords({ model: '', start: '', end: '' }, { limit: 50, offset: 0 }),
    ).rejects.toMatchObject({ code: 'response_invalid' } satisfies Partial<ApiError>)
  })

  it('模型名列表只接受字符串数组', async () => {
    stubFetch(() => jsonResponse(['model-a', 'model-b']))
    await expect(fetchUsageModels()).resolves.toEqual(['model-a', 'model-b'])

    stubFetch(() => jsonResponse([{ name: 'model-a' }]))
    await expect(fetchUsageModels()).rejects.toMatchObject({
      code: 'response_invalid',
    } satisfies Partial<ApiError>)
  })

  it('用量库不可用时把 503 原样抛给调用方，由界面决定文案', async () => {
    stubFetch(() =>
      jsonResponse(
        { code: 'usage_database_unavailable', detail: '用量库当前不可用。', retryable: true },
        503,
      ),
    )

    await expect(fetchUsageSummary({ model: '', start: '', end: '' })).rejects.toMatchObject({
      status: 503,
      code: 'usage_database_unavailable',
    } satisfies Partial<ApiError>)
  })
})
