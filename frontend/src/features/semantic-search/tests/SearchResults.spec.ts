import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SearchResults from '../components/SearchResults.vue'

const documentResult = {
  documentId: '20000000-0000-4000-8000-000000000001',
  contentHash: 'a'.repeat(64),
  title: '同一新闻',
  url: 'https://example.com/news',
  sourceName: '测试来源',
  publishedAt: null,
  labels: [],
  authors: [],
  chunkCount: 2,
  bestScore: 0.91,
  bestMatch: {
    id: '10000000-0000-4000-8000-000000000001',
    excerpt: '新闻分组片段',
    score: 0.91,
    chunkIndex: 0,
    chunkCount: 2,
  },
  additionalMatches: [],
}

const chunkResult = {
  id: documentResult.bestMatch.id,
  documentId: documentResult.documentId,
  contentHash: documentResult.contentHash,
  title: documentResult.title,
  excerpt: documentResult.bestMatch.excerpt,
  url: documentResult.url,
  sourceName: documentResult.sourceName,
  publishedAt: null,
  labels: [],
  authors: [],
  score: 0.91,
  chunkIndex: 0,
  chunkCount: 2,
  embeddingModel: 'bge-m3:567m',
}

describe('SearchResults modes', () => {
  it('deduplicates document groups but preserves duplicate document ids in Chunk mode', async () => {
    const wrapper = mount(SearchResults, {
      props: {
        mode: 'document',
        status: 'success',
        results: [
          documentResult,
          { ...documentResult, documentId: documentResult.documentId.toUpperCase() },
        ],
        chunkResults: [
          chunkResult,
          {
            ...chunkResult,
            id: '10000000-0000-4000-8000-000000000002',
            excerpt: '同一新闻的第二个原始 Chunk',
            chunkIndex: 1,
          },
        ],
        error: null,
        lastQuery: '央行利率',
      },
    })

    expect(wrapper.findAll('.result-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('找到 1 篇相关新闻')

    await wrapper.setProps({ mode: 'chunk' })
    expect(wrapper.findAll('.chunk-card')).toHaveLength(2)
    expect(wrapper.text()).toContain('找到 2 个相关片段')
    expect(wrapper.text()).toContain('同一新闻的第二个原始 Chunk')
  })
})
