import type { components } from './generated/openapi'
import { requestJson, requestVoid } from './client'

export type AuthUserDto = components['schemas']['AuthUserResponse']

export function fetchCurrentUser(): Promise<AuthUserDto> {
  return requestJson<AuthUserDto>('/auth/me', { method: 'GET' }, { notifyUnauthorized: false })
}

export function loginWithPassword(email: string, password: string): Promise<void> {
  const body = new URLSearchParams({
    username: email,
    password,
  })
  return requestVoid(
    '/auth/login',
    {
      method: 'POST',
      body,
    },
    { notifyUnauthorized: false },
  )
}

export function logoutCurrentUser(): Promise<void> {
  return requestVoid('/auth/logout', { method: 'POST' }, { notifyUnauthorized: false })
}
