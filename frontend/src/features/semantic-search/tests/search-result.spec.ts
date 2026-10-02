// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { formatScore, toDocumentResult } from '../model/search-result'

const firstMatch = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.91,
  page_content: '最高分片段',
  chunk_index: 1,
  chunk_count: 4,
}

const dto = {
  mime_type: 'text/plain',
  document_id: '20000000-0000-4000-8000-000000000001',
  knowledge_base_id: '10000000-0000-4000-8000-000000000010',
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
    const result = toDocumentResult(dto)

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

describe('formatScore', () => {
  it('显示原始数值，但不补齐小数位', () => {
    // 不补零：0.91 不该写成 0.910（同一次检索里几张卡顶着同一个三位数像写死的）。
    expect(formatScore(0.91)).toBe('0.91')
    expect(formatScore(0.9)).toBe('0.9')
    // 更高精度的原始值照原样给到三位；再多位时按三位显示（0.90051 不取边界值，
    // 免得断言挂在浮点表示上）。
    expect(formatScore(0.905)).toBe('0.905')
    expect(formatScore(0.90051)).toBe('0.901')
    // 不换算成百分比。
    expect(formatScore(1)).toBe('1')
  })
})
