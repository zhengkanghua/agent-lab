import type { DocumentDetailDto } from '@/api/documents'

export interface NewsDocumentDetail {
  documentId: string
  knowledgeBaseId: string
  knowledgeBaseName?: string | null
  uploadFilename?: string | null
  mimeType?: string
  contentHash: string
  revision: number
  title: string
  url: string | null
  sourceName: string | null
  publishedAt: string | null
  authors: string[]
  labels: string[]
  contentText: string
}

export function toNewsDocumentDetail(dto: DocumentDetailDto): NewsDocumentDetail {
  return {
    documentId: dto.document_id,
    knowledgeBaseId: dto.knowledge_base_id,
    knowledgeBaseName: dto.knowledge_base_name,
    uploadFilename: dto.upload_filename,
    mimeType: dto.mime_type,
    contentHash: dto.content_hash,
    revision: dto.revision,
    title: dto.title,
    url: dto.url,
    sourceName: dto.source_name ?? null,
    publishedAt: dto.published_at ?? null,
    authors: [...dto.authors],
    labels: [...dto.labels],
    contentText: dto.content_text,
  }
}
