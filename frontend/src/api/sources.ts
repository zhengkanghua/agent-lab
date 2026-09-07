import type { components } from './generated/openapi'
import { ApiError, requestJson } from './client'
import { hasText, isRecord, isUuid } from './json-guards'

export type SourceDto = components['schemas']['SourceResponse']

export async function listSources(signal?: AbortSignal): Promise<SourceDto[]> {
  const response = await requestJson<unknown>('/sources', { method: 'GET', signal })
  if (!Array.isArray(response) || !response.every(isSource)) {
    throw invalidResponse()
  }
  return response
}

export async function bindSource(id: string, knowledgeBaseId: string | null): Promise<SourceDto> {
  const response = await requestJson<unknown>(`/sources/${encodeURIComponent(id)}/knowledge-base`, {
    method: 'PATCH',
    body: JSON.stringify({ knowledge_base_id: knowledgeBaseId }),
  })
  if (!isSource(response)) throw invalidResponse()
  return response
}

function isSource(value: unknown): value is SourceDto {
  return (
    isRecord(value) &&
    isUuid(value.id) &&
    hasText(value.provider) &&
    hasText(value.external_id) &&
    hasText(value.name) &&
    (value.feed_url === null || typeof value.feed_url === 'string') &&
    (value.home_url === null || typeof value.home_url === 'string') &&
    (value.knowledge_base_id === null || isUuid(value.knowledge_base_id)) &&
    (value.knowledge_base_key === null || hasText(value.knowledge_base_key)) &&
    (value.sync_checkpoint === null || hasText(value.sync_checkpoint)) &&
    (value.sync_checkpoint_updated_at === null ||
      (typeof value.sync_checkpoint_updated_at === 'string' &&
        !Number.isNaN(Date.parse(value.sync_checkpoint_updated_at))))
  )
}

function invalidResponse(): ApiError {
  return new ApiError({
    message: 'The source service returned an invalid response.',
    code: 'response_invalid',
  })
}
