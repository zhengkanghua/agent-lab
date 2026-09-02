import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SearchRecordTurn from '../components/SearchRecordTurn.vue'
import type { SearchRecord } from '../model/search-record'
import type { NewsDocumentResult } from '../model/search-result'

function makeRecord(overrides: Partial<SearchRecord> = {}): SearchRecord {
  return {
    id: 1,
    query: '央行利率',
    documentLimit: 10,
    matchesPerDocument: 3,
    status: 'success',
    results: [],
    error: null,
    ...overrides,
  }
}

const documentResult: NewsDocumentResult = {
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

describe('SearchRecordTurn', () => {
  it('renders expanded results for a success record', () => {
    const record = makeRecord({
      status: 'success',
      results: [documentResult],
    })
    const wrapper = mount(SearchRecordTurn, {
      props: { record, isLatest: true, expanded: true },
    })

    expect(wrapper.findAll('.result-card')).toHaveLength(1)
    expect(wrapper.text()).toContain('命中 1 篇')
  })

  it('renders the empty state', () => {
    const wrapper = mount(SearchRecordTurn, {
      props: { record: makeRecord({ status: 'empty' }), isLatest: true, expanded: true },
    })

    expect(wrapper.text()).toContain('没有命中')
    expect(wrapper.text()).toContain('换一种表达再试')
  })

  it('renders a retry button only for a retryable error', () => {
    const retryable = makeRecord({
      status: 'error',
      error: {
        title: '服务暂时不可用',
        description: '请稍后再试。',
        retryable: true,
      },
    })
    const wrapper = mount(SearchRecordTurn, {
      props: { record: retryable, isLatest: true, expanded: true },
    })

    const retry = wrapper.find('.retry-button')
    expect(retry.exists()).toBe(true)
    expect(wrapper.text()).toContain('本次未完成')
  })

  it('omits retry when the error is not retryable', () => {
    const wrapper = mount(SearchRecordTurn, {
      props: {
        record: makeRecord({
          status: 'error',
          error: { title: '检索失败', description: '请重试。', retryable: false },
        }),
        isLatest: true,
        expanded: true,
      },
    })

    expect(wrapper.find('.retry-button').exists()).toBe(false)
  })

  it('collapses body when not expanded and emits toggle on the title row', async () => {
    const record = makeRecord({ status: 'success', results: [documentResult] })
    const wrapper = mount(SearchRecordTurn, {
      props: { record, isLatest: false, expanded: false },
    })

    // 折叠态不渲染 body。
    expect(wrapper.findAll('.result-card')).toHaveLength(0)

    await wrapper.get('.record-toggle').trigger('click')
    expect(wrapper.emitted('toggle')).toHaveLength(1)
  })

  it('latest record has no collapse toggle (static header)', () => {
    const wrapper = mount(SearchRecordTurn, {
      props: { record: makeRecord(), isLatest: true, expanded: true },
    })

    expect(wrapper.find('.record-toggle--static').exists()).toBe(true)
    expect(wrapper.find('.record-chevron').exists()).toBe(false)
  })
})
