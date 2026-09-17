import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createUser,
  deleteUser,
  listUsers,
  resetUserPassword,
  revokeUserSessions,
  updateUser,
} from './user-admin'

const user = {
  id: '10000000-0000-4000-8000-000000000001',
  email: 'reader@example.com',
  is_active: true,
  is_superuser: false,
  is_verified: true,
  is_environment_admin: false,
  created_at: '2026-08-18T00:00:00Z',
  updated_at: '2026-08-18T00:00:00Z',
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('user admin API', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('lists validated accounts through the superuser endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([user]))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listUsers()).resolves.toEqual([user])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/users',
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    )
  })

  it('maps create, update, password reset, and session revocation bodies exactly', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(user, 201))
      .mockResolvedValueOnce(jsonResponse({ ...user, is_active: false }))
      .mockResolvedValueOnce(jsonResponse(user))
      .mockResolvedValueOnce(jsonResponse({ revoked_sessions: 3 }))
    vi.stubGlobal('fetch', fetchMock)

    await createUser({
      email: 'reader@example.com',
      password: 'private-create-password',
      isSuperuser: false,
    })
    await updateUser({ userId: user.id, isActive: false })
    await resetUserPassword({ userId: user.id, password: 'private-reset-password' })
    await expect(revokeUserSessions(user.id)).resolves.toEqual({ revoked_sessions: 3 })

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      '/api/admin/users',
      `/api/admin/users/${user.id}`,
      `/api/admin/users/${user.id}/password`,
      `/api/admin/users/${user.id}/sessions`,
    ])
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).body).toBe(
      JSON.stringify({
        email: 'reader@example.com',
        password: 'private-create-password',
        is_superuser: false,
      }),
    )
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).body).toBe(
      JSON.stringify({ is_active: false }),
    )
    expect((fetchMock.mock.calls[2]?.[1] as RequestInit).body).toBe(
      JSON.stringify({ password: 'private-reset-password' }),
    )
    expect(fetchMock.mock.calls[3]?.[1]).toEqual(expect.objectContaining({ method: 'DELETE' }))
  })

  it('deletes an account through a 204 with no body', async () => {
    // 204 是空体。这条用例真正防的是「把删账号接到 requestJson 上」——那条路径会把空响应
    // 当成 response_invalid 抛出来，删除明明成功却报错，而且只在真跑接口时才发现。
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteUser(user.id)).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/admin/users/${user.id}`,
      expect.objectContaining({ method: 'DELETE', credentials: 'same-origin' }),
    )
  })

  it('surfaces a stable code when deleting is refused', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          code: 'last_superuser_protected',
          detail: '最后一个活跃超级管理员不能被删除。',
          retryable: false,
        },
        409,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(deleteUser(user.id)).rejects.toMatchObject({
      code: 'last_superuser_protected',
      status: 409,
    })
  })

  it('rejects malformed users and revocation counts before rendering', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([{ ...user, is_environment_admin: 'false' }]))
      .mockResolvedValueOnce(jsonResponse({ revoked_sessions: -1 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(listUsers()).rejects.toMatchObject({ code: 'response_invalid' })
    await expect(revokeUserSessions(user.id)).rejects.toMatchObject({
      code: 'response_invalid',
    })
  })
})
