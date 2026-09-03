import { computed, onScopeDispose, ref } from 'vue'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import {
  listUsers,
  resetUserPassword,
  revokeUserSessions,
  updateUser,
  type UserAdminDto,
} from '@/api/user-admin'
import { presentAdminError } from '../model/admin-error'
import { validatePassword, type DirectoryLoadState } from '../model/admin-validation'
import { sortUsers, summarizeUsers } from '../model/user-account'
import { userAdminKeys } from '../constants/query-keys'

export interface UseUserDirectoryOptions {
  /**
   * 当前登录账号的 id。取成 getter 而不是 Ref：调用点是
   * `() => authSession.user.value?.id`，本 feature 因此不必 import 另一个 feature。
   */
  currentUserId: () => string | undefined
  /**
   * 当前账号把自己停用或降级之后执行。刷新会话与跳转都归页面：
   * 它们涉及 auth 与 router，而 feature 之间不互相 import、也不 import 布局与页面。
   */
  onSelfDowngraded: () => Promise<void>
}

/** 一次行内操作。错误默认落到该行的错误位，密码重置传自己的 sink。 */
interface RowAction {
  userId: string
  fallback: string
  run: () => Promise<void>
  onFailure?: (message: string) => void
}

/**
 * 账号目录的状态与请求（已使用 Vue Query 重构）。
 */
export function useUserDirectory(options: UseUserDirectoryOptions) {
  const queryClient = useQueryClient()

  const loadErrorOverride = ref('')
  const feedback = ref('')
  const busyUserIds = ref(new Set<string>())
  const rowErrors = ref<Record<string, string>>({})
  const resetUserId = ref<string | null>(null)
  const resetPassword = ref('')
  const resetError = ref('')

  const query = useQuery({
    queryKey: userAdminKeys.users(),
    queryFn: async ({ signal }) => {
      const loadedUsers = await listUsers(signal)
      return sortUsers(loadedUsers)
    },
    staleTime: 10_000,
  })

  const users = computed(() => query.data.value ?? [])

  const loadState = computed<DirectoryLoadState>(() => {
    if (query.isPending.value) return 'loading'
    if (query.isError.value || loadErrorOverride.value) return 'error'
    return 'ready'
  })

  const loadError = computed(() => {
    if (loadErrorOverride.value) return loadErrorOverride.value
    if (query.error.value)
      return presentAdminError(query.error.value, '暂时无法读取账号列表，请稍后重试。')
    return ''
  })

  const stats = computed(() => summarizeUsers(users.value))

  function load(): Promise<void> {
    loadErrorOverride.value = ''
    return query.refetch() as unknown as Promise<void>
  }

  function setActive(user: UserAdminDto, isActive: boolean): Promise<void> {
    return updateAccount(user, { isActive })
  }

  function setSuperuser(user: UserAdminDto, isSuperuser: boolean): Promise<void> {
    return updateAccount(user, { isSuperuser })
  }

  const updateMutation = useMutation({
    mutationFn: ({
      user,
      change,
    }: {
      user: UserAdminDto
      change: { isActive?: boolean; isSuperuser?: boolean }
    }) => updateUser({ userId: user.id, ...change }),
    onSuccess: (updated) => {
      replaceUser(updated)
      feedback.value = `已更新账号 ${updated.email}。`
      if (updated.id === options.currentUserId() && (!updated.is_active || !updated.is_superuser)) {
        options.onSelfDowngraded()
      }
    },
  })

  async function updateAccount(
    user: UserAdminDto,
    change: { isActive?: boolean; isSuperuser?: boolean },
  ): Promise<void> {
    if (user.is_environment_admin) return

    await runRowAction({
      userId: user.id,
      fallback: '账号状态更新失败，请稍后重试。',
      run: async () => {
        await updateMutation.mutateAsync({ user, change })
      },
    })
  }

  function openPasswordReset(user: UserAdminDto): void {
    if (user.is_environment_admin || isBusy(user.id)) return
    // 再点同一行是收起：这一行的按钮既是开也是关。
    resetUserId.value = resetUserId.value === user.id ? null : user.id
    resetPassword.value = ''
    resetError.value = ''
    setRowError(user.id, '')
  }

  function cancelPasswordReset(): void {
    resetUserId.value = null
    resetPassword.value = ''
    resetError.value = ''
  }

  const resetPasswordMutation = useMutation({
    mutationFn: ({ userId, password }: { userId: string; password: string }) =>
      resetUserPassword({ userId, password }),
    onSuccess: (updated) => {
      replaceUser(updated)
      cancelPasswordReset()
      feedback.value = `已重置 ${updated.email} 的密码，并撤销该账号的全部会话。`
    },
  })

  async function submitPasswordReset(user: UserAdminDto): Promise<void> {
    if (isBusy(user.id)) return

    const validation = validatePassword(resetPassword.value)
    if (validation) {
      resetError.value = validation
      return
    }
    resetError.value = ''

    await runRowAction({
      userId: user.id,
      fallback: '密码重置失败，请稍后重试。',
      onFailure: (message) => {
        resetError.value = message
      },
      run: async () => {
        await resetPasswordMutation.mutateAsync({ userId: user.id, password: resetPassword.value })
      },
    })
  }

  /**
   * 撤销一个账号的全部会话。
   */
  const revokeSessionsMutation = useMutation({
    mutationFn: (userId: string) => revokeUserSessions(userId),
  })

  async function revokeSessions(user: UserAdminDto): Promise<void> {
    if (isBusy(user.id)) return
    if (!window.confirm(`撤销 ${user.email} 的全部登录会话？`)) return

    await runRowAction({
      userId: user.id,
      fallback: '会话撤销失败，请稍后重试。',
      run: async () => {
        const result = await revokeSessionsMutation.mutateAsync(user.id)
        feedback.value =
          result.revoked_sessions === 0
            ? `${user.email} 当前没有有效会话。`
            : `已撤销 ${user.email} 的 ${result.revoked_sessions} 个会话。`
      },
    })
  }

  async function runRowAction({ userId, fallback, run, onFailure }: RowAction): Promise<void> {
    if (isBusy(userId)) return

    setBusy(userId, true)
    setRowError(userId, '')
    feedback.value = ''
    try {
      await run()
    } catch (cause) {
      const message = presentAdminError(cause, fallback)
      if (onFailure) onFailure(message)
      else setRowError(userId, message)
    } finally {
      setBusy(userId, false)
    }
  }

  /** 创建成功后把新行并进列表。创建表单自己不碰列表。 */
  function acceptCreatedUser(created: UserAdminDto): void {
    queryClient.setQueryData(userAdminKeys.users(), (oldData: UserAdminDto[] | undefined) => {
      const existing = oldData ?? []
      return sortUsers([...existing, created])
    })
    feedback.value = `已创建账号 ${created.email}。`
  }

  function clearFeedback(): void {
    feedback.value = ''
  }

  function replaceUser(updated: UserAdminDto): void {
    queryClient.setQueryData(userAdminKeys.users(), (oldData: UserAdminDto[] | undefined) => {
      const existing = oldData ?? []
      return sortUsers(existing.map((user) => (user.id === updated.id ? updated : user)))
    })
  }

  function isBusy(userId: string): boolean {
    return busyUserIds.value.has(userId)
  }

  /** 整只替换 Set：原地 add/delete 不会触发依赖这个 ref 的渲染。 */
  function setBusy(userId: string, busy: boolean): void {
    const next = new Set(busyUserIds.value)
    if (busy) next.add(userId)
    else next.delete(userId)
    busyUserIds.value = next
  }

  function setRowError(userId: string, message: string): void {
    rowErrors.value = { ...rowErrors.value, [userId]: message }
  }

  function clearSensitiveInput(): void {
    resetPassword.value = ''
  }

  onScopeDispose(() => {
    clearSensitiveInput()
  })

  return {
    users,
    loadState,
    loadError,
    feedback,
    stats,
    busyUserIds,
    rowErrors,
    resetUserId,
    resetPassword,
    resetError,
    load,
    setActive,
    setSuperuser,
    openPasswordReset,
    cancelPasswordReset,
    submitPasswordReset,
    revokeSessions,
    acceptCreatedUser,
    clearFeedback,
    isBusy,
    clearSensitiveInput,
  }
}
