import { requestVoid } from './client'

export interface PasswordChangeRequest {
  current_password: string
  new_password: string
}

export function changeOwnPassword(request: PasswordChangeRequest): Promise<void> {
  return requestVoid(
    '/auth/me/password',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    },
    { notifyUnauthorized: false },
  )
}
