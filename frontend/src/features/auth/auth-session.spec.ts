import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import * as authApi from '@/api/auth'
import { createAuthSession } from './auth-session'

const currentUser = {
  id: '10000000-0000-4000-8000-000000000001',
  email: 'reader@example.com',
  is_active: true,
  is_superuser: false,
  is_verified: true,
  is_environment_admin: false,
}

describe('auth session', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('treats a 401 session check as an anonymous visitor', async () => {
    vi.spyOn(authApi, 'fetchCurrentUser').mockRejectedValue(
      new ApiError({ message: 'Unauthorized', status: 401, code: 'authentication_required' }),
    )
    const session = createAuthSession()

    await session.initialize()

    expect(session.status.value).toBe('anonymous')
    expect(session.user.value).toBeNull()
    expect(session.error.value).toBeNull()
  })

  it('establishes the session only after login and /auth/me both succeed', async () => {
    const login = vi.spyOn(authApi, 'loginWithPassword').mockResolvedValue()
    const me = vi.spyOn(authApi, 'fetchCurrentUser').mockResolvedValue(currentUser)
    const session = createAuthSession()

    await session.login('reader@example.com', 'private-password')

    expect(login).toHaveBeenCalledWith('reader@example.com', 'private-password')
    expect(me).toHaveBeenCalledOnce()
    expect(session.status.value).toBe('authenticated')
    expect(session.user.value).toEqual(currentUser)
  })

  it('keeps an authenticated session visible when logout cannot reach the backend', async () => {
    vi.spyOn(authApi, 'fetchCurrentUser').mockResolvedValue(currentUser)
    vi.spyOn(authApi, 'logoutCurrentUser').mockRejectedValue(
      new ApiError({ message: 'offline', code: 'network_error', retryable: true }),
    )
    const session = createAuthSession()
    await session.initialize()

    await expect(session.logout()).rejects.toMatchObject({ code: 'network_error' })
    expect(session.status.value).toBe('authenticated')
    expect(session.user.value).toEqual(currentUser)
  })
})
