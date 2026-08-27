import { afterEach, describe, expect, it, vi } from 'vitest'
import { searchDocuments } from './document-search'

const match = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.91,
  page_content: '最高分片段',
  chunk_index: 0,
  chunk_count: 2,
}

const result = {
  document_id: '20000000-0000-4000-8000-000000000001',
  content_hash: 'a'.repeat(64),
  title: '政策利率维持不变',
  url: 'https://example.com/news',
  source_name: '测试来源',
  published_at: null,
  authors: [],
  labels: ['宏观'],
  chunk_count: 2,
  best_score: 0.91,
  best_match: match,
  additional_matches: [],
}

describe('searchDocuments', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends document and per-document limits to the grouped endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([result]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      searchDocuments({ query: '央行利率', documentLimit: 7, matchesPerDocument: 4 }),
    ).resolves.toEqual([result])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/document-search',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          query: '央行利率',
          document_limit: 7,
          matches_per_document: 4,
        }),
      }),
    )
  })

  it('rejects malformed grouped matches before rendering', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([{ ...result, best_match: { ...match, score: 'bad' } }]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(
      searchDocuments({ query: '宏观', documentLimit: 10, matchesPerDocument: 3 }),
    ).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it.each([
    {
      name: 'best score differs from the best match',
      body: { ...result, best_score: 0.5 },
    },
    {
      name: 'additional matches are not sorted',
      body: {
        ...result,
        additional_matches: [
          { ...match, chunk_id: '10000000-0000-4000-8000-000000000002', score: 0.8 },
          { ...match, chunk_id: '10000000-0000-4000-8000-000000000003', score: 0.85 },
        ],
      },
    },
    {
      name: 'a match declares a different chunk count',
      body: {
        ...result,
        additional_matches: [
          {
            ...match,
            chunk_id: '10000000-0000-4000-8000-000000000002',
            score: 0.8,
            chunk_count: 3,
          },
        ],
      },
    },
    {
      name: 'a chunk id is duplicated',
      body: { ...result, additional_matches: [{ ...match, score: 0.8 }] },
    },
  ])('rejects grouped contract drift when $name', async ({ body }) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([body]), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(
      searchDocuments({ query: '宏观', documentLimit: 10, matchesPerDocument: 3 }),
    ).rejects.toMatchObject({ code: 'response_invalid' })
  })
})
