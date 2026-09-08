import { afterEach, describe, expect, it, vi } from 'vitest'
import { searchDocuments } from './document-search'
import { scopedSearchResponse } from './document-search.fixture'

const match = {
  chunk_id: '10000000-0000-4000-8000-000000000001',
  score: 0.91,
  page_content: '最高分片段',
  chunk_index: 0,
  chunk_count: 2,
}

const result = {
  mime_type: 'text/plain',
  document_id: '20000000-0000-4000-8000-000000000001',
  knowledge_base_id: '10000000-0000-4000-8000-000000000010',
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

  it.each([
    { url: null },
    { source_name: null },
    { source_name: undefined },
    { url: null, source_name: null },
  ])('accepts mixed results with optional metadata %j', async (metadata) => {
    const body = scopedSearchResponse([result, { ...result, ...metadata }])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
    await expect(
      searchDocuments({ query: '资料', documentLimit: 10, matchesPerDocument: 3 }),
    ).resolves.toEqual(JSON.parse(JSON.stringify(body)))
  })

  it.each([
    { url: 'javascript:alert(1)' },
    { url: undefined },
    { source_name: ' ' },
    { source_name: 123 },
    { document_id: 'invalid' },
    { knowledge_base_id: undefined },
    { mime_type: undefined },
    { mime_type: ' ' },
    { title: undefined },
  ])('rejects invalid or missing metadata %j', async (metadata) => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify(scopedSearchResponse([{ ...result, ...metadata }]))),
        ),
    )
    await expect(
      searchDocuments({ query: '资料', documentLimit: 10, matchesPerDocument: 3 }),
    ).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it('sends document and per-document limits to the grouped endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(scopedSearchResponse([result])), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      searchDocuments({ query: '央行利率', documentLimit: 7, matchesPerDocument: 4 }),
    ).resolves.toEqual(scopedSearchResponse([result]))
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/document-search',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          query: '央行利率',
          document_limit: 7,
          matches_per_document: 4,
          scope: { mode: 'all' },
        }),
      }),
    )
  })

  it('rejects malformed grouped matches before rendering', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify(
            scopedSearchResponse([{ ...result, best_match: { ...match, score: 'bad' } }]),
          ),
          {
            status: 200,
            headers: { 'content-type': 'application/json' },
          },
        ),
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
        new Response(JSON.stringify(scopedSearchResponse([body])), {
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
