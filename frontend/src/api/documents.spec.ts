import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchDocument } from './documents'

const detail = {
  document_id: '20000000-0000-4000-8000-000000000001',
  content_hash: 'b'.repeat(64),
  revision: 3,
  title: '政策利率维持不变',
  url: 'https://example.com/news',
  source_name: '测试来源',
  published_at: null,
  authors: [],
  labels: ['宏观'],
  content_text: '完整正文。',
}

describe('fetchDocument', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reads the full text only from the document detail endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(detail), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchDocument({ documentId: detail.document_id })).resolves.toEqual(detail)
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/documents/${detail.document_id}`,
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('rejects an incomplete full-text contract', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ...detail, content_text: '' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(fetchDocument({ documentId: detail.document_id })).rejects.toMatchObject({
      code: 'response_invalid',
    })
  })

  it('rejects a valid-looking response for a different document', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ...detail,
            document_id: '20000000-0000-4000-8000-000000000002',
          }),
          {
            status: 200,
            headers: { 'content-type': 'application/json' },
          },
        ),
      ),
    )

    await expect(fetchDocument({ documentId: detail.document_id })).rejects.toMatchObject({
      code: 'response_invalid',
    })
  })
})
