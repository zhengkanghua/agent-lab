import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import {
  hasText,
  isHttpUrl,
  isNullableString,
  isPositiveInteger,
  isRecord,
  isSha256,
  isStringArray,
  isUuid,
} from './json-guards'

export type DocumentDetailDto = components['schemas']['DocumentDetailResponse']

export interface FetchDocumentOptions {
  documentId: string
  signal?: AbortSignal
}

export async function fetchDocument({
  documentId,
  signal,
}: FetchDocumentOptions): Promise<DocumentDetailDto> {
  const response = await requestJson<unknown>(`/documents/${encodeURIComponent(documentId)}`, {
    method: 'GET',
    signal,
  })

  if (!isDocumentDetailDto(response)) {
    throw new ApiError({
      message: 'The document service returned an invalid article.',
      code: 'response_invalid',
    })
  }

  if (response.document_id.toLowerCase() !== documentId.toLowerCase()) {
    throw new ApiError({
      message: 'The document service returned a different article.',
      code: 'response_invalid',
    })
  }

  return response
}

function isDocumentDetailDto(value: unknown): value is DocumentDetailDto {
  if (!isRecord(value)) return false

  return (
    isUuid(value.document_id) &&
    isSha256(value.content_hash) &&
    isPositiveInteger(value.revision) &&
    hasText(value.title) &&
    isHttpUrl(value.url) &&
    hasText(value.source_name) &&
    isNullableString(value.published_at) &&
    isStringArray(value.authors) &&
    isStringArray(value.labels) &&
    hasText(value.content_text)
  )
}
