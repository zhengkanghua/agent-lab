import { computed, onScopeDispose, ref } from 'vue'
import { useQuery, useMutation, useQueryClient } from '@tanstack/vue-query'
import {
  deleteUser,
  listUsers,
  resetUserPassword,
  revokeUserSessions,
  updateUser,
  type UserAdminDto,
} from '@/api/user-admin'
import { presentAdminError } from '../model/admin-error'
import { validatePassword, type DirectoryLoadState } from '../model/admin-validation'
import { sortUsers, summarizeUsers } from '../model/user-account'
import { userAdminKeys, includeDeletedOf } from '../constants/query-keys'

export interface UseUserDirectoryOptions {
  /**
   * 当前登录账号的 id。取成 getter 而不是 Ref：调用点是
   * `() => authSession.user.value?.id`，本 feature 因此不必 import 另一个 feature。
   */
  currentUserId: () => string | undefined
  /**
   * 当前账号把自己停用或降级之后执行。刷新会话与跳转都归页面：
   * 它们涉及 auth 与 router，而 feature 之间不互相 import、也不 import 布局与页面。
   *
   * 删除自己不在本回调的范围内——那条路在 `deleteAccount` 里就被挡掉了，见那里的说明。
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
  /** 是否连已注销的账号一起看；默认关，对应接口上的 `include_deleted`。 */
  const includeDeleted = ref(false)

  const query = useQuery({
    // 键用 computed：参数进键之后，切换开关就是换一个键、换一份数据，
    // 不会命中 10 秒新鲜期里那份旧缓存。
    queryKey: computed(() => userAdminKeys.users(includeDeleted.value)),
    queryFn: async ({ signal }) => {
      const loadedUsers = await listUsers(signal, { includeDeleted: includeDeleted.value })
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

  function setIncludeDeleted(value: boolean): void {
    includeDeleted.value = value
  }

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
      if (resetUserId.value === updated.id) cancelPasswordReset()
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
        if (resetUserId.value === user.id) resetError.value = message
        else setRowError(user.id, message)
      },
      run: async () => {
        await resetPasswordMutation.mutateAsync({ userId: user.id, password: resetPassword.value })
      },
    })
  }

  const deleteMutation = useMutation({
    mutationFn: (userId: string) => deleteUser(userId),
  })

  /**
   * 注销一个账号。环境托管超级用户与当前登录账号都不允许注销。
   *
   * 当前账号自己不能注销，不是因为后端拦得住（后端挡的是最后一个活跃超管），而是这个页面
   * 会立刻失去意义：注销完自己的会话就没了，接下来要么跳登录页要么跳检索页，让管理员
   * 先处理别人、再让别人来处理自己更顺。要注销自己得换一个账号操作。
   */
  async function deleteAccount(user: UserAdminDto): Promise<void> {
    if (isBusy(user.id)) return
    if (user.is_environment_admin || user.id === options.currentUserId()) return
    if (
      !window.confirm(
        `注销账号 ${user.email}？它将无法再登录，已登录的会话也会失效；` +
          `账号记录、会话归属与个人偏好都会保留，且这是不可恢复的终态。`,
      )
    )
      return

    await runRowAction({
      userId: user.id,
      fallback: '账号注销失败，请稍后重试。',
      run: async () => {
        await deleteMutation.mutateAsync(user.id)
        markDeregistered(user)
        feedback.value = `已注销账号 ${user.email}。`
      },
    })
  }

  /**
   * 把缓存里的这一行改成已注销，而不是把它从列表里摘掉。
   *
   * 摘掉在开关打开时是错的：那一行并没有消失，只是状态变了。后端返回 204 空体、
   * 拿不到它盖的注销时间，所以按刚刚这一刻补一个；缓存 10 秒过期后重新取数会换成
   * 服务端的值。
   */
  function markDeregistered(user: UserAdminDto): void {
    replaceUser({ ...user, is_active: false, deleted_at: new Date().toISOString() })
    // 这一行可能正展开着密码重置表单，收起它：它已经注销，不再有可重置的密码。
    if (resetUserId.value === user.id) cancelPasswordReset()
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
    updateCachedDirectories((users) => sortUsers([...users, created]))
    feedback.value = `已创建账号 ${created.email}。`
  }

  function clearFeedback(): void {
    feedback.value = ''
  }

  function replaceUser(updated: UserAdminDto): void {
    updateCachedDirectories((users, includeDeleted) => {
      // 注销之后这一行仍然存在，只是状态变了：默认口径（不含已注销）下它不该再出现，
      // 而开关打开的那一份里它必须留在原位、显示成「已注销」。
      if (updated.deleted_at !== null && !includeDeleted) {
        return users.filter((user) => user.id !== updated.id)
      }
      return sortUsers(users.map((user) => (user.id === updated.id ? updated : user)))
    })
  }

  /**
   * 把一次改动写进**所有已缓存**的账号列表。
   *
   * 列表最多有两份缓存：默认的（不含已注销）与开关打开时的（含已注销）。写完之后两份都要
   * 各自成立，所以按「这个键有没有带上已注销」分派两种处理，而不是三处各写一遍
   * `setQueryData(userAdminKeys.users())`——键带上参数之后，那种写法会写到一份没人读的缓存上。
   */
  function updateCachedDirectories(
    update: (users: UserAdminDto[], includeDeleted: boolean) => UserAdminDto[],
  ): void {
    for (const cached of queryClient.getQueryCache().findAll({ queryKey: userAdminKeys.all })) {
      const includeDeleted = includeDeletedOf(cached.queryKey)
      queryClient.setQueryData<UserAdminDto[]>(cached.queryKey, (oldData) =>
        oldData === undefined ? oldData : update(oldData, includeDeleted),
      )
    }
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
    includeDeleted,
    setIncludeDeleted,
    load,
    setActive,
    setSuperuser,
    openPasswordReset,
    cancelPasswordReset,
    submitPasswordReset,
    revokeSessions,
    deleteAccount,
    acceptCreatedUser,
    clearFeedback,
    isBusy,
    clearSensitiveInput,
  }
}
