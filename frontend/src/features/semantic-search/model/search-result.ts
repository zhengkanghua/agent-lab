import type { DocumentSearchMatchDto, DocumentSearchResultDto } from '@/api/document-search'
import type { ReadableResult } from '@/shared/model/readable-result'

// ReadableResult（打开全文所需的文档身份）与 formatPublishedAt 归 shared/model/readable-result.ts：
// 它们被阅读器、结果卡、Agent 引用三处共用，留在这个 feature 里会让另外两个反向依赖它。
export type { ReadableResult }

export interface DocumentMatch {
  id: string
  excerpt: string
  score: number
  chunkIndex: number
  chunkCount: number
}

export interface DocumentResult extends ReadableResult {
  chunkCount: number
  bestScore: number
  bestMatch: DocumentMatch
  additionalMatches: DocumentMatch[]
}

export function toDocumentResult(dto: DocumentSearchResultDto): DocumentResult {
  return {
    documentId: dto.document_id,
    knowledgeBaseId: dto.knowledge_base_id,
    uploadFilename: dto.upload_filename,
    contentHash: dto.content_hash,
    title: dto.title,
    url: dto.url,
    sourceName: dto.source_name ?? null,
    publishedAt: dto.published_at ?? null,
    labels: [...dto.labels],
    authors: [...dto.authors],
    chunkCount: dto.chunk_count,
    bestScore: dto.best_score,
    bestMatch: toDocumentMatch(dto.best_match),
    additionalMatches: (dto.additional_matches ?? []).map(toDocumentMatch),
  }
}

// 不去重也不重排：document_id 唯一性和「最高分降序 + document_id 升序」的顺序都由后端保证。
// Qdrant 按 index_instance_id 分组（同一 Document 可能有多份正文实例），组内重复由
// qdrant/search.py 的 search_groups 抛 QdrantSearchResponseError；跨实例的「同一 Document
// 只出一条」由 knowledge/visibility.py 核验当前正式指向后判定，不满足则重查。
// 排序键是 (-score, str(document_id))。前端再算一遍只会在两边规则漂移时产生分歧。
export function toDocumentResults(dtos: DocumentSearchResultDto[]): DocumentResult[] {
  return dtos.map(toDocumentResult)
}

function toDocumentMatch(dto: DocumentSearchMatchDto): DocumentMatch {
  return {
    id: dto.chunk_id,
    excerpt: dto.page_content,
    score: dto.score,
    chunkIndex: dto.chunk_index,
    chunkCount: dto.chunk_count,
  }
}

const scoreFormatter = new Intl.NumberFormat('zh-CN', {
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
})

export function formatScore(value: number): string {
  return scoreFormatter.format(value)
}

/** 片段折叠阈值：超过这个字符数的正文先截断，由卡片自己提供展开开关。 */
export const COLLAPSED_CHARACTERS = 520

/** 作者行：去空白、去重、最多列两位，更多时以「等」收尾。 */
export function formatAuthorLine(authors: string[]): string {
  const unique = [...new Set(authors.map((author) => author.trim()).filter(Boolean))]
  if (unique.length <= 2) return unique.join('、')
  return `${unique.slice(0, 2).join('、')} 等`
}

export function isExcerptLong(excerpt: string): boolean {
  return excerpt.length > COLLAPSED_CHARACTERS
}

/** expanded 为真或正文本就不长时原样返回，否则截断到阈值并补省略号。 */
export function collapseExcerpt(excerpt: string, expanded: boolean): string {
  if (expanded || !isExcerptLong(excerpt)) return excerpt
  return `${excerpt.slice(0, COLLAPSED_CHARACTERS).trimEnd()}…`
}
