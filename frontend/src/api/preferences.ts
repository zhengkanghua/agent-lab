import { ApiError, requestJson, type RequestOptions } from './client'
import { isRecord, isNonNegativeInteger } from './json-guards'
import { normalizeMatchesPerDocument, normalizeResultLimit } from './document-search'
import type { components } from './generated/openapi'

export type UserPreferenceDto = components['schemas']['UserPreferenceResponse']
export type UserPreferenceUpdateRequest = components['schemas']['UserPreferenceUpdateRequest']

/** 与应用级偏好 store 无关的纯数据：这一层只管 HTTP 与形状校验。 */
export interface RemotePreferences {
  /** 空串表示未配置，与后端 `null` 对应——两者在界面上都是「用默认」。 */
  systemPrompt: string
  documentLimit: number
  matchesPerDocument: number
}

const PATH = '/auth/me/preferences'

/**
 * 读取当前账号的个人偏好。
 *
 * `notifyUnauthorized: false` 与账号自助改密那条一致：偏好的取值域由登录态决定，但调用方
 * （设置页、检索页）不该因为一次读偏好失败就触发全局登出流程——读不到偏好是「用默认值」，
 * 不是「登录失效」。
 */
export async function fetchPreferences(options: RequestOptions = {}): Promise<RemotePreferences> {
  const response = await requestJson<unknown>(PATH, { method: 'GET' }, options)
  return readPreferences(response)
}

/** 整体覆盖当前账号的个人偏好，返回落库后那一份。 */
export async function savePreferences(
  preferences: RemotePreferences,
  options: RequestOptions = {},
): Promise<RemotePreferences> {
  const payload: UserPreferenceUpdateRequest = {
    // 空串按「未配置」提交：后端把 null 与空白都归一成「用默认提示词」，
    // 这里发空串而不是 null，是为了不动编辑框里那个受控的字符串状态。
    system_prompt: preferences.systemPrompt.trim() === '' ? null : preferences.systemPrompt,
    document_limit: preferences.documentLimit,
    matches_per_document: preferences.matchesPerDocument,
  }
  const response = await requestJson<unknown>(
    PATH,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    options,
  )
  return readPreferences(response)
}

/**
 * 把接口返回的 JSON 规范化成前端偏好。
 *
 * 这里不信任后端返回的形状也不信任它的边界：字段缺失或类型不对时落回默认，数量参数按契约
 * 边界归一。偏好是体验数据，一次脏响应不该让设置页打不开。
 */
function readPreferences(value: unknown): RemotePreferences {
  if (!isRecord(value)) {
    throw invalidPreferencesResponse()
  }
  const systemPrompt = value.system_prompt
  if (systemPrompt !== null && typeof systemPrompt !== 'string') {
    throw invalidPreferencesResponse()
  }
  if (
    !isNonNegativeInteger(value.document_limit) ||
    !isNonNegativeInteger(value.matches_per_document)
  ) {
    throw invalidPreferencesResponse()
  }
  return {
    systemPrompt: systemPrompt ?? '',
    // 归一化按契约边界兜底：后端已经校验过，但这里是前端拿到的最后一个把关点，
    // 一个越界值会让设置页的下拉框选不中任何一项。
    documentLimit: normalizeResultLimit(value.document_limit),
    matchesPerDocument: normalizeMatchesPerDocument(value.matches_per_document),
  }
}

function invalidPreferencesResponse(): ApiError {
  return new ApiError({
    message: 'The account service returned invalid preferences.',
    code: 'response_invalid',
  })
}
