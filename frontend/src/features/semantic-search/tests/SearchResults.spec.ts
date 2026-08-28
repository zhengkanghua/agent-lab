import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SearchResults from '../components/SearchResults.vue'

const documentResult = {
  documentId: '20000000-0000-4000-8000-000000000001',
  contentHash: 'a'.repeat(64),
  title: '一篇新闻',
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
  // 两种模式都原样透传后端顺序与条数：document 分组唯一性由 Qdrant grouped query
  // 的 group_by 保证，chunk 模式则按契约允许同一 document 的多个 Chunk 分别出现。
  // 前端不再去重——曾有一版按 documentId 去重并据此计数，命中时会让页面显示的篇数
  // 少于后端实际返回，且 UUID 序列化恒为小写，那种大小写重复本就构造不出来。
  it('两种模式都按后端返回的条数与顺序渲染', async () => {
    const wrapper = mount(SearchResults, {
      props: {
        mode: 'document',
        status: 'success',
        results: [
          documentResult,
          {
            ...documentResult,
            documentId: '20000000-0000-4000-8000-000000000002',
            title: '另一篇新闻',
          },
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

    expect(wrapper.findAll('.result-card')).toHaveLength(2)
    expect(wrapper.text()).toContain('找到 2 篇相关新闻')
    expect(wrapper.text()).toContain('另一篇新闻')

    await wrapper.setProps({ mode: 'chunk' })
    expect(wrapper.findAll('.chunk-card')).toHaveLength(2)
    expect(wrapper.text()).toContain('找到 2 个相关片段')
    expect(wrapper.text()).toContain('同一新闻的第二个原始 Chunk')
  })
})
