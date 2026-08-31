import { computed, onScopeDispose, ref } from 'vue'
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
 * 账号目录的状态与请求。
 *
 * 收编的是原来散在页面里的三份 try/finally：改状态、重置密码、撤销会话都要
 * 「置忙 → 清错 → 清上一条成功提示 → 发请求 → 失败写错误 → 无论如何解忙」，
 * 顺序一致但各写一遍，漏掉 finally 那一行就会把某一行永久留在禁用态。
 * 现在只有 `runRowAction` 一处实现。
 *
 * 「刚改的是自己」的处置交给调用方：停用或降级自己之后要么被踢到登录页、要么退回检索页，
 * 那是路由与会话的事，不是目录的事。
 */
export function useUserDirectory(options: UseUserDirectoryOptions) {
  const users = ref<UserAdminDto[]>([])
  const loadState = ref<DirectoryLoadState>('loading')
  const loadError = ref('')
  const feedback = ref('')
  const busyUserIds = ref(new Set<string>())
  const rowErrors = ref<Record<string, string>>({})
  const resetUserId = ref<string | null>(null)
  const resetPassword = ref('')
  const resetError = ref('')

  let loadController: AbortController | null = null

  const stats = computed(() => summarizeUsers(users.value))

  /**
   * 读取账号列表。
   *
   * 连点刷新会发多条请求，回来的顺序不保证与发出顺序一致。AbortController 只能拦住还没
   * resolve 的读取，「响应已到、await 还没恢复执行」的窗口内 abort 不起作用，所以另外比
   * controller 身份：不是当前那一个就直接丢弃，不写状态。
   */
  async function load(): Promise<void> {
    loadController?.abort()
    const controller = new AbortController()
    loadController = controller
    loadState.value = 'loading'
    loadError.value = ''
    try {
      const loadedUsers = await listUsers(controller.signal)
      if (controller !== loadController) return
      users.value = sortUsers(loadedUsers)
      loadState.value = 'ready'
    } catch (cause) {
      if (controller.signal.aborted || controller !== loadController) return
      loadError.value = presentAdminError(cause, '暂时无法读取账号列表，请稍后重试。')
      loadState.value = 'error'
    }
  }

  function setActive(user: UserAdminDto, isActive: boolean): Promise<void> {
    return updateAccount(user, { isActive })
  }

  function setSuperuser(user: UserAdminDto, isSuperuser: boolean): Promise<void> {
    return updateAccount(user, { isSuperuser })
  }

  async function updateAccount(
    user: UserAdminDto,
    change: { isActive?: boolean; isSuperuser?: boolean },
  ): Promise<void> {
    if (user.is_environment_admin) return

    await runRowAction({
      userId: user.id,
      fallback: '账号状态更新失败，请稍后重试。',
      run: async () => {
        const updated = await updateUser({ userId: user.id, ...change })
        replaceUser(updated)
        feedback.value = `已更新账号 ${updated.email}。`

        if (
          updated.id === options.currentUserId() &&
          (!updated.is_active || !updated.is_superuser)
        ) {
          await options.onSelfDowngraded()
        }
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
        replaceUser(await resetUserPassword({ userId: user.id, password: resetPassword.value }))
        // 先收起表单再报成功：留着它会让人以为还要再确认一次。
        cancelPasswordReset()
        feedback.value = `已重置 ${user.email} 的密码，并撤销该账号的全部会话。`
      },
    })
  }

  /**
   * 撤销一个账号的全部会话。
   *
   * 二次确认留在这里而不是交给组件：它是这个动作的一部分——撤销会把该账号所有设备上的
   * 登录都踢掉，且不可撤销。放到组件里就变成「某个按钮恰好问了一句」，换个入口调用同一个
   * 方法时会静默少掉这道确认。
   */
  async function revokeSessions(user: UserAdminDto): Promise<void> {
    if (isBusy(user.id)) return
    if (!window.confirm(`撤销 ${user.email} 的全部登录会话？`)) return

    await runRowAction({
      userId: user.id,
      fallback: '会话撤销失败，请稍后重试。',
      run: async () => {
        const result = await revokeUserSessions(user.id)
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
    users.value = sortUsers([...users.value, created])
    feedback.value = `已创建账号 ${created.email}。`
  }

  function clearFeedback(): void {
    feedback.value = ''
  }

  function replaceUser(updated: UserAdminDto): void {
    users.value = sortUsers(users.value.map((user) => (user.id === updated.id ? updated : user)))
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
    loadController?.abort()
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
