import type { components } from './generated/openapi'
import { ApiError, requestJson, requestVoid } from './client'
import { hasText, isRecord, isSha256, isUuid } from './json-guards'

export type FileDocumentDto = components['schemas']['FileDocumentResponse']
export type FileDocumentListDto = components['schemas']['FileDocumentListResponse']

export async function listFileDocuments(
  offset: number,
  signal?: AbortSignal,
): Promise<FileDocumentListDto> {
  const value = await requestJson<unknown>(`/file-documents?offset=${offset}&limit=25`, {
    method: 'GET',
    signal,
  })
  if (
    !isRecord(value) ||
    !Array.isArray(value.items) ||
    !value.items.every(isFileDocument) ||
    typeof value.has_more !== 'boolean' ||
    typeof value.max_file_bytes !== 'number' ||
    value.max_file_bytes < 1
  ) {
    throw invalidResponse()
  }
  return value as unknown as FileDocumentListDto
}

export async function saveFileDocument(
  file: File,
  knowledgeBaseId: string,
  target?: FileDocumentDto,
): Promise<FileDocumentDto> {
  const body = new FormData()
  body.set('file', file)
  if (target) {
    body.set('revision', String(target.revision))
    body.set('management_revision', String(target.management_revision))
  } else body.set('knowledge_base_id', knowledgeBaseId)
  const value = await requestJson<unknown>(
    target ? `/file-documents/${target.document_id}/file` : '/file-documents',
    {
      method: target ? 'PUT' : 'POST',
      body,
    },
  )
  if (!isFileDocument(value)) throw invalidResponse()
  return value
}

export async function deleteFileDocument(item: FileDocumentDto): Promise<void> {
  await requestVoid(
    `/file-documents/${item.document_id}?revision=${item.revision}&management_revision=${item.management_revision}`,
    {
      method: 'DELETE',
    },
  )
}

function isFileDocument(value: unknown): value is FileDocumentDto {
  return (
    isRecord(value) &&
    isUuid(value.document_id) &&
    isUuid(value.knowledge_base_id) &&
    hasText(value.knowledge_base_name) &&
    typeof value.knowledge_base_active === 'boolean' &&
    hasText(value.upload_filename) &&
    hasText(value.title) &&
    hasText(value.mime_type) &&
    (value.content_hash === null || isSha256(value.content_hash)) &&
    Number.isInteger(value.revision) &&
    Number(value.revision) > 0 &&
    Number.isInteger(value.management_revision) &&
    Number(value.management_revision) > 0 &&
    (value.processing_id === null || isUuid(value.processing_id)) &&
    (value.candidate_revision === null ||
      (Number.isInteger(value.candidate_revision) && Number(value.candidate_revision) > 0)) &&
    (value.candidate_state === null || typeof value.candidate_state === 'string') &&
    (value.candidate_error === null || typeof value.candidate_error === 'string') &&
    (value.current_version_id === null || isUuid(value.current_version_id)) &&
    typeof value.usage_status === 'string' &&
    typeof value.updated_at === 'string' &&
    Number.isFinite(Date.parse(value.updated_at)) &&
    ['pending', 'processing', 'indexed', 'failed'].includes(String(value.processing_status)) &&
    (value.processing_error === null || typeof value.processing_error === 'string') &&
    typeof value.deletion_pending === 'boolean' &&
    (value.deletion_error === null || typeof value.deletion_error === 'string')
  )
}

function invalidResponse(): ApiError {
  return new ApiError({
    message: '文件服务返回的数据无效，请刷新列表核对操作结果。',
    code: 'response_invalid',
  })
}
