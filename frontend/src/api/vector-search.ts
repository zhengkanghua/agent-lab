import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import { isSha256 } from './json-guards'

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
    typeof value.score === 'number' &&
    Number.isFinite(value.score) &&
    typeof value.chunk_index === 'number' &&
    Number.isInteger(value.chunk_index) &&
    typeof value.chunk_count === 'number' &&
    Number.isInteger(value.chunk_count) &&
    value.chunk_index >= 0 &&
    value.chunk_count > value.chunk_index &&
    isStringArray(value.labels) &&
    isStringArray(value.authors) &&
    (value.published_at === undefined ||
      value.published_at === null ||
      typeof value.published_at === 'string')
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isHttpUrl(value: unknown): value is string {
  if (typeof value !== 'string') return false

  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}
