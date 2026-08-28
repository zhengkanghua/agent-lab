import { afterEach, describe, expect, it, vi } from 'vitest'
import { searchVector } from './vector-search'

const result = {
  point_id: '10000000-0000-4000-8000-000000000001',
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.8123,
  page_content: '央行维持政策利率不变。',
  document_id: '20000000-0000-4000-8000-000000000001',
  content_hash: 'a'.repeat(64),
  chunk_index: 0,
  chunk_count: 2,
  title: '政策利率维持不变',
  url: 'https://example.com/news',
  published_at: null,
  source_updated_at: null,
  document_type: 'article',
  source_id: '30000000-0000-4000-8000-000000000001',
  source_provider: 'test',
  source_name: '测试来源',
  source_external_id: 'feed/1',
  document_external_id: 'article/1',
  authors: [],
  labels: ['宏观'],
  previous_chunk_id: null,
  next_chunk_id: null,
  index_schema_version: 'v1',
  embedding_model: 'bge-m3:567m',
}

describe('searchVector', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sends the typed search payload and preserves backend result order', async () => {
    const second = {
      ...result,
      point_id: '10000000-0000-4000-8000-000000000002',
      chunk_id: '10000000-0000-4000-8000-000000000002',
      score: 0.7,
    }
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([result, second]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const response = await searchVector({ query: '央行利率', topK: 5 })

    expect(response.map((item) => item.chunk_id)).toEqual([result.chunk_id, second.chunk_id])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/vector-search',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ query: '央行利率', top_k: 5 }),
      }),
    )
  })

  it('rejects malformed result items before they reach the view model', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([{ ...result, labels: null }]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(searchVector({ query: '宏观', topK: 10 })).rejects.toMatchObject({
      code: 'response_invalid',
    })
  })

  // 后端这两个字段是 UUID 类型；非 UUID 说明契约已经漂移，不能放进展示模型。
  it.each([['chunk_id'], ['document_id']])('rejects a non-UUID %s', async (field) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([{ ...result, [field]: 'not-a-uuid' }]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(searchVector({ query: '宏观', topK: 10 })).rejects.toMatchObject({
      code: 'response_invalid',
    })
  })

  it('rejects non-HTTP source links', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([{ ...result, url: 'javascript:alert(1)' }]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(searchVector({ query: '宏观', topK: 10 })).rejects.toMatchObject({
      code: 'response_invalid',
    })
  })
})
