import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import UsageSection from '../components/UsageSection.vue'

let wrapper: VueWrapper
let client: QueryClient

const CALL_ID = '11111111-1111-4111-8111-111111111111'

function summaryBody(overrides: Record<string, unknown> = {}) {
  return {
    input_tokens: 1000,
    output_tokens: 400,
    cached_tokens: 120,
    total_tokens: 1400,
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

interface StubOptions {
  summary?: Record<string, unknown>
  records?: Record<string, unknown>[]
  hasMore?: boolean
  models?: string[]
  status?: number
}

/** 按路径分派假响应，并记下每次请求的 URL。 */
function stubApi(options: StubOptions = {}) {
  const urls: URL[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost')
      urls.push(url)
      const status = options.status ?? 200
      if (url.pathname.endsWith('/usage/summary')) {
        return Response.json(options.summary ?? summaryBody(), { status })
      }
      if (url.pathname.endsWith('/usage/records')) {
        return Response.json(
          { items: options.records ?? [recordBody()], has_more: options.hasMore ?? false },
          { status },
        )
      }
      if (url.pathname.endsWith('/usage/models')) {
        return Response.json(options.models ?? ['gpt-x'], { status })
      }
      throw new Error(`unexpected request: ${url.pathname}`)
    }),
  )
  return urls
}

function mountSection(): VueWrapper {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  wrapper = mount(UsageSection, {
    global: { plugins: [[VueQueryPlugin, { queryClient: client }]] },
  })
  return wrapper
}

/** 只关心筛选参数，去掉分页与顺序差异。 */
function filtersOf(urls: URL[]): string[] {
  return urls.map((url) => {
    const params = new URLSearchParams(url.search)
    params.delete('limit')
    params.delete('offset')
    return `${url.pathname}?${params.toString()}`
  })
}

afterEach(() => {
  wrapper?.unmount()
  client?.clear()
  vi.unstubAllGlobals()
})

describe('UsageSection', () => {
  it('汇总四个数字与调用次数来自汇总接口，明细来自明细接口', async () => {
    stubApi({ records: [recordBody()], models: ['gpt-x'] })
    const current = mountSection()
    await flushPromises()

    const totals = current.findAll('.total').map((item) => item.text())
    expect(totals[0]).toContain('调用次数')
    expect(totals[0]).toContain('3')
    expect(totals[1]).toContain('1,000')
    expect(totals[2]).toContain('400')
    expect(totals[3]).toContain('120')
    expect(totals[4]).toContain('1,400')

    const row = current.find('tbody tr')
    expect(row.text()).toContain('gpt-x')
    expect(row.text()).toContain('完成')
    expect(row.text()).toContain('1.2 s')
  })

  it('缓存缺失显示成破折号，不是 0', async () => {
    stubApi({
      records: [recordBody({ cached_tokens: null })],
      summary: summaryBody({ cached_tokens: null }),
    })
    const current = mountSection()
    await flushPromises()

    expect(current.findAll('.total')[3]!.text()).toContain('—')
    const cells = current.find('tbody tr').findAll('td')
    // 发生时刻、模型、状态、输入、输出、缓存、合计、耗时
    expect(cells[5]!.text()).toBe('—')
  })

  it('模型筛选同时作用于汇总与明细', async () => {
    const urls = stubApi({ models: ['gpt-x', 'gpt-y'] })
    const current = mountSection()
    await flushPromises()
    urls.length = 0

    await current.find('#usage-model').setValue('gpt-y')
    await flushPromises()

    const filters = filtersOf(urls)
    expect(filters).toContain('/api/usage/summary?model=gpt-y')
    expect(filters).toContain('/api/usage/records?model=gpt-y')
  })

  it('时间范围换算成 UTC 时刻后同时作用于汇总与明细', async () => {
    const urls = stubApi()
    const current = mountSection()
    await flushPromises()
    urls.length = 0

    await current.find('#usage-start').setValue('2026-03-01')
    await flushPromises()

    const expectedStart = new Date('2026-03-01T00:00:00').toISOString()
    const filters = filtersOf(urls)
    expect(filters).toContain(`/api/usage/summary?start=${encodeURIComponent(expectedStart)}`)
    expect(filters).toContain(`/api/usage/records?start=${encodeURIComponent(expectedStart)}`)
  })

  it('模型下拉的选项来自接口返回的、当前账号用过的模型', async () => {
    stubApi({ models: ['gpt-x', 'gpt-y'] })
    const current = mountSection()
    await flushPromises()

    const options = current.findAll('#usage-model option').map((item) => item.text())
    expect(options).toEqual(['全部模型', 'gpt-x', 'gpt-y'])
  })

  it('一条记录都没有时显示空状态，而不是报错', async () => {
    stubApi({
      records: [],
      summary: summaryBody({
        input_tokens: 0,
        output_tokens: 0,
        cached_tokens: 0,
        total_tokens: 0,
        call_count: 0,
      }),
    })
    const current = mountSection()
    await flushPromises()

    expect(current.find('.empty-state').exists()).toBe(true)
    expect(current.find('.records').exists()).toBe(false)
    expect(current.find('.load-error').exists()).toBe(false)
  })

  it('用量库不可用时给出明确提示，不把空列表渲染成「没有消耗」', async () => {
    stubApi({ status: 503 })
    const current = mountSection()
    await flushPromises()

    expect(current.find('.load-error').text()).toContain('用量库当前不可用')
    expect(current.find('.totals').exists()).toBe(false)
  })
})
