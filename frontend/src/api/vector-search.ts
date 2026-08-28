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
} from './json-guards'

export type VectorSearchRequest = components['schemas']['VectorSearchRequest']
export type VectorSearchResultDto = components['schemas']['VectorSearchResult']
export type VectorSearchErrorDto = components['schemas']['VectorSearchErrorResponse']

export interface SearchVectorOptions {
  query: string
  topK: number
  signal?: AbortSignal
}

export async function searchVector({
  query,
  topK,
  signal,
}: SearchVectorOptions): Promise<VectorSearchResultDto[]> {
  const payload: VectorSearchRequest = {
    query,
    top_k: topK,
  }

  const response = await requestJson<unknown>('/vector-search', {
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

  if (!response.every(isVectorSearchResultDto)) {
    throw new ApiError({
      message: 'The search service returned an invalid result item.',
      code: 'response_invalid',
    })
  }

  return response
}

function isVectorSearchResultDto(value: unknown): value is VectorSearchResultDto {
  if (!isRecord(value)) return false

  return (
    hasText(value.chunk_id) &&
    hasText(value.document_id) &&
    isSha256(value.content_hash) &&
    hasText(value.title) &&
    hasText(value.page_content) &&
    hasText(value.source_name) &&
    hasText(value.embedding_model) &&
    isHttpUrl(value.url) &&
    isFiniteNumber(value.score) &&
    isNonNegativeInteger(value.chunk_index) &&
    isNonNegativeInteger(value.chunk_count) &&
    value.chunk_count > value.chunk_index &&
    isStringArray(value.labels) &&
    isStringArray(value.authors) &&
    isNullableString(value.published_at)
  )
}
