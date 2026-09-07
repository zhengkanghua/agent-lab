import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import { hasText, isRecord, isUuid } from './json-guards'

export type KnowledgeBaseDto = components['schemas']['KnowledgeBaseResponse']
export type KnowledgeBaseCreateRequest = components['schemas']['KnowledgeBaseCreateRequest']
export type KnowledgeBaseUpdateRequest = components['schemas']['KnowledgeBaseUpdateRequest']

export async function listKnowledgeBases(
  includeInactive = false,
  signal?: AbortSignal,
): Promise<KnowledgeBaseDto[]> {
  const query = new URLSearchParams({ include_inactive: String(includeInactive) })
  const response = await requestJson<unknown>(`/knowledge-bases?${query}`, {
    method: 'GET',
    signal,
  })
  if (!Array.isArray(response) || !response.every(isKnowledgeBase)) {
    throw invalidResponse()
  }
  return response
}

export async function createKnowledgeBase(
  body: KnowledgeBaseCreateRequest,
): Promise<KnowledgeBaseDto> {
  return requestKnowledgeBase('/knowledge-bases', { method: 'POST', body: JSON.stringify(body) })
}

export async function updateKnowledgeBase(
  id: string,
  body: KnowledgeBaseUpdateRequest,
): Promise<KnowledgeBaseDto> {
  return requestKnowledgeBase(`/knowledge-bases/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

async function requestKnowledgeBase(path: string, init: RequestInit): Promise<KnowledgeBaseDto> {
  const response = await requestJson<unknown>(path, init)
  if (!isKnowledgeBase(response)) throw invalidResponse()
  return response
}

function isKnowledgeBase(value: unknown): value is KnowledgeBaseDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    hasText(value.key) &&
    hasText(value.name) &&
    (value.description === null || typeof value.description === 'string') &&
    typeof value.is_active === 'boolean' &&
    typeof value.created_at === 'string' &&
    !Number.isNaN(Date.parse(value.created_at)) &&
    typeof value.updated_at === 'string' &&
    !Number.isNaN(Date.parse(value.updated_at))
  )
}

function invalidResponse(): ApiError {
  return new ApiError({
    message: 'The knowledge base service returned an invalid response.',
    code: 'response_invalid',
  })
}
