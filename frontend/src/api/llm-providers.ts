import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import { hasText, isHttpUrl, isRecord, isUuid } from './json-guards'

export type LlmProviderDto = components['schemas']['LlmProviderResponse']
export type LlmProviderKind = components['schemas']['LlmProvider']
export type LlmProviderCreateRequest = components['schemas']['LlmProviderCreateRequest']
export type LlmProviderUpdateRequest = components['schemas']['LlmProviderUpdateRequest']

/**
 * 上游渠道目录的缓存身份。
 *
 * 后台两个板块读的是同一份数据：渠道目录本身，以及新增可用模型时的「所属渠道」下拉。两边
 * 用同一个 key，命中的才是同一份缓存——否则同一份目录会有两份缓存，在一个页面上改完渠道、
 * 切到另一个页面还可能看到旧值。
 */
export const llmProvidersQueryKey = ['llm-providers', 'management'] as const

/**
 * 上游渠道的后台管理接口。
 *
 * 凭据只有入口没有出口：请求体里可以带明文，响应里永远只有 `credential_configured` 这个
 * 布尔。读取路径上没有任何「撤回凭据」的函数——本表只停用不删除，凭据也不提供清空。
 */
export async function listLlmProviders(signal?: AbortSignal): Promise<LlmProviderDto[]> {
  const response = await requestJson<unknown>('/llm-providers', { method: 'GET', signal })
  if (!Array.isArray(response) || !response.every(isLlmProvider)) throw invalidResponse()
  return response
}

export async function createLlmProvider(
  body: LlmProviderCreateRequest,
): Promise<LlmProviderDto> {
  return requestLlmProvider('/llm-providers', { method: 'POST', body: JSON.stringify(body) })
}

export async function updateLlmProvider(
  id: string,
  body: LlmProviderUpdateRequest,
): Promise<LlmProviderDto> {
  return requestLlmProvider(`/llm-providers/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

async function requestLlmProvider(path: string, init: RequestInit): Promise<LlmProviderDto> {
  const response = await requestJson<unknown>(path, init)
  if (!isLlmProvider(response)) throw invalidResponse()
  return response
}

/**
 * 判定一条渠道的形状。
 *
 * 这里刻意只认契约上的字段：多出来的字段（尤其是将来可能出现的凭据字段）既不被读取，也不被
 * 转发出去——页面拿到的对象就是这几个字段。
 */
function isLlmProvider(value: unknown): value is LlmProviderDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    hasText(value.name) &&
    isProviderKind(value.provider) &&
    isHttpUrl(value.base_url) &&
    typeof value.enabled === 'boolean' &&
    typeof value.credential_configured === 'boolean' &&
    typeof value.created_at === 'string' &&
    !Number.isNaN(Date.parse(value.created_at)) &&
    typeof value.updated_at === 'string' &&
    !Number.isNaN(Date.parse(value.updated_at))
  )
}

export function isProviderKind(value: unknown): value is LlmProviderKind {
  return value === 'openai_compatible' || value === 'ollama'
}

function invalidResponse(): ApiError {
  return new ApiError({
    message: '模型目录服务返回了无效的响应。',
    code: 'response_invalid',
  })
}
