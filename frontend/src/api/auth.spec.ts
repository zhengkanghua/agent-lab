import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchCurrentUser, loginWithPassword, logoutCurrentUser } from './auth'

describe('auth API', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('submits password login as form data and accepts an empty 204 response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await loginWithPassword('reader@example.com', 'private-password')

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/auth/login')
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('same-origin')
    expect(init.body).toBeInstanceOf(URLSearchParams)
    expect((init.body as URLSearchParams).get('username')).toBe('reader@example.com')
    expect((init.body as URLSearchParams).get('password')).toBe('private-password')
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
  })

  it('loads the current user and logs out through cookie-auth endpoints', async () => {
    const currentUser = {
      id: '10000000-0000-4000-8000-000000000001',
      email: 'reader@example.com',
      is_active: true,
      is_superuser: false,
      is_verified: true,
      is_environment_admin: false,
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(currentUser), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchCurrentUser()).resolves.toEqual(currentUser)
    await expect(logoutCurrentUser()).resolves.toBeUndefined()
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/auth/me')
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/auth/logout')
  })
})
