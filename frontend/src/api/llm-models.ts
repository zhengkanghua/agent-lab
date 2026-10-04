import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import { hasText, isNullableString, isPositiveInteger, isRecord, isUuid } from './json-guards'

export type LlmModelDto = components['schemas']['LlmModelResponse']
export type AvailableLlmModelDto = components['schemas']['AvailableLlmModelResponse']
export type LlmModelCreateRequest = components['schemas']['LlmModelCreateRequest']
export type LlmModelUpdateRequest = components['schemas']['LlmModelUpdateRequest']
export type ResolvedLlmModelDto = components['schemas']['ResolvedLlmModel']

/**
 * 可用模型的后台管理接口与用户选择列表。
 *
 * 两边的读路径刻意分开：`listLlmModels` 只给超级用户，回的是原始字段（没有展示名就是
 * `null`，编辑表单据此知道它是一个空输入框）；`listAvailableLlmModels` 任何登录账号都能
 * 读，回的是已经落过回落的展示名，只包含「自身启用且所属渠道也启用」的条目。
 *
 * 本表只停用不删除，所以这里没有任何删除函数。
 */
export async function listLlmModels(signal?: AbortSignal): Promise<LlmModelDto[]> {
  const response = await requestJson<unknown>('/llm-models', { method: 'GET', signal })
  if (!Array.isArray(response) || !response.every(isLlmModel)) throw invalidResponse()
  return response
}

export async function listAvailableLlmModels(
  signal?: AbortSignal,
): Promise<AvailableLlmModelDto[]> {
  const response = await requestJson<unknown>('/llm-models/available', { method: 'GET', signal })
  if (!Array.isArray(response) || !response.every(isAvailableLlmModel)) throw invalidResponse()
  return response
}

export async function createLlmModel(body: LlmModelCreateRequest): Promise<LlmModelDto> {
  return requestLlmModel('/llm-models', { method: 'POST', body: JSON.stringify(body) })
}

export async function updateLlmModel(
  id: string,
  body: LlmModelUpdateRequest,
): Promise<LlmModelDto> {
  return requestLlmModel(`/llm-models/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

async function requestLlmModel(path: string, init: RequestInit): Promise<LlmModelDto> {
  const response = await requestJson<unknown>(path, init)
  if (!isLlmModel(response)) throw invalidResponse()
  return response
}

/**
 * 判定一条后台模型数据的形状。
 *
 * 只认契约上的字段：它不在模型这一行上的东西（例如用户能不能选到）不在这里判——那由服务端
 * 按「自身启用 + 所属渠道启用」算出来，这里拿到的就是算完的结果。
 */
function isLlmModel(value: unknown): value is LlmModelDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    isUuid(value.provider_id) &&
    hasText(value.provider_name) &&
    typeof value.provider_enabled === 'boolean' &&
    hasText(value.upstream_model_name) &&
    isNullableString(value.display_name) &&
    isPositiveInteger(value.context_window) &&
    typeof value.is_default === 'boolean' &&
    typeof value.enabled === 'boolean' &&
    isTimestamp(value.created_at) &&
    isTimestamp(value.updated_at)
  )
}

/** 选择列表那一份：展示名是服务端落过回落的字符串，不会是空。 */
function isAvailableLlmModel(value: unknown): value is AvailableLlmModelDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    hasText(value.display_name) &&
    isPositiveInteger(value.context_window) &&
    hasText(value.provider_name)
  )
}

/**
 * 判定一轮运行里那份**冻结**的模型快照。
 *
 * 与上面那份的区别：这一份是提问那一刻拍下来的（展示名、窗口都是当时的），消费方是回放里的
 * 轮次，不是选择器。三个字段缺一不可——窗口要拿去算压缩，缺了它下游只能静默当作没有。
 */
export function isResolvedLlmModel(value: unknown): value is ResolvedLlmModelDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    hasText(value.display_name) &&
    isPositiveInteger(value.context_window)
  )
}

function isTimestamp(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value))
}

function invalidResponse(): ApiError {
  return new ApiError({
    message: '模型目录服务返回了无效的响应。',
    code: 'response_invalid',
  })
}
