import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_REQUEST_TIMEOUT_MS, requestFile, requestJson, setUnauthorizedHandler } from './client'

describe('requestJson', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    setUnauthorizedHandler(null)
  })

  it('normalizes the backend error contract without exposing request data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: 'qdrant_timeout',
            detail: 'Vector database query timed out.',
            retryable: true,
          }),
          { status: 504, headers: { 'content-type': 'application/json' } },
        ),
      ),
    )

    const promise = requestJson('/vector-search', {
      method: 'POST',
      body: JSON.stringify({ query: 'private query' }),
    })

    await expect(promise).rejects.toMatchObject({
      status: 504,
      code: 'qdrant_timeout',
      retryable: true,
    })
  })

  it('marks network failures as retryable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))

    await expect(requestJson('/vector-search', { method: 'POST' })).rejects.toEqual(
      expect.objectContaining({
        code: 'network_error',
        retryable: true,
        status: 0,
      }),
    )
  })

  it('accepts an empty successful array', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('[]', {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(requestJson('/vector-search', { method: 'POST' })).resolves.toEqual([])
  })

  it('turns a hanging request into a retryable timeout', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener(
              'abort',
              () => reject(new DOMException('aborted', 'AbortError')),
              { once: true },
            )
          }),
      ),
    )

    const assertion = expect(
      requestJson('/vector-search', { method: 'POST' }),
    ).rejects.toMatchObject({
      code: 'request_timeout',
      retryable: true,
    })

    await vi.advanceTimersByTimeAsync(API_REQUEST_TIMEOUT_MS)
    await assertion
  })

  it('notifies the application when an authenticated API request returns 401', async () => {
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Unauthorized' }), {
          status: 401,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    await expect(requestJson('/document-search', { method: 'POST' })).rejects.toMatchObject({
      status: 401,
      code: 'authentication_required',
    })
    expect(unauthorized).toHaveBeenCalledOnce()
  })

  it('原件下载同样处理登录失效，不把错误 JSON 当作文件返回', async () => {
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(Response.json({ detail: '需要登录。' }, { status: 401 })),
    )
    await expect(
      requestFile('/document-management/candidates/example/original', { method: 'GET' }),
    ).rejects.toMatchObject({ status: 401, code: 'authentication_required' })
    expect(unauthorized).toHaveBeenCalledOnce()
  })

  it('下载保留原始字节和历史原件文件名，不借用当前文档的文件名', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(new Uint8Array([255, 10]), {
          headers: {
            'Content-Disposition':
              "attachment; filename*=UTF-8''" + encodeURIComponent('原始手册.md'),
          },
        }),
      ),
    )
    const file = await requestFile('/document-management/candidates/example/original', {
      method: 'GET',
    })
    expect(file.filename).toBe('原始手册.md')
    expect(new Uint8Array(await file.blob.arrayBuffer())).toEqual(new Uint8Array([255, 10]))
  })
})
