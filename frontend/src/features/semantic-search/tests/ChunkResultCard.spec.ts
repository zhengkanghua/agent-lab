import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ChunkResultCard from '../components/ChunkResultCard.vue'

const result = {
  id: '10000000-0000-4000-8000-000000000001',
  documentId: '20000000-0000-4000-8000-000000000001',
  contentHash: 'a'.repeat(64),
  title: '政策利率维持不变',
  excerpt: '央行维持政策利率不变。',
  url: 'https://example.com/news',
  sourceName: '测试来源',
  publishedAt: null,
  labels: ['宏观'],
  authors: [],
  score: 0.8123,
  chunkIndex: 1,
  chunkCount: 3,
  embeddingModel: 'bge-m3:567m',
}

describe('ChunkResultCard', () => {
  it('shows the raw Chunk identity and opens the shared full-text reader', async () => {
    const wrapper = mount(ChunkResultCard, { props: { result, rank: 0 } })

    expect(wrapper.text()).toContain('片段 2 / 3')
    expect(wrapper.text()).toContain('0.812')
    expect(wrapper.text()).toContain(result.excerpt)
    await wrapper.get<HTMLButtonElement>('.read-button').trigger('click')
    expect(wrapper.emitted('read')?.[0]?.[0]).toMatchObject({
      documentId: result.documentId,
      contentHash: result.contentHash,
    })
  })
})
