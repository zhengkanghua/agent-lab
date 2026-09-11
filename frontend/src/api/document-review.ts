import type { components } from './generated/openapi'
import { ApiError, requestFile, requestJson, requestVoid } from './client'
import {
  hasText,
  isNonNegativeInteger,
  isPositiveInteger,
  isRecord,
  isStringArray,
  isUuid,
} from './json-guards'

export type ManagedDocumentDto = components['schemas']['ManagedDocument']
export type ReviewDetailDto = components['schemas']['ReviewDetail']
export type ProcessingSummaryDto = components['schemas']['ProcessingSummary']
export type ProcessingDetailDto = components['schemas']['ProcessingDetail']
export type ProcessingReceiptDto = components['schemas']['ProcessingReceipt']
export type DocumentPreviewDto = components['schemas']['DocumentPreview']
export type VersionSummaryDto = components['schemas']['VersionSummary']
export type VersionDetailDto = components['schemas']['VersionDetail']
export type ReviewDecisionDto = components['schemas']['ReviewDecision']
export type ReviewTarget = components['schemas']['ReviewTargetRequest']

export interface ReviewFilters {
  knowledgeBaseId: string
  sourceKind: '' | 'file' | 'freshrss'
  state: string
}

const root = '/document-management'
const nullableText = (value: unknown) => value === null || typeof value === 'string'
const nullableId = (value: unknown) => value === null || isUuid(value)

function invalidResponse(): ApiError {
  return new ApiError({
    code: 'response_invalid',
    message: '文档管理服务返回的数据无效，请刷新核对操作结果。',
  })
}

function isManagedDocument(value: unknown): value is ManagedDocumentDto {
  return (
    isRecord(value) &&
    isUuid(value.document_id) &&
    isUuid(value.knowledge_base_id) &&
    typeof value.knowledge_base_name === 'string' &&
    typeof value.knowledge_base_active === 'boolean' &&
    typeof value.title === 'string' &&
    ['file', 'freshrss'].includes(String(value.source_kind)) &&
    hasText(value.usage_status) &&
    isPositiveInteger(value.revision) &&
    isPositiveInteger(value.management_revision) &&
    nullableId(value.current_version_id) &&
    nullableId(value.latest_processing_id) &&
    nullableId(value.draft_processing_id) &&
    nullableText(value.upload_filename) &&
    nullableText(value.processing_state) &&
    nullableText(value.error_code) &&
    typeof value.deletion_pending === 'boolean' &&
    typeof value.updated_at === 'string'
  )
}

function isProcessing(value: unknown): value is ProcessingSummaryDto {
  return (
    isRecord(value) &&
    isUuid(value.processing_id) &&
    isUuid(value.document_id) &&
    hasText(value.source_kind) &&
    hasText(value.state) &&
    typeof value.title === 'string' &&
    isPositiveInteger(value.candidate_revision) &&
    typeof value.requires_review === 'boolean' &&
    typeof value.source_stored === 'boolean' &&
    isStringArray(value.issue_codes) &&
    nullableText(value.preview_fingerprint) &&
    nullableText(value.error_code) &&
    nullableText(value.source_sha256) &&
    typeof value.created_at === 'string' &&
    typeof value.updated_at === 'string'
  )
}

/** 验证界面实际读取的结构；算法质量和采用资格仍由后端决定。 */
function isPreview(value: unknown): value is DocumentPreviewDto {
  if (!isRecord(value) || !isRecord(value.document) || !isRecord(value.chunk_result)) return false
  const document = value.document
  const result = value.chunk_result
  return (
    typeof document.title === 'string' &&
    typeof document.body === 'string' &&
    ['markdown', 'plain'].includes(String(document.text_format)) &&
    Array.isArray(document.outline) &&
    document.outline.every(
      (entry) =>
        isRecord(entry) &&
        hasText(entry.id) &&
        typeof entry.title === 'string' &&
        isPositiveInteger(entry.level) &&
        nullableText(entry.parent_id),
    ) &&
    Array.isArray(document.blocks) &&
    document.blocks.every(
      (block) =>
        isRecord(block) &&
        hasText(block.id) &&
        hasText(block.kind) &&
        typeof block.text === 'string' &&
        isStringArray(block.heading_ids),
    ) &&
    isRecord(result.specification) &&
    isPositiveInteger(result.specification.max_tokens) &&
    Array.isArray(result.chunks) &&
    result.chunks.every(
      (chunk) =>
        isRecord(chunk) &&
        isNonNegativeInteger(chunk.sequence) &&
        typeof chunk.text === 'string' &&
        typeof chunk.embedding_text === 'string' &&
        isNonNegativeInteger(chunk.token_count) &&
        isStringArray(chunk.block_ids) &&
        isStringArray(chunk.heading_ids) &&
        isStringArray(chunk.headings),
    )
  )
}

function isProcessingDetail(value: unknown): value is ProcessingDetailDto {
  return (
    isRecord(value) &&
    nullableText(value.draft_text) &&
    ['markdown', 'plain'].includes(String(value.text_format)) &&
    (value.preview === null || isPreview(value.preview)) &&
    isProcessing(value)
  )
}

function isVersion(value: unknown): value is VersionSummaryDto {
  return (
    isRecord(value) &&
    isUuid(value.version_id) &&
    isUuid(value.processing_id) &&
    isPositiveInteger(value.revision) &&
    typeof value.title === 'string' &&
    hasText(value.content_hash) &&
    typeof value.created_at === 'string'
  )
}

async function page<T>(path: string, guard: (value: unknown) => value is T, signal?: AbortSignal) {
  const value = await requestJson<unknown>(path, { method: 'GET', signal })
  if (
    !isRecord(value) ||
    !Array.isArray(value.items) ||
    !value.items.every(guard) ||
    typeof value.has_more !== 'boolean'
  )
    throw invalidResponse()
  return { items: value.items as T[], has_more: value.has_more }
}

export function listManagedDocuments(filters: ReviewFilters, offset: number, signal?: AbortSignal) {
  const query = new URLSearchParams({ offset: String(offset), limit: '25' })
  if (filters.knowledgeBaseId) query.set('knowledge_base_id', filters.knowledgeBaseId)
  if (filters.sourceKind) query.set('source_kind', filters.sourceKind)
  if (filters.state) query.set('state', filters.state)
  return page(root + '?' + query, isManagedDocument, signal)
}

export async function getDocumentReview(
  documentId: string,
  processingId?: string,
  signal?: AbortSignal,
) {
  const value = await requestJson<unknown>(
    root + '/' + documentId + (processingId ? '?processing_id=' + processingId : ''),
    { method: 'GET', signal },
  )
  if (
    !isRecord(value) ||
    !isManagedDocument(value.document) ||
    !isProcessingDetail(value.candidate) ||
    value.document.document_id !== documentId ||
    value.candidate.document_id !== documentId ||
    (value.latest_source !== null && !isProcessing(value.latest_source)) ||
    (value.draft !== null && !isProcessing(value.draft))
  )
    throw invalidResponse()
  return value as unknown as ReviewDetailDto
}

async function command(
  path: string,
  body: unknown,
  method = 'POST',
): Promise<ProcessingReceiptDto> {
  const value = await requestJson<unknown>(root + path, { method, body: JSON.stringify(body) })
  if (
    !isRecord(value) ||
    !isUuid(value.document_id) ||
    !isUuid(value.processing_id) ||
    !isPositiveInteger(value.candidate_revision) ||
    !hasText(value.state)
  )
    throw invalidResponse()
  return value as unknown as ProcessingReceiptDto
}

export function startDocumentReview(
  documentId: string,
  managementRevision: number,
  processingId?: string,
) {
  return command('/' + documentId + '/draft', {
    management_revision: managementRevision,
    processing_id: processingId,
  })
}

export function useLatestDocumentSource(documentId: string, managementRevision: number) {
  return command('/' + documentId + '/use-latest-source', {
    management_revision: managementRevision,
  })
}

export function saveDocumentDraft(
  processingId: string,
  body: components['schemas']['SaveDraftRequest'],
) {
  return command('/candidates/' + processingId + '/draft', body, 'PUT')
}

export function previewDocumentDraft(processingId: string, body: ReviewTarget) {
  return command('/candidates/' + processingId + '/preview', body)
}

export function adoptDocumentCandidate(
  processingId: string,
  body: components['schemas']['AdoptRequest'],
) {
  return command('/candidates/' + processingId + '/adopt', body)
}

export function rejectDocumentCandidate(
  processingId: string,
  body: components['schemas']['ReviewDecisionRequest'],
) {
  return command('/candidates/' + processingId + '/reject', body)
}

export function retryDocumentCandidate(processingId: string, body: ReviewTarget) {
  return command('/candidates/' + processingId + '/retry', body)
}

export function deleteManagedDocument(document: ManagedDocumentDto) {
  return requestVoid(
    root +
      '/' +
      document.document_id +
      '?revision=' +
      document.revision +
      '&management_revision=' +
      document.management_revision,
    { method: 'DELETE' },
  )
}

export function getDocumentOriginal(processingId: string, signal?: AbortSignal) {
  return requestFile(root + '/candidates/' + processingId + '/original', { method: 'GET', signal })
}

export function listDocumentCandidates(documentId: string, offset: number, signal?: AbortSignal) {
  return page(
    root + '/' + documentId + '/candidates?offset=' + offset + '&limit=25',
    isProcessing,
    signal,
  )
}

export function listDocumentVersions(documentId: string, offset: number, signal?: AbortSignal) {
  return page(
    root + '/' + documentId + '/versions?offset=' + offset + '&limit=25',
    isVersion,
    signal,
  )
}

export async function getDocumentVersion(
  documentId: string,
  versionId: string,
  signal?: AbortSignal,
) {
  const value = await requestJson<unknown>(root + '/' + documentId + '/versions/' + versionId, {
    method: 'GET',
    signal,
  })
  if (
    !isRecord(value) ||
    !isPreview(value.preview) ||
    !isRecord(value.metadata) ||
    !isRecord(value.processing_spec) ||
    !isVersion(value)
  )
    throw invalidResponse()
  return value as unknown as VersionDetailDto
}

export function listDocumentDecisions(documentId: string, offset: number, signal?: AbortSignal) {
  return page(
    root + '/' + documentId + '/reviews?offset=' + offset + '&limit=25',
    (value): value is ReviewDecisionDto =>
      isRecord(value) &&
      isUuid(value.review_id) &&
      isUuid(value.processing_id) &&
      isPositiveInteger(value.candidate_revision) &&
      hasText(value.decision) &&
      hasText(value.decision_source) &&
      nullableText(value.conclusion) &&
      typeof value.created_at === 'string' &&
      isRecord(value.content_snapshot),
    signal,
  )
}
