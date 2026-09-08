import type { components } from './generated/openapi'
import { hasText, isRecord, isSha256, isUuid } from './json-guards'

export type DocumentEvidence = components['schemas']['DocumentEvidence']

/** 只接受服务端的结构化证据，模型正文中的 UUID、标题或链接不能建立引用。 */
export function isDocumentEvidence(value: unknown): value is DocumentEvidence {
  return (
    isRecord(value) &&
    typeof value.citation_id === 'string' &&
    /^E[0-9a-f]{12}$/.test(value.citation_id) &&
    isUuid(value.document_id) &&
    isUuid(value.knowledge_base_id) &&
    hasText(value.knowledge_base_name) &&
    hasText(value.title) &&
    isSha256(value.content_hash) &&
    hasText(value.excerpt) &&
    (value.kind === 'match' || value.kind === 'document') &&
    (value.truncated === undefined || typeof value.truncated === 'boolean') &&
    ['source_name', 'upload_filename', 'url', 'published_at'].every(
      (key) => value[key] == null || typeof value[key] === 'string',
    )
  )
}

export function isCitationList(value: unknown): value is DocumentEvidence[] {
  return Array.isArray(value) && value.every(isDocumentEvidence)
}

export function isInvalidCitationList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}
