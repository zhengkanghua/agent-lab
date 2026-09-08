import type { DocumentSearchResultDto, ScopedDocumentSearchResponse } from './document-search'
import { newsKnowledgeBase } from './knowledge-bases.fixture'

export function scopedSearchResponse<T = DocumentSearchResultDto>(
  results: T[],
): {
  scope: ScopedDocumentSearchResponse['scope']
  results: T[]
} {
  return { scope: { mode: 'all', knowledge_bases: [newsKnowledgeBase] }, results }
}
