import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import {
  hasText,
  isFiniteNumber,
  isHttpUrl,
  isNonNegativeInteger,
  isNullableString,
  isRecord,
  isSha256,
  isStringArray,
  isUuid,
} from './json-guards'

export type DocumentSearchRequest = components['schemas']['DocumentSearchRequest']
export type DocumentSearchResultDto = components['schemas']['DocumentSearchResult']
export type DocumentSearchMatchDto = components['schemas']['DocumentSearchMatch']

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
    !isNonNegativeInteger(value.chunk_count) ||
    value.chunk_count === 0 ||
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
