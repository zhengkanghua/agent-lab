import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SearchResultCard from '../components/SearchResultCard.vue'

const result = {
  documentId: '20000000-0000-4000-8000-000000000001',
  contentHash: 'a'.repeat(64),
  title: '政策利率维持不变',
  url: 'https://example.com/news',
  sourceName: '测试来源',
  publishedAt: '2026-08-14T08:00:00Z',
  labels: ['宏观', '利率'],
  authors: [],
  chunkCount: 2,
  bestScore: 0.9123,
  bestMatch: {
    id: '10000000-0000-4000-8000-000000000001',
    excerpt: '第一段。'.repeat(200),
    score: 0.9123,
    chunkIndex: 0,
    chunkCount: 2,
  },
  additionalMatches: [
    {
      id: '10000000-0000-4000-8000-000000000002',
      excerpt: '第二段。',
      score: 0.8,
      chunkIndex: 1,
      chunkCount: 2,
    },
  ],
}

describe('SearchResultCard', () => {
  it('renders source, title, score and labels', () => {
    const wrapper = mount(SearchResultCard, {
      props: { result, rank: 0 },
    })

    expect(wrapper.text()).toContain('测试来源')
    expect(wrapper.text()).toContain('政策利率维持不变')
    expect(wrapper.text()).toContain('0.912')
    expect(wrapper.text()).toContain('宏观')
    expect(wrapper.get('.read-button').text()).toContain('阅读全文')
    expect(wrapper.find('a').attributes('href')).toBe(result.url)
  })

  it('allows a long best match to be expanded', async () => {
    const wrapper = mount(SearchResultCard, {
      props: { result, rank: 0 },
    })
    const expandButton = wrapper.get('.best-expand')

    expect(expandButton.text()).toContain('展开最佳片段')
    await expandButton.trigger('click')
    expect(expandButton.text()).toContain('收起最佳片段')
  })

  it('expands and collapses the other related matches in the same result', async () => {
    const wrapper = mount(SearchResultCard, {
      props: { result, rank: 0 },
    })
    const toggle = wrapper.get('.related-toggle')

    expect(wrapper.find('.related-matches').exists()).toBe(false)
    expect(toggle.text()).toContain('查看另外 1 个相关片段')
    await toggle.trigger('click')
    expect(wrapper.get('.related-matches').text()).toContain('第二段。')
    expect(toggle.text()).toContain('收起相关片段')
    await toggle.trigger('click')
    expect(wrapper.find('.related-matches').exists()).toBe(false)
  })

  it('does not show a related-match toggle when only the best match exists', () => {
    const wrapper = mount(SearchResultCard, {
      props: {
        result: { ...result, additionalMatches: [] },
        rank: 0,
      },
    })

    expect(wrapper.find('.related-toggle').exists()).toBe(false)
  })

  it('emits the selected document and trigger when opening full text', async () => {
    const wrapper = mount(SearchResultCard, {
      props: { result, rank: 0 },
    })

    await wrapper.get('.read-button').trigger('click')

    expect(wrapper.emitted('read')?.[0]?.[0]).toMatchObject({
      documentId: result.documentId,
    })
    expect(wrapper.emitted('read')?.[0]?.[1]).toBeTruthy()
  })

  it('keeps long titles, missing time, and many labels readable', () => {
    const longTitle = '很长的新闻标题'.repeat(24)
    const labels = Array.from({ length: 18 }, (_, index) => `标签-${index + 1}`)
    const wrapper = mount(SearchResultCard, {
      props: {
        result: { ...result, title: longTitle, publishedAt: null, labels },
        rank: 0,
      },
    })

    expect(wrapper.get('.result-title').text()).toBe(longTitle)
    expect(wrapper.text()).toContain('时间未提供')
    expect(wrapper.findAll('.label-list li')).toHaveLength(labels.length)
  })
})
