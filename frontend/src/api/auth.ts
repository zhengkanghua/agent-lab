import type { components } from './generated/openapi'
import { requestJson, requestVoid } from './client'

// 本地扩展：后端已添加 created_at，但 openapi.ts 被其他会话占用未重新生成
export type AuthUserDto = components['schemas']['AuthUserResponse'] & {
  created_at: string
}

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
