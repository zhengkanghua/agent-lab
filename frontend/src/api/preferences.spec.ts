import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchPreferences, savePreferences } from './preferences'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('preferences API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('读取走账号自助前缀，并把 null 提示词转成空串', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ system_prompt: null, document_limit: 20, matches_per_document: 5 }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchPreferences()).resolves.toEqual({
      systemPrompt: '',
      documentLimit: 20,
      matchesPerDocument: 5,
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/me/preferences',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    )
  })

  it('保存把空白提示词发成 null，其余字段原样', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ system_prompt: null, document_limit: 5, matches_per_document: 3 }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await savePreferences({ systemPrompt: '   ', documentLimit: 5, matchesPerDocument: 3 })

    expect(JSON.parse(fetchMock.mock.calls[0]![1].body as string)).toEqual({
      system_prompt: null,
      document_limit: 5,
      matches_per_document: 3,
    })
    expect((fetchMock.mock.calls[0]![1] as RequestInit).method).toBe('PUT')
  })

  it('非空提示词原样提交，不做 trim', async () => {
    // 提示词里的换行和缩进是内容的一部分，trim 掉会改变模型看到的东西。
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        system_prompt: '第一行\n第二行',
        document_limit: 10,
        matches_per_document: 3,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await savePreferences({
      systemPrompt: '第一行\n第二行',
      documentLimit: 10,
      matchesPerDocument: 3,
    })

    expect(JSON.parse(fetchMock.mock.calls[0]![1].body as string).system_prompt).toBe(
      '第一行\n第二行',
    )
  })

  it('越界的数量参数按契约边界归一', async () => {
    // 后端已经校验过，但这是前端拿到值的最后一道关：一个越界值会让设置页的下拉框
    // 选不中任何一项，界面显示成空白。
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ system_prompt: null, document_limit: 9999, matches_per_document: 0 }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchPreferences()).resolves.toEqual({
      systemPrompt: '',
      documentLimit: 100,
      matchesPerDocument: 1,
    })
  })

  it('形状不对时抛 response_invalid，而不是把半个结构交给界面', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ system_prompt: 42, document_limit: '很多', matches_per_document: 3 }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchPreferences()).rejects.toMatchObject({ code: 'response_invalid' })
  })

  it('读失败不触发全局登出', async () => {
    // 偏好读不到是「用默认值」，不是「登录失效」。触发全局登出会把一次网络抖动
    // 变成把用户踢出登录。
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ code: 'unknown_error', detail: 'nope', retryable: true }, 401),
      )
    vi.stubGlobal('fetch', fetchMock)
    const unauthorized = vi.fn()
    const { setUnauthorizedHandler } = await import('./client')
    setUnauthorizedHandler(unauthorized)

    await expect(fetchPreferences({ notifyUnauthorized: false })).rejects.toMatchObject({
      status: 401,
    })
    expect(unauthorized).not.toHaveBeenCalled()
    setUnauthorizedHandler(null)
  })
})
