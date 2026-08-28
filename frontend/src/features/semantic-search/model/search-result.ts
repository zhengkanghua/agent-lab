import type { DocumentSearchMatchDto, DocumentSearchResultDto } from '../../../api/document-search'
import type { VectorSearchResultDto } from '../../../api/vector-search'

export type SearchMode = 'document' | 'chunk'

/** 搜索结果打开全文时所需的稳定文档身份和回退元数据。 */
export interface NewsReadableResult {
  documentId: string
  contentHash: string
  title: string
  url: string
  sourceName: string
  publishedAt: string | null
  labels: string[]
  authors: string[]
}

export interface NewsDocumentMatch {
  id: string
  excerpt: string
  score: number
  chunkIndex: number
  chunkCount: number
}

export interface NewsDocumentResult extends NewsReadableResult {
  chunkCount: number
  bestScore: number
  bestMatch: NewsDocumentMatch
  additionalMatches: NewsDocumentMatch[]
}

export function toNewsDocumentResult(dto: DocumentSearchResultDto): NewsDocumentResult {
  return {
    documentId: dto.document_id,
    contentHash: dto.content_hash,
    title: dto.title,
    url: dto.url,
    sourceName: dto.source_name,
    publishedAt: dto.published_at ?? null,
    labels: [...dto.labels],
    authors: [...dto.authors],
    chunkCount: dto.chunk_count,
    bestScore: dto.best_score,
    bestMatch: toNewsDocumentMatch(dto.best_match),
    additionalMatches: (dto.additional_matches ?? []).map(toNewsDocumentMatch),
  }
}

// 不去重也不重排：document_id 唯一性和「最高分降序 + document_id 升序」的顺序都由
// Qdrant grouped query 在后端保证，重复 document_id 会被后端直接拒绝。前端再算一遍
// 只会在两边规则漂移时产生分歧。
export function toNewsDocumentResults(dtos: DocumentSearchResultDto[]): NewsDocumentResult[] {
  return dtos.map(toNewsDocumentResult)
}

function toNewsDocumentMatch(dto: DocumentSearchMatchDto): NewsDocumentMatch {
  return {
    id: dto.chunk_id,
    excerpt: dto.page_content,
    score: dto.score,
    chunkIndex: dto.chunk_index,
    chunkCount: dto.chunk_count,
  }
}

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  timeZone: 'Asia/Shanghai',
})

const scoreFormatter = new Intl.NumberFormat('zh-CN', {
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
})

export function formatPublishedAt(value: string | null): string {
  if (!value) {
    return '时间未提供'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '时间未提供'
  }
  return dateFormatter.format(date)
}

export function formatScore(value: number): string {
  return scoreFormatter.format(value)
}

// 保留原始 Chunk 展示模型导出，供仍直接消费 /vector-search 的独立调用方使用。
export interface NewsChunkResult extends NewsReadableResult {
  id: string
  excerpt: string
  score: number
  chunkIndex: number
  chunkCount: number
  embeddingModel: string
}

export function toNewsChunkResult(dto: VectorSearchResultDto): NewsChunkResult {
  return {
    id: dto.chunk_id,
    documentId: dto.document_id,
    contentHash: dto.content_hash,
    title: dto.title,
    excerpt: dto.page_content,
    url: dto.url,
    sourceName: dto.source_name,
    publishedAt: dto.published_at ?? null,
    labels: [...dto.labels],
    authors: [...dto.authors],
    score: dto.score,
    chunkIndex: dto.chunk_index,
    chunkCount: dto.chunk_count,
    embeddingModel: dto.embedding_model,
  }
}

export function toNewsChunkResults(dtos: VectorSearchResultDto[]): NewsChunkResult[] {
  return dtos.map(toNewsChunkResult)
}
