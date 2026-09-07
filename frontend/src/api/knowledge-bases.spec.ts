import { afterEach, describe, expect, it, vi } from 'vitest'
import { createKnowledgeBase, listKnowledgeBases, updateKnowledgeBase } from './knowledge-bases'
import { newsKnowledgeBase } from './knowledge-bases.fixture'

afterEach(() => vi.unstubAllGlobals())

describe('knowledge base API', () => {
  it('uses the same-origin contract and sends only explicit updates', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json([newsKnowledgeBase]))
      .mockResolvedValueOnce(Response.json(newsKnowledgeBase))
      .mockResolvedValueOnce(Response.json({ ...newsKnowledgeBase, description: null }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await listKnowledgeBases(true)).toEqual([newsKnowledgeBase])
    await createKnowledgeBase({ key: 'news', name: '新闻', is_active: true })
    await updateKnowledgeBase(newsKnowledgeBase.id, { description: null })
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/knowledge-bases?include_inactive=true')
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      credentials: 'same-origin',
      method: 'GET',
    })
    expect(JSON.parse(fetchMock.mock.calls[1]?.[1].body)).toEqual({
      key: 'news',
      name: '新闻',
      is_active: true,
    })
    expect(fetchMock.mock.calls[2]?.[0]).toBe(`/api/knowledge-bases/${newsKnowledgeBase.id}`)
    expect(JSON.parse(fetchMock.mock.calls[2]?.[1].body)).toEqual({ description: null })
  })

  it.each([
    null,
    {},
    [{ ...newsKnowledgeBase, id: 'bad-id' }],
    [{ ...newsKnowledgeBase, name: '' }],
    [{ ...newsKnowledgeBase, is_active: 'false' }],
    [{ ...newsKnowledgeBase, description: undefined }],
    [{ ...newsKnowledgeBase, updated_at: 'not-a-date' }],
  ])('rejects malformed list data', async (body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body)))
    await expect(listKnowledgeBases()).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it('preserves conflicts from the backend', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        Response.json(
          {
            code: 'knowledge_base_key_conflict',
            detail: '知识库稳定键已被使用。',
            retryable: false,
          },
          { status: 409 },
        ),
      ),
    )
    await expect(
      createKnowledgeBase({ key: 'news', name: '重复', is_active: true }),
    ).rejects.toMatchObject({
      status: 409,
      code: 'knowledge_base_key_conflict',
      retryable: false,
    })
  })
})
