import { afterEach, describe, expect, it, vi } from 'vitest'
import { createLlmProvider, listLlmProviders, updateLlmProvider } from './llm-providers'
import { localProvider, openaiProvider } from './llm-providers.fixture'

afterEach(() => vi.unstubAllGlobals())

describe('llm provider API', () => {
  it('reads the directory from the same-origin contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json([openaiProvider, localProvider]))
    vi.stubGlobal('fetch', fetchMock)

    expect(await listLlmProviders()).toEqual([openaiProvider, localProvider])
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/llm-providers')
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      credentials: 'same-origin',
      method: 'GET',
    })
  })

  it('sends the credential only on the write it belongs to', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json(openaiProvider))
      .mockResolvedValueOnce(Response.json(openaiProvider))
    vi.stubGlobal('fetch', fetchMock)

    await createLlmProvider({
      name: '主中转站',
      provider: 'openai_compatible',
      base_url: 'https://api.example.com/v1',
      credential: 'sk-plain',
      enabled: true,
    })
    // 编辑时留空 = 不改凭据：请求体里干脆不带这个字段。
    await updateLlmProvider(openaiProvider.id, { name: '主中转站（改）' })

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/llm-providers')
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toEqual({
      name: '主中转站',
      provider: 'openai_compatible',
      base_url: 'https://api.example.com/v1',
      credential: 'sk-plain',
      enabled: true,
    })
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`/api/llm-providers/${openaiProvider.id}`)
    expect(fetchMock.mock.calls[1]?.[1].method).toBe('PATCH')
    expect(JSON.parse(fetchMock.mock.calls[1]?.[1].body)).toEqual({ name: '主中转站（改）' })
  })

  it.each([
    null,
    {},
    [{ ...openaiProvider, id: 'bad-id' }],
    [{ ...openaiProvider, name: '' }],
    // 接入类型只认契约上的两个值：多出来的一个不该被当成可用渠道渲染。
    [{ ...openaiProvider, provider: 'anthropic' }],
    [{ ...openaiProvider, base_url: 'not-a-url' }],
    [{ ...openaiProvider, enabled: 'true' }],
    [{ ...openaiProvider, credential_configured: undefined }],
    [{ ...openaiProvider, updated_at: 'not-a-date' }],
  ])('rejects malformed directory data', async (body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body)))
    await expect(listLlmProviders()).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it('preserves the backend failure contract', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        Response.json(
          {
            code: 'llm_provider_credential_required',
            detail: '该接入类型必须配置凭据，请在本次保存里补上凭据。',
            retryable: false,
          },
          { status: 422 },
        ),
      ),
    )
    await expect(
      createLlmProvider({
        name: '主中转站',
        provider: 'openai_compatible',
        base_url: 'https://api.example.com/v1',
        enabled: true,
      }),
    ).rejects.toMatchObject({
      status: 422,
      code: 'llm_provider_credential_required',
      retryable: false,
    })
  })
})
