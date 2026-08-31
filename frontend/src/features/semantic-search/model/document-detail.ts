import type { DocumentDetailDto } from '@/api/documents'

export interface NewsDocumentDetail {
  documentId: string
  contentHash: string
  revision: number
  title: string
  url: string
  sourceName: string
  publishedAt: string | null
  authors: string[]
  labels: string[]
  contentText: string
}

export function toNewsDocumentDetail(dto: DocumentDetailDto): NewsDocumentDetail {
  return {
    documentId: dto.document_id,
    contentHash: dto.content_hash,
    revision: dto.revision,
    title: dto.title,
    url: dto.url,
    sourceName: dto.source_name,
    publishedAt: dto.published_at ?? null,
    authors: [...dto.authors],
    labels: [...dto.labels],
    contentText: dto.content_text,
  }
}
