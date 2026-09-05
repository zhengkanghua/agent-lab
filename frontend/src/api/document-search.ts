import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import {
  hasText,
  isFiniteNumber,
  isHttpUrl,
  isNonNegativeInteger,
  isNullableString,
  isPositiveInteger,
  isRecord,
  isSha256,
  isStringArray,
  isUuid,
} from './json-guards'

export type DocumentSearchRequest = components['schemas']['DocumentSearchRequest']
export type DocumentSearchResultDto = components['schemas']['DocumentSearchResult']
export type DocumentSearchMatchDto = components['schemas']['DocumentSearchMatch']

/**
 * 数量参数的契约边界。与后端一一对应：document_limit 是 1..100，matches_per_document 是
 * 1..20。放在 api 层是因为它们是请求契约的一部分——检索页的输入条与设置中心的「检索偏好」
 * 是两个不同的 UI 消费方，边界只有这一份，谁都不许自己另抄一份。
 *
 * UI 的下拉选项可以比 MAX 收得更窄（产品选择），但归一化必须按契约兜底，以免发出后端
 * 必然拒绝的请求。MAX_QUERY_CHARACTERS 同理，仍在
 * features/semantic-search/model/search-validation.ts（它只服务检索输入框一处）。
 */
export const MIN_RESULT_LIMIT = 1
export const MAX_RESULT_LIMIT = 100
export const DEFAULT_RESULT_LIMIT = 10
export const DEFAULT_MATCHES_PER_DOCUMENT = 3
export const MAX_MATCHES_PER_DOCUMENT = 20

export function normalizeResultLimit(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_RESULT_LIMIT
  return Math.min(MAX_RESULT_LIMIT, Math.max(MIN_RESULT_LIMIT, Math.trunc(value)))
}

export function normalizeMatchesPerDocument(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_MATCHES_PER_DOCUMENT
  return Math.min(MAX_MATCHES_PER_DOCUMENT, Math.max(MIN_RESULT_LIMIT, Math.trunc(value)))
}

export interface SearchDocumentsOptions {
  query: string
  documentLimit: number
  matchesPerDocument: number
  signal?: AbortSignal
}

export async function searchDocuments({
  query,
  documentLimit,
  matchesPerDocument,
  signal,
}: SearchDocumentsOptions): Promise<DocumentSearchResultDto[]> {
  const payload: DocumentSearchRequest = {
    query,
    document_limit: documentLimit,
    matches_per_document: matchesPerDocument,
  }

  const response = await requestJson<unknown>('/document-search', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })

  if (!Array.isArray(response)) {
    throw new ApiError({
      message: 'The search service returned an unexpected result shape.',
      code: 'response_invalid',
    })
  }

  if (!response.every(isDocumentSearchResultDto)) {
    throw new ApiError({
      message: 'The search service returned an invalid document result.',
      code: 'response_invalid',
    })
  }

  return response
}

function isDocumentSearchResultDto(value: unknown): value is DocumentSearchResultDto {
  if (!isRecord(value)) return false

  if (
    !isUuid(value.document_id) ||
    !isSha256(value.content_hash) ||
    !hasText(value.title) ||
    !isHttpUrl(value.url) ||
    !hasText(value.source_name) ||
    !isNullableString(value.published_at) ||
    !isStringArray(value.authors) ||
    !isStringArray(value.labels) ||
    !isPositiveInteger(value.chunk_count) ||
    !isFiniteNumber(value.best_score) ||
    !isDocumentSearchMatchDto(value.best_match)
  ) {
    return false
  }

  let additionalMatches: DocumentSearchMatchDto[] = []
  if (value.additional_matches !== undefined) {
    if (
      !Array.isArray(value.additional_matches) ||
      !value.additional_matches.every(isDocumentSearchMatchDto)
    ) {
      return false
    }
    additionalMatches = value.additional_matches
  }

  const matches = [value.best_match, ...additionalMatches]
  if (
    value.best_score !== value.best_match.score ||
    matches.some((match) => match.chunk_count !== value.chunk_count) ||
    new Set(matches.map((match) => match.chunk_id)).size !== matches.length
  ) {
    return false
  }

  return matches.every((match, index) => index === 0 || matches[index - 1]!.score >= match.score)
}

function isDocumentSearchMatchDto(value: unknown): value is DocumentSearchMatchDto {
  if (!isRecord(value)) return false

  return (
    isUuid(value.chunk_id) &&
    isFiniteNumber(value.score) &&
    hasText(value.page_content) &&
    isNonNegativeInteger(value.chunk_index) &&
    isNonNegativeInteger(value.chunk_count) &&
    value.chunk_count > value.chunk_index
  )
}
