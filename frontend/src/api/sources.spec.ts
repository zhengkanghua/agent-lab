import { afterEach, describe, expect, it, vi } from 'vitest'
import { bindSource, listSources } from './sources'
import { boundNewsSource, unboundSource } from './sources.fixture'

afterEach(() => vi.unstubAllGlobals())

describe('source API', () => {
  it('lists sources and sends explicit binding payloads', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json([boundNewsSource, unboundSource]))
      .mockResolvedValueOnce(
        Response.json({
          ...unboundSource,
          knowledge_base_id: boundNewsSource.knowledge_base_id,
          knowledge_base_key: boundNewsSource.knowledge_base_key,
        }),
      )
      .mockResolvedValueOnce(Response.json(unboundSource))
    vi.stubGlobal('fetch', fetchMock)

    expect(await listSources()).toEqual([boundNewsSource, unboundSource])
    const rebound = await bindSource(unboundSource.id, boundNewsSource.knowledge_base_id)
    expect(rebound.knowledge_base_key).toBe('news')
    const unbound = await bindSource(unboundSource.id, null)
    expect(unbound.knowledge_base_id).toBeNull()

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/sources')
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      credentials: 'same-origin',
      method: 'GET',
    })
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`/api/sources/${unboundSource.id}/knowledge-base`)
    expect(JSON.parse(fetchMock.mock.calls[1]?.[1].body)).toEqual({
      knowledge_base_id: boundNewsSource.knowledge_base_id,
    })
    expect(JSON.parse(fetchMock.mock.calls[2]?.[1].body)).toEqual({ knowledge_base_id: null })
  })

  it.each([
    null,
    {},
    [{ ...boundNewsSource, id: 'bad-id' }],
    [{ ...boundNewsSource, external_id: '' }],
    [{ ...boundNewsSource, knowledge_base_id: 'not-a-uuid' }],
    [{ ...boundNewsSource, sync_checkpoint_updated_at: 'not-a-date' }],
  ])('rejects malformed list data', async (body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body)))
    await expect(listSources()).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it('preserves backend conflict and missing errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        Response.json(
          {
            code: 'source_binding_conflict',
            detail: '来源已有 Document，不能修改 KnowledgeBase 绑定。',
            retryable: false,
          },
          { status: 409 },
        ),
      ),
    )
    await expect(bindSource(boundNewsSource.id, null)).rejects.toMatchObject({
      status: 409,
      code: 'source_binding_conflict',
      retryable: false,
    })
  })
})
