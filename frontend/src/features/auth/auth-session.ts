import { readonly, ref, type Ref } from 'vue'
import { ApiError } from '@/api/client'
import {
  fetchCurrentUser,
  loginWithPassword,
  logoutCurrentUser,
  type AuthUserDto,
} from '@/api/auth'

export type AuthSessionStatus = 'unknown' | 'loading' | 'authenticated' | 'anonymous' | 'error'

export interface AuthSession {
  status: Readonly<Ref<AuthSessionStatus>>
  user: Readonly<Ref<AuthUserDto | null>>
  error: Readonly<Ref<ApiError | null>>
  initialize: (force?: boolean) => Promise<void>
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  expire: () => void
  clear: () => void
}

export function createAuthSession(): AuthSession {
  const status = ref<AuthSessionStatus>('unknown')
  const user = ref<AuthUserDto | null>(null)
  const error = ref<ApiError | null>(null)
  let initialization: Promise<void> | null = null

  function setAnonymous(): void {
    user.value = null
    error.value = null
    status.value = 'anonymous'
  }

  async function loadCurrentUser(): Promise<void> {
    status.value = 'loading'
    error.value = null
    try {
      user.value = await fetchCurrentUser()
      status.value = 'authenticated'
    } catch (cause) {
      user.value = null
      if (cause instanceof ApiError && cause.status === 401) {
        setAnonymous()
        return
      }
      error.value =
        cause instanceof ApiError
          ? cause
          : new ApiError({
              message: 'Unable to check the current session.',
              code: 'session_check_failed',
              cause,
            })
      status.value = 'error'
    }
  }

  async function initialize(force = false): Promise<void> {
    if (!force && status.value !== 'unknown' && status.value !== 'error') return
    if (!force && initialization) return initialization

    initialization = loadCurrentUser().finally(() => {
      initialization = null
    })
    return initialization
  }

  async function login(email: string, password: string): Promise<void> {
    await loginWithPassword(email, password)
    await loadCurrentUser()
    if (status.value !== 'authenticated') {
      throw (
        error.value ??
        new ApiError({
          message: 'The session could not be established.',
          code: 'session_check_failed',
        })
      )
    }
  }

  async function logout(): Promise<void> {
    try {
      await logoutCurrentUser()
      setAnonymous()
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        setAnonymous()
        return
      }
      throw cause
    }
  }

  function expire(): void {
    setAnonymous()
  }

  function clear(): void {
    initialization = null
    user.value = null
    error.value = null
    status.value = 'unknown'
  }

  return {
    status: readonly(status),
    user: readonly(user),
    error: readonly(error),
    initialize,
    login,
    logout,
    expire,
    clear,
  }
}

export const authSession = createAuthSession()
