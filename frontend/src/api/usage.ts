import { ApiError, requestJson } from './client'
import { isNonNegativeInteger, isRecord, isStringArray, isUuid } from './json-guards'

/**
 * 用量查询：明细、汇总与模型名列表。
 *
 * 与 `preferences.ts` 同构：这一层只管 HTTP 与形状校验，不碰界面状态。接口返回的是 snake_case
 * 且字段可空，这里翻成前端自己的 camelCase 形状并逐字段校验——用量是展示数据，一次脏响应不该
 * 让设置页整块打不开，但也不该把 `undefined` 渲染成「0 个 token」（那是把「没报」说成「没有」）。
 */

/** 明细里的一行。`cachedTokens` 为 null 表示上游没报过缓存，不是 0。 */
export interface UsageRecord {
  callId: string
  occurredAt: string
  modelName: string | null
  status: 'completed' | 'failed'
  source: 'upstream' | 'missing'
  inputTokens: number
  outputTokens: number
  cachedTokens: number | null
  totalTokens: number
  durationMs: number
  threadId: string | null
  runId: string | null
}

/** 汇总：四个 token 合计加调用次数；`cachedTokens` 为 null 表示上游一次都没报过缓存。 */
export interface UsageSummary {
  inputTokens: number
  outputTokens: number
  cachedTokens: number | null
  totalTokens: number
  callCount: number
}

export interface UsageRecordPage {
  items: UsageRecord[]
  hasMore: boolean
}

/**
 * 明细与汇总共用的一组筛选。空串表示「不筛这一维」，与后端省略该查询参数等价。
 *
 * `start` / `end` 是 UTC 的 ISO 时刻（含起点、不含终点）。用户看到的本地日期在这里之前就已经
 * 换算成 UTC——时区换算只发生在前端展示层，后端只按 UTC 时刻比较。
 */
export interface UsageFilters {
  model: string
  start: string
  end: string
}

/** 明细一页取多少条。与后端 `DEFAULT_USAGE_PAGE_SIZE` 一致，改一边就要改另一边。 */
export const USAGE_PAGE_SIZE = 50

export async function fetchUsageSummary(
  filters: UsageFilters,
  signal?: AbortSignal,
): Promise<UsageSummary> {
  const response = await requestJson<unknown>(`/usage/summary${queryString(filters)}`, {
    method: 'GET',
    signal,
  })
  return readSummary(response)
}

export async function fetchUsageRecords(
  filters: UsageFilters,
  page: { limit: number; offset: number },
  signal?: AbortSignal,
): Promise<UsageRecordPage> {
  const response = await requestJson<unknown>(
    `/usage/records${queryString(filters, {
      limit: String(page.limit),
      offset: String(page.offset),
    })}`,
    { method: 'GET', signal },
  )
  return readRecordPage(response)
}

/**
 * 当前账号用过的模型名，给筛选栏取值。
 *
 * 刻意不跟时间范围走：跟着走的话，选了某个模型再改范围，选项里那个模型可能消失，而下拉还选着
 * 它。口径就是「这个账号用过哪些模型」。
 */
export async function fetchUsageModels(signal?: AbortSignal): Promise<string[]> {
  const response = await requestJson<unknown>('/usage/models', { method: 'GET', signal })
  if (!isStringArray(response)) throw invalidUsageResponse()
  return response
}

/** 拼查询串；空的筛选不出现，让 URL 与后端收到的参数都保持干净。 */
function queryString(filters: UsageFilters, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams()
  if (filters.model !== '') params.set('model', filters.model)
  if (filters.start !== '') params.set('start', filters.start)
  if (filters.end !== '') params.set('end', filters.end)
  for (const [key, value] of Object.entries(extra)) params.set(key, value)
  const text = params.toString()
  return text === '' ? '' : `?${text}`
}

function readSummary(value: unknown): UsageSummary {
  if (!isRecord(value)) throw invalidUsageResponse()
  const { input_tokens, output_tokens, cached_tokens, total_tokens, call_count } = value
  if (
    !isNonNegativeInteger(input_tokens) ||
    !isNonNegativeInteger(output_tokens) ||
    !(cached_tokens === null || isNonNegativeInteger(cached_tokens)) ||
    !isNonNegativeInteger(total_tokens) ||
    !isNonNegativeInteger(call_count)
  ) {
    throw invalidUsageResponse()
  }
  return {
    inputTokens: input_tokens,
    outputTokens: output_tokens,
    // 保留 null：它与 0 是两件事（上游没报过缓存 vs 报了 0 缓存），界面要分开呈现。
    cachedTokens: cached_tokens,
    totalTokens: total_tokens,
    callCount: call_count,
  }
}

function readRecordPage(value: unknown): UsageRecordPage {
  if (!isRecord(value) || !Array.isArray(value.items) || typeof value.has_more !== 'boolean') {
    throw invalidUsageResponse()
  }
  return { items: value.items.map(readRecord), hasMore: value.has_more }
}

function readRecord(value: unknown): UsageRecord {
  if (!isRecord(value)) throw invalidUsageResponse()
  const {
    call_id,
    occurred_at,
    model_name,
    status,
    source,
    input_tokens,
    output_tokens,
    cached_tokens,
    total_tokens,
    duration_ms,
    thread_id,
    run_id,
  } = value
  if (
    !isUuid(call_id) ||
    typeof occurred_at !== 'string' ||
    !(model_name === null || typeof model_name === 'string') ||
    (status !== 'completed' && status !== 'failed') ||
    (source !== 'upstream' && source !== 'missing') ||
    !isNonNegativeInteger(input_tokens) ||
    !isNonNegativeInteger(output_tokens) ||
    !(cached_tokens === null || isNonNegativeInteger(cached_tokens)) ||
    !isNonNegativeInteger(total_tokens) ||
    !isNonNegativeInteger(duration_ms) ||
    !(thread_id === null || (typeof thread_id === 'string' && isUuid(thread_id))) ||
    !(run_id === null || (typeof run_id === 'string' && isUuid(run_id)))
  ) {
    throw invalidUsageResponse()
  }
  return {
    callId: call_id,
    occurredAt: occurred_at,
    modelName: model_name,
    status,
    source,
    inputTokens: input_tokens,
    outputTokens: output_tokens,
    cachedTokens: cached_tokens,
    totalTokens: total_tokens,
    durationMs: duration_ms,
    threadId: thread_id,
    runId: run_id,
  }
}

function invalidUsageResponse(): ApiError {
  return new ApiError({
    message: '用量服务返回了无效的响应。',
    code: 'response_invalid',
  })
}
