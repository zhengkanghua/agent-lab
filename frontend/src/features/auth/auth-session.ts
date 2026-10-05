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
  /**
   * 会话是「用着用着失效的」，不是「打开页面时就没登录」。
   *
   * 两者的表现原来一模一样：都被送去登录页，登录页一句话不说。用户正读着文档被弹走，
   * 只会以为是自己点错了什么。这个标记让登录页能说清原因。
   *
   * 由全局 401 处理器在**真的把用户从页面上弹走**那一刻置位（见 main.ts），不放在
   * expire() 里：expire() 在「已经站在登录页时的 401」也会被调到，那种是陌生访客的探测，
   * 给他一句「登录已过期」是凭空造出一个不存在的会话。
   */
  expiredMidSession: Readonly<Ref<boolean>>
  markExpiredMidSession: () => void
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
  const expiredMidSession = ref(false)
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
              message: '无法核对当前登录状态。',
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
          message: '无法建立登录会话。',
          code: 'session_check_failed',
        })
      )
    }
    // 重新登录成功，上一次「被弹回来」这件事到此为止——不清的话，用户主动退出后再回
    // 登录页还会看到一句莫名其妙的「登录已过期」。
    expiredMidSession.value = false
  }

  async function logout(): Promise<void> {
    try {
      await logoutCurrentUser()
      // 主动退出不是过期。清理放在成功这一支里：退登失败时会话还在，用户可能就地重试，
      // 这时不该动那个标记。
      expiredMidSession.value = false
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

  function markExpiredMidSession(): void {
    expiredMidSession.value = true
  }

  function clear(): void {
    initialization = null
    user.value = null
    error.value = null
    expiredMidSession.value = false
    status.value = 'unknown'
  }

  return {
    status: readonly(status),
    user: readonly(user),
    error: readonly(error),
    expiredMidSession: readonly(expiredMidSession),
    markExpiredMidSession,
    initialize,
    login,
    logout,
    expire,
    clear,
  }
}

export const authSession = createAuthSession()
