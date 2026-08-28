import { describe, expect, it } from 'vitest'
import { toNewsDocumentResult } from '../model/search-result'

const firstMatch = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.91,
  page_content: '最高分片段',
  chunk_index: 1,
  chunk_count: 4,
}

const dto = {
  document_id: '20000000-0000-4000-8000-000000000001',
  content_hash: 'a'.repeat(64),
  title: '长标题新闻',
  url: 'https://example.com/news',
  source_name: '测试来源',
  published_at: null,
  authors: ['作者甲'],
  labels: ['宏观', '利率'],
  chunk_count: 4,
  best_score: 0.91,
  best_match: firstMatch,
  additional_matches: [
    {
      chunk_id: '10000000-0000-4000-8000-000000000002',
      score: 0.82,
      page_content: '另一个片段',
      chunk_index: 3,
      chunk_count: 4,
    },
  ],
}

describe('document search view model', () => {
  it('maps the grouped DTO without treating score as a percentage', () => {
    const result = toNewsDocumentResult(dto)

    expect(result).toMatchObject({
      documentId: dto.document_id,
      contentHash: dto.content_hash,
      publishedAt: null,
      bestScore: 0.91,
      bestMatch: {
        id: firstMatch.chunk_id,
        chunkIndex: 1,
        chunkCount: 4,
      },
    })
    expect(result.additionalMatches).toHaveLength(1)
  })
})
